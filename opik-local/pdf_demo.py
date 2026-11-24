"""真实运行 PDF 主子 Agent，将原文附件、逐页工具和分析报告记录到 Opik。"""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from smoke import ensure_project, read_local_config
from session_evaluation import check_model

# 只复用当前仓库的纯 Agent 和 PDF 工具模块，不导入 Langfuse 配置或凭据。
REPOSITORY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_DIR))

from pdf_agent import PDF_READER_NAME, build_pdf_agent, get_read_coverage
from pdf_tools import build_pdf_tools
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, ToolMessage


class PdfProgress(BaseCallbackHandler):
    """显示真实工具调用进度，不在终端重复打印整页文字。"""

    def on_tool_start(self, serialized, input_str, **kwargs):
        """只打印委派、文档信息和页面读取这三种业务工具的入参。"""
        name = (serialized or {}).get("name", "")
        if name in {"task", "get_pdf_info", "read_pdf_page"}:
            print(f"工具 {name}：{input_str}", flush=True)


def parse_args():
    """默认使用仓库已有的 11 页论文，也允许显式指定其他本地 PDF。"""
    config = read_local_config()
    port = os.environ.get("OPIK_PORT", config.get("OPIK_PORT", "5174"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=REPOSITORY_DIR / "2507.21504v1.pdf")
    parser.add_argument("--question", default="请读取 PDF 全部页面，用中文约 800–1200 字概述主题、主要观点、评估框架、关键数据和局限，标注来源页码。参考文献页只需确认其用途，无需逐条摘录；无法从文字确认的图表明确说明，不必反复重读。")
    parser.add_argument("--output", type=Path, help="报告路径；默认按本次运行编号保存，避免覆盖之前的报告")
    parser.add_argument("--project", help="默认按普通 PDF 或完整证据样例选择独立项目")
    parser.add_argument("--model", default=os.environ.get("OPIK_DEMO_MODEL", "custom-llm/TAMS/bailian/qwen3.8-max"))
    parser.add_argument("--base-url", default=f"http://127.0.0.1:{port}/api")
    parser.add_argument("--full-evidence", action="store_true", help="实际加载 HTTP skill、压缩测试历史、经 MCP 阅读，再执行只读取证评估并写回 Opik")
    parser.add_argument("--no-evaluate", action="store_true", help="完整证据模式仅运行 Agent，之后用 --evaluate-trace 单独取证评分")
    parser.add_argument("--evaluate-trace", help="只评已有完整证据轨迹，不重新调用 PDF Agent")
    parser.add_argument("--evaluation-timeout", type=int, default=300)
    return parser.parse_args()


def run_analysis(args):
    """调用真实模型完成委派和全文读取，仅在实际覆盖检查通过后保存报告。"""
    args.project = args.project or ("deepagents-opik-pdf-evidence-demo" if args.full_evidence else "deepagents-opik-pdf-demo")
    base_url = args.base_url.rstrip("/")
    # 只重评入口也需要在导入 Opik SDK 前指向本机，避免落到默认云端项目。
    os.environ.update({"OPIK_URL_OVERRIDE": base_url, "OPIK_PROJECT_NAME": args.project,
                       "OPIK_WORKSPACE": "default", "OPIK_API_KEY": "opik-local",
                       "OPIK_ANALYTICS_ENABLE": "false"})
    opener = build_opener(ProxyHandler({}))
    check_model(opener, base_url, args.model)
    project = ensure_project(opener, base_url, args.project, "PDF 主 Agent 委派阅读子 Agent，记录全文读取证据和页码引用")
    project_url = base_url.removesuffix("/api") + "/default/projects/" + project["id"]
    if args.evaluate_trace:
        from full_evaluation import append_evaluation_report, evaluate_full_trace
        evaluation = evaluate_full_trace(opener, base_url, project["id"], args.evaluate_trace, args.model,
                                        args.evaluation_timeout, project_name=args.project)
        if args.output:
            append_evaluation_report(args.output.expanduser().resolve(), evaluation, project_url)
        return evaluation
    pdf_path = args.pdf.expanduser().resolve()
    read_events = []  # 来自工具实际执行的逐页读取记录，供覆盖检查使用。
    tools = build_pdf_tools(pdf_path, read_events)
    pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    run_id = uuid4().hex[:10]
    thread_id = f"pdf-{run_id}"
    report_path = (args.output or REPOSITORY_DIR / "output/opik-pdf" / f"analysis-{run_id}.md").expanduser().resolve()
    os.environ.update({"OPIK_URL_OVERRIDE": base_url, "OPIK_PROJECT_NAME": args.project,
                       "OPIK_WORKSPACE": "default", "OPIK_API_KEY": "opik-local",
                       "OPIK_ANALYTICS_ENABLE": "false"})
    import opik
    from opik import Attachment, opik_context
    from opik.integrations.langchain import OpikTracer
    from langchain_openai import ChatOpenAI
    from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware

    model = ChatOpenAI(
        model=args.model, base_url=base_url + "/v1/private", api_key="opik-local",
        temperature=0, timeout=180, max_retries=0,
        streaming=True, stream_usage=True, max_tokens=4000,
        extra_body={"custom_parameters": {"enable_thinking": False}},
    )
    # 超限直接报错，不把尚未完成的读取或模型的兜底回复当成成功报告。
    agent = build_pdf_agent(model, tools, reader_middleware=[
        ModelCallLimitMiddleware(run_limit=20, exit_behavior="error"),
        ToolCallLimitMiddleware(tool_name="read_pdf_page", run_limit=50, exit_behavior="error"),
    ])
    resources = ExitStack()
    store = None
    runtime_metadata = {"pdf_sha256": pdf_hash, "filename": pdf_path.name, "reader_agent": PDF_READER_NAME,
                        "full_evidence": args.full_evidence}
    if args.full_evidence:
        from evidence_runtime import EvidenceStore
        store = EvidenceStore(REPOSITORY_DIR / "output/opik-evidence" / run_id)
        runtime_metadata["history_source"] = "demo_fixture"

    @opik.track(name="pdf-analysis-workflow", project_name=args.project, capture_input=False, capture_output=False)
    def analyze():
        """完整调用链挂在本次根轨迹下，PDF 原文作为附件单独保存。"""
        opik_context.update_current_trace(
            input={"question": args.question, "filename": pdf_path.name},
            thread_id=thread_id, tags=["deepagents", "pdf-demo", "subagent-demo"],
            metadata=runtime_metadata,
            attachments=[Attachment(data=str(pdf_path), content_type="application/pdf")],
        )
        trace_id = opik_context.get_current_trace_data().id
        trace_url = base_url.removesuffix("/api") + "/default/projects/" + project["id"] + "/logs?" + urlencode(
            {"logsType": "traces", "time_range": "past30days", "trace": trace_id}
        )
        print("文件：", pdf_path, flush=True)
        print("查看追踪：", trace_url, flush=True)
        active_agent = agent
        messages = [{"role": "user", "content": args.question}]
        tracer = OpikTracer(project_name=args.project, thread_id=thread_id, tags=["pdf-demo", "subagent-demo"])
        service = None
        if store:
            from evidence_runtime import EvidenceTracer, load_http_skill, connect_mcp, compact_demo_context
            from mcp_pdf import PdfEvidenceService, McpPdfClient, adapt_mcp_tools
            skill_path = Path(__file__).resolve().parent / "skills/pdf-review/SKILL.md"
            service = resources.enter_context(PdfEvidenceService(tools, skill_path))
            client = McpPdfClient(service.url + "/mcp")
            # 初始化 span 内打开客户端；预先注册清理，发生异常也会关闭连接。
            resources.callback(client.__exit__, None, None, None)
            skill_content = load_http_skill(service.url + "/skills/pdf-review/SKILL.md", store, args.project)
            connect_mcp(client, store, args.project)
            messages = compact_demo_context(model, store, args.project, thread_id, args.question)
            active_agent = build_pdf_agent(model, adapt_mcp_tools(client, store.catalog["snapshot_id"]),
                skill_instructions=skill_content, reader_middleware=[
                    ModelCallLimitMiddleware(run_limit=20, exit_behavior="error"),
                    ToolCallLimitMiddleware(tool_name="read_pdf_page", run_limit=50, exit_behavior="error"),
                ])
            runtime_metadata.update({"skill_snapshot_id": store.skill["snapshot_id"],
                "mcp_catalog_id": store.catalog["snapshot_id"], "compaction_snapshot_id": store.compaction["snapshot_id"]})
            tracer = EvidenceTracer(store, "pdf_agent", project_name=args.project, thread_id=thread_id,
                                    tags=["pdf-demo", "full-evidence"])
        try:
            result = active_agent.invoke(
                {"messages": messages},
                config={"callbacks": [tracer, PdfProgress()], "run_name": "pdf-main-agent", "recursion_limit": 80},
            )
        finally:
            # 失败也保存已经发生的读取覆盖，便于在平台区分解析失败和模型汇总失败。
            if read_events:
                opik_context.update_current_trace(metadata={**runtime_metadata, **get_read_coverage(read_events)})
        # 必须存在实际 task 请求和对应工具返回，不能仅凭模型宣称已委派。
        task_ids = {
            call["id"] for message in result["messages"] if isinstance(message, AIMessage)
            for call in message.tool_calls
            if call["name"] == "task" and call["args"].get("subagent_type") == PDF_READER_NAME
        }
        if not task_ids or not any(isinstance(message, ToolMessage) and message.tool_call_id in task_ids for message in result["messages"]):
            raise RuntimeError("主 Agent 没有完成向 PDF 阅读子 Agent 的实际委派")
        coverage = get_read_coverage(read_events)
        opik_context.update_current_trace(metadata={**runtime_metadata, **coverage})
        if coverage["incomplete_pages"] or not coverage["read_pages"]:
            raise RuntimeError(f"PDF 阅读未完成：{coverage}")
        answer = result["messages"][-1].content
        evidence = None
        if store:
            evidence = {"history_source": "demo_fixture", "skill_http_requests": service.skill_requests,
                "skill_snapshot_id": store.skill["snapshot_id"], "mcp_catalog_id": store.catalog["snapshot_id"],
                "compaction_snapshot_id": store.compaction["snapshot_id"], "server_calls": service.calls,
                "model_snapshots": store.model_snapshots, "snapshots": list(store.records.values())}
            index = store.save("evidence-index", evidence)
            opik_context.update_current_trace(attachments=[store.attachment(index)],
                metadata={**runtime_metadata, **coverage, "evidence_index_id": index["snapshot_id"]})
        opik_context.update_current_trace(output={"answer": answer, "read_coverage": coverage,
                                                  **({"runtime_evidence": evidence} if evidence else {})})
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            f"# PDF 分析报告\n\n- 文件：{pdf_path.name}\n- SHA256：{pdf_hash}\n"
            f"- 完整读取页码：{coverage['read_pages']}\n- 需要 OCR 页码：{coverage['ocr_pages']}\n"
            f"- 追踪：{trace_url}\n- 读取方式：提取各页文字层，不包含图表视觉理解或 OCR。\n\n{answer}\n", encoding="utf-8",
        )
        print("\n实际阅读覆盖：", json.dumps(coverage, ensure_ascii=False), flush=True)
        print("分析报告：", report_path, flush=True)
        print("查看追踪：", trace_url, flush=True)
        return {"trace_id": trace_id, "trace_url": trace_url, "report_path": report_path}

    try:
        result = analyze(opik_args={"trace": {"thread_id": thread_id, "project_name": args.project}})
    finally:
        resources.close()
        opik.flush_tracker()
    if args.full_evidence:
        from full_evaluation import append_evaluation_report, evaluate_full_trace
        if args.no_evaluate:
            print(f"追踪已完成；运行 evidence-demo --evaluate-trace {result['trace_id']} 可单独执行取证评分。", flush=True)
        else:
            evaluation = evaluate_full_trace(opener, base_url, project["id"], result["trace_id"], args.model,
                                            args.evaluation_timeout, project_name=args.project)
            append_evaluation_report(report_path, evaluation, project_url)
            print("评估及证据报告：", report_path, flush=True)
    return result


if __name__ == "__main__":
    try:
        run_analysis(parse_args())
    except HTTPError as error:
        print(f"运行失败：Opik HTTP {error.code}，请检查服务及模型网关。", file=sys.stderr)
        sys.exit(1)
    except (OSError, URLError, RuntimeError, ValueError) as error:
        print(f"运行失败：{error}", file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        # 模型客户端可能包含网关返回正文；终端仅输出类型和状态，详细错误由追踪保存。
        status = getattr(getattr(error, "response", None), "status_code", None)
        print(f"运行失败：{type(error).__name__}" + (f"，HTTP {status}" if status else "") + "，请查看本次 Opik 追踪。", file=sys.stderr)
        sys.exit(1)
