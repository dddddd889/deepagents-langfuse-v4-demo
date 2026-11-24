"""本地 HTTP skill 服务及标准 MCP PDF 服务，客户端与服务端通过真实网络通信。"""

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
import hashlib
import json
from pathlib import Path
import socket
import threading
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import Context, FastMCP
from starlette.responses import Response
import uvicorn


class PdfEvidenceService:
    """在随机本机端口提供 skill 正文和绑定到单个 PDF 的 MCP 工具。"""

    def __init__(self, pdf_tools, skill_path: Path):
        """绑定已有 PDF 工具；执行日志来自服务端实际收到的 MCP 请求。"""
        self.tools = {item.name: item for item in pdf_tools}
        self.skill_bytes = skill_path.read_bytes()
        self.calls = []
        self.skill_requests = 0
        self.server = None
        self.thread = None
        self.socket = None
        self.url = None

    def __enter__(self):
        """启动标准 Streamable HTTP MCP 服务，仅监听 127.0.0.1。"""
        mcp = FastMCP("pdf-tools-local", stateless_http=True, json_response=True, log_level="ERROR")

        @mcp.custom_route("/skills/pdf-review/SKILL.md", methods=["GET"])
        async def skill(request):
            """通过 HTTP 返回版本固定的 skill，保留真实请求次数用于核验。"""
            self.skill_requests += 1
            return Response(self.skill_bytes, media_type="text/markdown", headers={
                "ETag": hashlib.sha256(self.skill_bytes).hexdigest(), "X-Skill-Version": "1.0.0",
            })

        def execute(name, arguments, ctx):
            """执行服务端 PDF 工具，并把实际 MCP 请求编号附在业务结果中。"""
            result = json.loads(self.tools[name].invoke(arguments))
            result["_mcp"] = {"server": "pdf-tools-local", "request_id": str(ctx.request_id), "isError": False}
            self.calls.append({"name": name, "arguments": arguments, "request_id": str(ctx.request_id)})
            return json.dumps(result, ensure_ascii=False)

        @mcp.tool()
        def get_pdf_info(ctx: Context) -> str:
            """获取当前 PDF 的文件名、标题和总页数；阅读页面之前先调用。"""
            return execute("get_pdf_info", {}, ctx)

        @mcp.tool()
        def read_pdf_page(page_number: int, ctx: Context, offset: int = 0) -> str:
            """读取指定页文字；offset 只取 0 或上次返回的 next_offset，不支持 limit。"""
            return execute("read_pdf_page", {"page_number": page_number, "offset": offset}, ctx)

        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(128)
        port = self.socket.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), log_level="error", access_log=False))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                self.__exit__(None, None, None)
                raise RuntimeError("本地 skill/MCP 服务启动失败")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc):
        """结束本次临时服务，保留本地报告和 Opik 数据。"""
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=10)
        if self.socket:
            self.socket.close()


class McpPdfClient:
    """用官方 MCP SDK 维护单个异步会话，供同步 Agent 工具安全调用。"""

    def __init__(self, url):
        """为本次 PDF 服务建立独立客户端，不继承机器上的 HTTP 代理。"""
        self.url = url
        self.ready = threading.Event()
        self.error = None
        self.loop = None
        self.stop = None
        self.session = None
        self.initialization = None

    async def _run(self):
        """所有 MCP 操作在同一事件循环执行，避免并行工具跨事件循环使用会话。"""
        self.loop = asyncio.get_running_loop()
        self.stop = asyncio.Event()
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=60) as http_client:
                async with streamable_http_client(self.url, http_client=http_client) as (read, write, _):
                    async with ClientSession(read, write) as self.session:
                        self.initialization = await self.session.initialize()
                        self.ready.set()
                        await self.stop.wait()
        except BaseException as error:
            self.error = error
            self.ready.set()

    def _start(self):
        """在线程中启动会话的事件循环。"""
        asyncio.run(self._run())

    def __enter__(self):
        """等待 initialize 完成后才允许发现和调用工具。"""
        self.thread = threading.Thread(target=self._start, daemon=True)
        self.thread.start()
        if not self.ready.wait(timeout=30):
            self.__exit__(None, None, None)
            raise RuntimeError("MCP initialize 超时")
        if self.error:
            raise RuntimeError("MCP initialize 失败") from self.error
        return self

    def submit(self, coroutine):
        """在会话循环执行一次协议请求；超时取消请求，防止遗留后台调用。"""
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout=60)
        except FutureTimeoutError:
            future.cancel()
            raise RuntimeError("MCP 请求超过 60 秒") from None

    def discover(self):
        """读取全部 tools/list 分页，保留服务端公开的参数格式和描述。"""
        tools, cursor = [], None
        while True:
            page = self.submit(self.session.list_tools(cursor=cursor))
            tools.extend(item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in page.tools)
            cursor = page.nextCursor
            if not cursor:
                return tools

    def call(self, name, arguments):
        """通过 tools/call 执行工具；isError=true 直接视为失败。"""
        result = self.submit(self.session.call_tool(name, arguments))
        text = "\n".join(item.text for item in result.content if item.type == "text")
        if result.isError:
            raise RuntimeError(f"MCP 工具 {name} 失败：{text[:300]}")
        return text

    def __exit__(self, *exc):
        """先结束会话再关闭临时 HTTP 服务。"""
        if self.loop and self.stop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.stop.set)
        if hasattr(self, "thread"):
            self.thread.join(timeout=10)


def adapt_mcp_tools(client, catalog_id):
    """按实际发现的 JSON Schema 创建 LangChain 工具，在同一个工具 span 记录 MCP 信息。"""
    from langchain_core.tools import StructuredTool

    def create(item):
        """为每个工具绑定自己的名称，避免闭包把所有调用送到最后一个工具。"""
        name = item["name"]

        def invoke(**arguments):
            """实际调用远端 MCP，不在客户端直接执行 PDF 读取函数。"""
            return client.call(name, arguments)

        return StructuredTool.from_function(
            invoke, name=name, description=item.get("description", name), args_schema=item["inputSchema"],
            metadata={"tool_origin": "mcp", "mcp_server": "pdf-tools-local", "mcp_method": "tools/call",
                      "mcp_transport": "streamable_http", "mcp_catalog_id": catalog_id, "mcp_endpoint": client.url},
        )

    return [create(item) for item in client.catalog]
