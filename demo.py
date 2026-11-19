"""演示 DeepAgents 向 Langfuse v4 记录问答、工具参数及工具结果。"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# 在初始化 Langfuse 前加载项目配置，避免继承其他项目的同名变量。
# Docker 镜像不包含 .env，容器运行时仍使用 Compose 注入的变量。
load_dotenv(Path(__file__).with_name(".env"), override=True)

from deepagents import create_deep_agent
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langfuse import get_client, propagate_attributes
from langfuse.langchain import CallbackHandler


@tool
def query_order(order_id: str) -> str:
    """根据订单编号查询订单状态和物流，返回包含查询结果的 JSON 文本。"""
    # 使用固定演示数据，让工具不依赖真实订单系统。
    if order_id != "ORD-1001":
        return json.dumps(
            {"order_id": order_id, "found": False, "message": "未找到订单"},
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "order_id": order_id,
            "found": True,
            "status": "已发货",
            "carrier": "顺丰",
            "tracking_no": "SF-DEMO-1001",
            "items": ["无线鼠标"],
        },
        ensure_ascii=False,
    )


def main() -> None:
    """运行一次订单问答，并在退出前发送本次追踪数据。"""
    required_keys = (
        "LANGFUSE_BASE_URL",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "OPENAI_API_KEY",
    )
    for key in required_keys:
        if not os.getenv(key) or "CHANGE_ME" in os.environ[key]:
            raise RuntimeError(f"请先在 .env 中填写 {key}")

    # 兼容服务可通过额外请求体配置能力，例如 DeepSeek 的非思考模式。
    try:
        extra_body = json.loads(os.getenv("OPENAI_EXTRA_BODY") or "{}")
    except json.JSONDecodeError as error:
        raise RuntimeError("OPENAI_EXTRA_BODY 必须是合法的 JSON 对象") from error
    if not isinstance(extra_body, dict):
        raise RuntimeError("OPENAI_EXTRA_BODY 必须是 JSON 对象，例如 {}")

    langfuse = get_client()
    if not langfuse.auth_check():
        raise RuntimeError("Langfuse 认证失败：请确认服务已就绪，且项目公私钥与服务端一致")

    # 模型必须支持工具调用；兼容 OpenAI 的服务可通过 OPENAI_BASE_URL 切换。
    model = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        temperature=0,
        extra_body=extra_body,
        timeout=60,
        max_retries=1,
    )
    agent = create_deep_agent(
        model=model,
        tools=[query_order],
        name="order-assistant",
        system_prompt=(
            "你是中文订单助手。查询订单时，必须先调用 query_order，"
            "根据工具返回的真实结果回答订单状态和快递单号。"
            "本演示直接查询订单即可，无需规划任务、操作文件或委派子智能体。"
        ),
    )
    # 回调负责记录内部模型调用、工具请求参数和工具返回结果。
    langfuse_handler = CallbackHandler()
    question = os.getenv(
        "DEMO_QUESTION", "请调用 query_order 查询订单 ORD-1001，告诉我状态和快递单号。"
    )

    try:
        # 将用户和会话信息传给所有子步骤，方便筛选同一用户的完整调用链。
        with propagate_attributes(
            trace_name="deepagents-order-demo",
            user_id=os.getenv("DEMO_USER_ID", "user-demo-001"),
            session_id=os.getenv("DEMO_SESSION_ID", "conversation-demo-001"),
            tags=["deepagents", "order-demo"],
        ):
            # 外层显式保存完整用户问题与最终回答，作为整次请求的入口。
            with langfuse.start_as_current_observation(
                as_type="agent", name="handle-order-question", input=question
            ) as request_observation:
                result = agent.invoke(
                    {"messages": [{"role": "user", "content": question}]},
                    config={
                        "callbacks": [langfuse_handler],
                        "run_name": "deepagents-order-agent",
                        "recursion_limit": 30,
                    },
                )
                answer = result["messages"][-1].content
                request_observation.update(output=answer)

                # 检查本轮确实执行过查询工具，避免只生成文字而没有演示工具调用。
                tool_results = [
                    message
                    for message in result["messages"]
                    if isinstance(message, ToolMessage) and message.name == "query_order"
                ]
                if not tool_results:
                    raise RuntimeError("本轮模型没有调用 query_order，请确认模型支持工具调用")

                print("用户问题：", question)
                print("最终回答：", answer)
                for message in tool_results:
                    print("工具返回：", message.content)
                print("Trace ID：", request_observation.trace_id)
                # 容器内上报走服务名，提供给用户的链接仍使用浏览器可访问的地址。
                trace_url = langfuse.get_trace_url(trace_id=request_observation.trace_id)
                public_url = os.getenv("LANGFUSE_PUBLIC_URL")
                if trace_url and public_url:
                    trace_url = trace_url.replace(
                        os.environ["LANGFUSE_BASE_URL"].rstrip("/"),
                        public_url.rstrip("/"),
                        1,
                    )
                if trace_url:
                    print("查看追踪：", trace_url)
    finally:
        # 短脚本必须等待缓冲区发送完成；正常和异常退出都执行。
        langfuse.flush()


if __name__ == "__main__":
    main()
