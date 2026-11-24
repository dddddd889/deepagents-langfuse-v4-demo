"""主 Agent 委派 PDF 阅读子 Agent，再汇总分析并记录完整 Langfuse 调用链。"""

import argparse
import os
from pathlib import Path

from demo_common import create_runtime, get_public_trace_url
from pdf_tools import build_pdf_tools
from pdf_media import prepare_pdf_attachment, verify_pdf_attachment
from pdf_dataset import PDF_CANDIDATE_DATASET, collect_pdf_candidate

from langchain_core.messages import AIMessage, ToolMessage
from langfuse import propagate_attributes
from langfuse.langchain import CallbackHandler
from pypdf.errors import PdfReadError

from pdf_agent import PDF_READER_NAME, build_pdf_agent, get_read_coverage
DEFAULT_REPORT_PATH = Path(__file__).resolve().parent / "output/pdf/analysis.md"


def build_argument_parser() -> argparse.ArgumentParser:
    """定义运行参数；只有显式指定 --collect-dataset 才开启自动采集。"""
    parser = argparse.ArgumentParser(
        description="主 Agent 委派 PDF 阅读子 Agent，汇总分析并追踪完整调用链。"
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        default=os.getenv("PDF_FILE_PATH") or None,
        help="你提供的本地 PDF 路径；也可使用 PDF_FILE_PATH 配置",
    )
    parser.add_argument(
        "--question",
        default="请通读 PDF，用中文概述主题、主要观点和关键数据，说明局限或风险并给出结论，每项标注来源页码；未提供的信息请明确说明。",
        help="对 PDF 提出的问题",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="Markdown 分析报告保存位置",
    )
    parser.add_argument(
        "--collect-dataset",
        action="store_true",
        help="分析成功后采集到 pdf-qa-candidates；相同文件内容和问题只保留已有记录",
    )
    return parser


def main() -> None:
    """真实执行子代理分析，按显式开关收集成功案例并输出追踪链接。"""
    parser = build_argument_parser()
    args = parser.parse_args()
    if not args.pdf:
        parser.error("请用 --pdf 或 PDF_FILE_PATH 指定你提供的 PDF 文件")
    pdf_path = Path(args.pdf).expanduser().resolve()
    report_path = args.output.expanduser().resolve()
    # 路径属于当前运行环境；Docker 中显示的是挂载后的容器路径。
    file_metadata = {
        "filename": pdf_path.name,
        "pdf_path": str(pdf_path),
        "report_path": str(report_path),
        "pdf_storage": "langfuse_media",
        "pdf_upload_status": "pending",
        "collect_dataset": args.collect_dataset,
        "dataset_collection_status": "pending" if args.collect_dataset else "disabled",
    }
    # 只有 PDF 工具执行成功后才填入审计结果，不能用模型文字冒充页面读取。
    read_events = []
    try:
        tools = build_pdf_tools(pdf_path, read_events)
        attachment = prepare_pdf_attachment(pdf_path)
    except (OSError, ValueError, PdfReadError) as error:
        parser.error(str(error))
    model, langfuse = create_runtime()
    agent = build_pdf_agent(model, tools)
    try:
        with propagate_attributes(
            trace_name="deepagents-pdf-subagent-demo",
            user_id=os.getenv("DEMO_USER_ID", "user-demo-001"),
            session_id=os.getenv("PDF_SESSION_ID", "pdf-demo-session-001"),
            tags=["deepagents", "pdf-demo", "subagent-demo"],
        ):
            # 最外层仅表示一次工作流，主、子代理分别由框架回调记录，避免重复包裹。
            with langfuse.start_as_current_observation(
                as_type="span",
                name="pdf-analysis-workflow",
                input={
                    "question": args.question,
                    "filename": pdf_path.name,
                    "pdf_path": str(pdf_path),
                    "pdf": attachment.media,
                },
                metadata=file_metadata,
            ) as observation:
                try:
                    file_metadata.update(
                        verify_pdf_attachment(
                            langfuse, attachment, observation.trace_id, observation.id
                        )
                    )
                except Exception:
                    observation.update(
                        metadata={**file_metadata, "pdf_upload_status": "failed"},
                        level="ERROR",
                        status_message="PDF 附件上传或下载校验失败",
                    )
                    raise
                observation.update(metadata=file_metadata)
                print("PDF 附件已上传：", file_metadata["pdf_media_id"], flush=True)
                print("对象存储路径：", file_metadata["pdf_object_path"], flush=True)
                print(
                    "查看追踪：",
                    get_public_trace_url(langfuse, observation.trace_id),
                    flush=True,
                )
                result = agent.invoke(
                    {"messages": [{"role": "user", "content": args.question}]},
                    config={
                        "callbacks": [CallbackHandler()],
                        "run_name": "pdf-main-agent",
                        "recursion_limit": 60,
                    },
                )
                # 检查真实 task 请求和对应返回，避免主 Agent 自己分析却声称调用了子代理。
                calls = [
                    call
                    for message in result["messages"]
                    if isinstance(message, AIMessage)
                    for call in message.tool_calls
                    if call["name"] == "task"
                    and call["args"].get("subagent_type") == PDF_READER_NAME
                ]
                call_ids = {call["id"] for call in calls}
                task_results = [
                    message
                    for message in result["messages"]
                    if isinstance(message, ToolMessage)
                    and message.tool_call_id in call_ids
                ]
                if not calls or not task_results:
                    raise RuntimeError(
                        "本轮主 Agent 没有完成 task 到 PDF 阅读子 Agent 的实际委派"
                    )
                coverage = get_read_coverage(read_events)
                if coverage["incomplete_pages"]:
                    raise RuntimeError(
                        f"子 Agent 未完整读取页面：{coverage['incomplete_pages']}"
                    )
                if not coverage["read_pages"]:
                    raise RuntimeError("没有可分析的 PDF 文字，扫描件请先进行 OCR。")
                answer = result["messages"][-1].content
                result_metadata = {
                    **file_metadata,
                    **coverage,
                    "reader_agent": PDF_READER_NAME,
                }
                observation.update(output=answer, metadata=result_metadata)
                trace_url = get_public_trace_url(langfuse, observation.trace_id)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    f"# PDF 分析报告\n\n- 文件：{pdf_path.name}\n- 已完整读取页码：{coverage['read_pages']}\n- 需要 OCR 页码：{coverage['ocr_pages']}\n- 阅读子代理：{PDF_READER_NAME}\n- 追踪：{trace_url or observation.trace_id}\n\n{answer}\n",
                    encoding="utf-8",
                )
                if args.collect_dataset:
                    # 先完成真实委派、页码检查和报告落盘，再收集案例。
                    with langfuse.start_as_current_observation(
                        as_type="span",
                        name="pdf-dataset-collection",
                        input={
                            "dataset_name": PDF_CANDIDATE_DATASET,
                            "pdf_sha256": attachment.sha256_hex,
                            "question": args.question,
                        },
                    ) as collection_observation:
                        try:
                            collection = collect_pdf_candidate(
                                langfuse,
                                enabled=True,
                                attachment=attachment,
                                filename=pdf_path.name,
                                question=args.question,
                                answer=answer,
                                trace_id=observation.trace_id,
                                observation_id=observation.id,
                            )
                        except Exception:
                            result_metadata["dataset_collection_status"] = "failed"
                            observation.update(
                                metadata=result_metadata,
                                level="ERROR",
                                status_message="分析报告已保存，但数据集采集失败",
                            )
                            raise
                        collection_observation.update(output=collection)
                    result_metadata.update(collection)
                    observation.update(metadata=result_metadata)
                    print("数据集采集：", collection["dataset_collection_status"])
                    print("数据集条目：", collection["dataset_item_id"])
                    if trace_url:
                        dataset_url = (
                            trace_url.rsplit("/traces/", 1)[0]
                            + f"/datasets/{collection['dataset_id']}/items"
                        )
                        print("查看数据集：", dataset_url)
                print("分析文件：", pdf_path)
                print(
                    "协作流程：pdf-main-agent -> task -> pdf-reader-agent -> PDF 工具 -> 主 Agent 汇总"
                )
                print("已完整读取页码：", coverage["read_pages"])
                print("分析结果：\n", answer)
                print("报告位置：", report_path)
                print("Trace ID：", observation.trace_id)
                if trace_url:
                    print("查看追踪：", trace_url)
    finally:
        # 所有子代理沿用同一个回调，退出前等待主、子代理和工具记录发送完成。
        langfuse.flush()


if __name__ == "__main__":
    main()
