"""演示 DeepAgents 向 Langfuse v4 记录问答、工具参数及工具结果。"""

import json
import os
from demo_common import create_runtime, get_public_trace_url

from deepagents import create_deep_agent
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langfuse import propagate_attributes
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
    model, langfuse = create_runtime()
    # 文件与 task 工具由 DeepAgents 内置中间件提供，业务工具在本文件定义。
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
                    if isinstance(message, ToolMessage)
                    and message.name == "query_order"
                ]
                if not tool_results:
                    raise RuntimeError(
                        "本轮模型没有调用 query_order，请确认模型支持工具调用"
                    )

                print("用户问题：", question)
                print("最终回答：", answer)
                for message in tool_results:
                    print("工具返回：", message.content)
                print("Trace ID：", request_observation.trace_id)
                # 容器内上报走服务名，提供给用户的链接仍使用浏览器可访问的地址。
                trace_url = get_public_trace_url(langfuse, request_observation.trace_id)
                if trace_url:
                    print("查看追踪：", trace_url)
    finally:
        # 短脚本必须等待缓冲区发送完成；正常和异常退出都执行。
        langfuse.flush()


if __name__ == "__main__":
    main()
