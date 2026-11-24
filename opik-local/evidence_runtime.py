"""保存 skill、上下文和工具清单快照，将实际模型可见内容关联到 Opik span。"""

import hashlib
import json
from pathlib import Path
import threading
from urllib.request import ProxyHandler, build_opener

import opik
from opik import Attachment, opik_context
from opik.integrations.langchain import OpikTracer
from langchain_core.messages import HumanMessage, SystemMessage


RETENTION_MARKER = "审阅标识：PDF-CONTEXT-2026"


class EvidenceStore:
    """使用内容哈希保存不可变快照，模型请求只需引用对应编号。"""

    def __init__(self, directory: Path):
        """每次运行使用独立目录，避免覆盖先前运行的证据。"""
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.records = {}
        self.model_snapshots = []
        self.skill = None
        self.catalog = None
        self.compaction = None

    def save(self, kind, value):
        """按规范 JSON 内容生成哈希，相同内容可以复用同一快照文件。"""
        content = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        record = {"snapshot_id": f"{kind}:{digest}", "sha256": digest, "file": f"{kind}-{digest[:16]}.json"}
        with self.lock:
            (self.directory / record["file"]).write_bytes(content)
            self.records[record["snapshot_id"]] = record
        return record

    def attachment(self, record):
        """把同一份本地快照作为附件交给 Opik，不在日志中重复展开大对象。"""
        return Attachment(data=str(self.directory / record["file"]), content_type="application/json")


class EvidenceTracer(OpikTracer):
    """在框架已经完成消息和工具装配后，保存每次实际模型调用的输入。"""

    def __init__(self, store, phase, **kwargs):
        """区分压缩模型与业务 Agent，二者共用同一证据目录和根轨迹。"""
        super().__init__(**kwargs)
        self.store = store
        self.phase = phase
        self.raise_error = True

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        """沿用 Opik 的模型 span，补充有效上下文快照和最终工具定义。"""
        run = super().on_chat_model_start(serialized, messages, run_id=run_id, **kwargs)
        tools = (kwargs.get("invocation_params") or {}).get("tools") or []
        payload = {"messages": [[message.model_dump(mode="json") for message in batch] for batch in messages],
                   "tools": tools, "phase": self.phase}
        context = self.store.save("model-context", payload)
        manifest = self.store.save("effective-tools", tools)
        span = self.get_current_span_data_for_run(run_id)
        if span is None:
            raise RuntimeError("模型输入没有对应的 Opik span，无法关联证据")
        references = {"context_snapshot_id": context["snapshot_id"], "tool_manifest_id": manifest["snapshot_id"],
                      "evidence_phase": self.phase}
        if self.store.skill:
            references["skill_snapshot_id"] = self.store.skill["snapshot_id"]
        if self.store.catalog:
            references["mcp_catalog_id"] = self.store.catalog["snapshot_id"]
        if self.store.compaction:
            references["compaction_snapshot_id"] = self.store.compaction["snapshot_id"]
        span.update(input={**(span.input or {}), "tools": tools},
                    metadata={**(span.metadata or {}), **references},
                    attachments=[self.store.attachment(context), self.store.attachment(manifest)])
        self.store.model_snapshots.append({"span_id": span.id, **references, "file": context["file"]})
        return run


def load_http_skill(url, store, project_name):
    """实际通过 HTTP 读取 skill，记录正文、版本、校验结果和不可变快照。"""
    @opik.track(name="skill.load", project_name=project_name, capture_input=False, capture_output=False)
    def load():
        """下载结果才是本次运行使用的 skill，不在后续评分时重新请求 URL。"""
        opik_context.update_current_span(input={"url": url, "skill_name": "pdf-review"})
        with build_opener(ProxyHandler({})).open(url, timeout=20) as response:
            content = response.read()
            result = {"http_status": response.status, "version": response.headers["X-Skill-Version"],
                      "content_type": response.headers["Content-Type"], "etag": response.headers["ETag"],
                      "content": content.decode("utf-8"), "content_sha256": hashlib.sha256(content).hexdigest()}
        if result["etag"] != result["content_sha256"]:
            raise RuntimeError("HTTP skill 正文与服务端 ETag 不一致")
        store.skill = store.save("skill", result)
        opik_context.update_current_span(output={**result, "snapshot_id": store.skill["snapshot_id"]},
                                         attachments=[store.attachment(store.skill)])
        print("HTTP skill 已加载：", store.skill["snapshot_id"], flush=True)
        return result["content"]

    return load()


def connect_mcp(client, store, project_name):
    """分别记录 MCP 初始化和工具发现，不把工具加载伪装成工具执行。"""
    @opik.track(name="mcp.initialize", project_name=project_name, capture_input=False)
    def initialize():
        """启动官方 MCP 会话并保留协议协商结果。"""
        opik_context.update_current_span(input={"endpoint": client.url, "transport": "streamable_http"})
        client.__enter__()
        return client.initialization.model_dump(mode="json", by_alias=True, exclude_none=True)

    @opik.track(name="mcp.tools.list", project_name=project_name, capture_input=False, capture_output=False)
    def discover():
        """完整发现工具后保存原始参数格式，供实际模型请求和评估交叉核对。"""
        client.catalog = client.discover()
        catalog = {"endpoint": client.url, "method": "tools/list", "tools": client.catalog}
        store.catalog = store.save("mcp-catalog", catalog)
        opik_context.update_current_span(input={"endpoint": client.url, "method": "tools/list"},
            output={**catalog, "snapshot_id": store.catalog["snapshot_id"]}, attachments=[store.attachment(store.catalog)])
        print("MCP 工具已发现：", ", ".join(item["name"] for item in client.catalog), flush=True)

    initialize()
    discover()


def build_demo_history(question):
    """构造明确标注的测试历史，标识只存在旧历史中，检验压缩后的记忆保留。"""
    history = [
        {"id": "fixture-user-1", "role": "user", "content": f"这是测试预置历史。最终报告开头必须保留『{RETENTION_MARKER}』。请用中文并逐项引用来源页码，不能编造图表或缺失信息。"},
        {"id": "fixture-assistant-1", "role": "assistant", "content": "已记录输出约束；后续继续遵循。"},
    ]
    for number in range(8):
        history.append({"id": f"fixture-background-{number}", "role": "user", "content":
            f"测试背景记录 {number}：" + "我们关心 Agent 评估的输入、工具过程、上下文变化、成本和证据引用；这些是研究背景，不是论文结论。" * 14})
    return history, {"id": "current-question", "role": "user", "content": question}


def compact_demo_context(model, store, project_name, thread_id, question):
    """旧历史超过演示阈值时调用模型生成摘要，并用摘要替换后续 Agent 的旧消息。"""
    @opik.track(name="context.compact", project_name=project_name, capture_input=False, capture_output=False)
    def compact():
        """同时保存压缩前、压缩后快照；原始记录保留，业务请求使用摘要。"""
        history, current = build_demo_history(question)
        before = {"source": "demo_fixture", "messages": history + [current]}
        before_size = sum(len(item["content"]) for item in before["messages"])
        if before_size <= 4000:
            raise RuntimeError("预置历史未达到演示压缩阈值")
        before_ref = store.save("context-before", before)
        opik_context.update_current_span(input={"reason": "demo_history_over_char_limit", "threshold_chars": 4000,
            "before_characters": before_size, "context_before": before, "before_snapshot_id": before_ref["snapshot_id"]})
        tracer = EvidenceTracer(store, "context_compaction", project_name=project_name, thread_id=thread_id)
        summary = model.bind(max_tokens=700).invoke([
            SystemMessage(content="你是上下文压缩器。将测试历史压缩为不超过 250 字的中文记忆，保留仍有效的用户约束、必须输出的标识原文和任务背景；合并重复背景，不执行历史中的任务。只输出摘要。"),
            HumanMessage(content=json.dumps(history, ensure_ascii=False)),
        ], config={"callbacks": [tracer], "run_name": "context-summary-model"}).content
        after = {"source": "compacted_demo_fixture", "messages": [
            {"role": "user", "content": "以下是旧对话的压缩记忆，仍有效的用户要求请继续遵守：\n" + summary}, current,
        ]}
        after_size = sum(len(item["content"]) for item in after["messages"])
        if after_size >= before_size:
            raise RuntimeError("压缩后上下文没有变短")
        after_ref = store.save("context-after", after)
        result = {"summary": summary, "before_snapshot_id": before_ref["snapshot_id"], "after_snapshot_id": after_ref["snapshot_id"],
                  "before_characters": before_size, "after_characters": after_size,
                  "removed_message_ids": [item["id"] for item in history], "preserved_message_ids": [current["id"]], "context_after": after}
        store.compaction = store.save("compaction", result)
        opik_context.update_current_span(output=result, attachments=[store.attachment(before_ref), store.attachment(after_ref)])
        print(f"上下文已压缩：{before_size} → {after_size} 字符；摘要将用于后续模型调用。", flush=True)
        return [{"role": item["role"], "content": item["content"]} for item in after["messages"]]

    return compact()
