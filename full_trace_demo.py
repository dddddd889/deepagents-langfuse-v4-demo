"""显式运行完整追踪样例；导入本文件或查看帮助不会请求真实模型。"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

# 此导入先加载项目 .env，必须在 Langfuse 导入前执行。
from demo_common import create_runtime, get_public_trace_url  # isort: skip

from langfuse import propagate_attributes
from langfuse.langchain import CallbackHandler

from full_trace_agent import run_workflow


async def main() -> None:
    """运行模型、保存评估报告，并等待追踪及 Scores 发送完毕。"""
    parser = argparse.ArgumentParser(
        description="运行压缩、Skill、MCP 和规则评估完整示例（会调用真实模型）"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/full-trace"),
        help="评估报告保存目录",
    )
    args = parser.parse_args()
    model, langfuse = create_runtime()
    session_id = f"full-trace-{uuid4().hex}"
    # 在调用协程内执行回调，使手动 MCP span 能继承当前工具的追踪上下文。
    handler = CallbackHandler()
    handler.run_inline = True
    try:
        with propagate_attributes(
            trace_name="deepagents-full-trace-demo",
            session_id=session_id,
            user_id=os.getenv("DEMO_USER_ID", "user-demo-001"),
            tags=["full-trace-demo", "deepagents"],
        ):
            report = await run_workflow(model, langfuse, [handler])
        trace_url = get_public_trace_url(langfuse, report["trace_id"])
        report["trace_url"] = trace_url
        args.output_dir.mkdir(parents=True, exist_ok=True)
        report_path = args.output_dir / f"{report['trace_id']}.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("最终回答：", report["answer"])
        print("评估结果：", json.dumps(report["scores"], ensure_ascii=False))
        print("评估报告：", report_path.resolve())
        print("查看追踪：", trace_url)
        if not report["passed"]:
            raise SystemExit("本轮规则评估未全部通过，详情见报告和 Langfuse Scores。")
    finally:
        langfuse.flush()


if __name__ == "__main__":
    asyncio.run(main())
