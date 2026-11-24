"""将原 Langfuse 订单样例适配为真实多轮 DeepAgents 调用和 Opik 会话评分。"""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from smoke import ensure_project, read_local_config
from session_evaluation import DEFAULT_MODEL, check_model, ensure_evaluator, evaluate_sessions


def query_order_data(order_id):
    """沿用原 demo.py 的固定订单事实，业务结果不依赖外部订单服务。"""
    if order_id != "ORD-1001":
        return {"order_id": order_id, "found": False, "message": "未找到订单"}
    return {
        "order_id": order_id, "found": True, "status": "已发货", "carrier": "顺丰",
        "tracking_no": "SF-DEMO-1001", "items": ["无线鼠标"],
    }


def build_agent(model):
    """复用原订单助手的工具和提示，增加隔离会话的内存检查点。"""
    from deepagents import create_deep_agent
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import InMemorySaver

    @tool
    def query_order(order_id: str) -> str:
        """根据订单编号查询订单状态和物流，返回包含查询结果的 JSON 文本。"""
        return json.dumps(query_order_data(order_id), ensure_ascii=False)

    return create_deep_agent(
        model=model, tools=[query_order], checkpointer=InMemorySaver(), name="order-assistant",
        system_prompt=(
            "你是中文订单助手。首次查询订单必须调用 query_order，"
            "只根据工具返回的真实结果回答。后续追问可以使用本会话已查询的结果。"
            "找不到订单时如实说明，不得编造物流信息。"
            "保留本会话的订单编号，正确理解‘刚才那单’、‘它’和单号等指代。"
            "本演示直接查询订单即可，无需规划任务、操作文件或委派子智能体。"
        ),
    )


def run_conversation(agent, project_name, thread_id, questions):
    """逐轮真实调用 Agent，每轮一个 trace，模型和工具回调挂在该 trace 下。"""
    import opik
    from opik import opik_context
    from opik.integrations.langchain import OpikTracer
    from langchain_core.messages import ToolMessage

    # 检查点返回累计历史，按工具调用编号区分本轮和先前轮次的工具结果。
    seen_tool_calls = set()

    @opik.track(name="handle-order-question", project_name=project_name, capture_input=False, capture_output=False)
    def run_turn(question, turn_number):
        """外层只记录本轮问答，完整历史由检查点保存，避免会话视图重复历史。"""
        opik_context.update_current_trace(
            input={"question": question}, thread_id=thread_id,
            metadata={"demo": "deepagents-order", "turn": turn_number, "session_id": thread_id},
        )
        tracer = OpikTracer(project_name=project_name, thread_id=thread_id, tags=["deepagents", "order-demo"])
        result = agent.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config={"callbacks": [tracer], "run_name": "deepagents-order-agent",
                    "configurable": {"thread_id": thread_id}, "recursion_limit": 30},
        )
        answer = result["messages"][-1].content
        opik_context.update_current_trace(output={"answer": answer})
        new_tool_results = [
            message for message in result["messages"]
            if isinstance(message, ToolMessage) and message.name == "query_order"
            and message.tool_call_id not in seen_tool_calls
        ]
        seen_tool_calls.update(message.tool_call_id for message in new_tool_results)
        if turn_number == 1 and not new_tool_results:
            raise RuntimeError("第一轮没有实际调用 query_order，请确认模型支持工具调用。")
        print(f"  第 {turn_number} 轮问题：{question}\n  回答：{answer}", flush=True)
        for message in new_tool_results:
            print("  query_order 实际返回：", message.content, flush=True)
        print("  Trace：", opik_context.get_current_trace_data().id, flush=True)

    print(f"\n开始会话：{thread_id}", flush=True)
    for turn_number, question in enumerate(questions, start=1):
        run_turn(question, turn_number, opik_args={"trace": {"thread_id": thread_id, "project_name": project_name}})


def parse_args():
    """集中定义真实运行和只评已有会话的入口，默认一次运行两个三轮会话。"""
    config = read_local_config()
    port = os.environ.get("OPIK_PORT", config.get("OPIK_PORT", "5174"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=f"http://127.0.0.1:{port}/api")
    parser.add_argument("--project", default="deepagents-opik-order-demo")
    parser.add_argument("--model", default=os.environ.get("OPIK_DEMO_MODEL", DEFAULT_MODEL))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-evaluate", action="store_true", help="仅生成真实多轮会话，之后在页面选择 Evaluate")
    mode.add_argument("--evaluate-thread", action="append", default=[], help="只评已有会话，可重复指定，不重跑 Agent")
    parser.add_argument("--evaluation-timeout", type=int, default=180, help="等待原生评分回写的秒数")
    return parser.parse_args()


def main():
    """通过已保存的 TAMS 配置调用模型，上报多轮追踪后执行原生 Threads 评分。"""
    args = parse_args()
    if args.evaluation_timeout <= 0:
        raise ValueError("--evaluation-timeout 必须大于 0")
    base_url = args.base_url.rstrip("/")
    opener = build_opener(ProxyHandler({}))
    check_model(opener, base_url, args.model)
    project = ensure_project(opener, base_url, args.project, "DeepAgents 真实订单工具调用、多轮会话与原生 Threads 评分")
    project_url = base_url.removesuffix("/api") + "/default/projects/" + project["id"]
    threads_url = project_url + "/logs?" + urlencode({"logsType": "threads", "time_range": "past30days"})
    print("会话页面：", threads_url, flush=True)
    if args.evaluate_thread:
        evaluate_sessions(opener, base_url, args.project, args.evaluate_thread, args.model, args.evaluation_timeout)
        return

    # 使用本项目明确的地址和项目，追踪数据不进入原 Langfuse 或云端默认项目。
    os.environ.update({"OPIK_URL_OVERRIDE": base_url, "OPIK_PROJECT_NAME": args.project,
                       "OPIK_WORKSPACE": "default", "OPIK_API_KEY": "opik-local",
                       "OPIK_ANALYTICS_ENABLE": "false"})
    import opik
    from langchain_openai import ChatOpenAI

    # Opik 网关使用已保存的 TAMS 密钥转发请求，本脚本无需读取或另存上游密钥。
    model = ChatOpenAI(
        model=args.model, base_url=base_url + "/v1/private", api_key="opik-local",
        temperature=0, timeout=75, max_retries=0,
        extra_body={"custom_parameters": {"enable_thinking": False}},
    )
    agent = build_agent(model)
    run_id = uuid4().hex[:10]  # 每次生成新会话，防止不同次运行的历史混在一起。
    scenarios = {
        "known": [
            "请调用 query_order 查询订单 ORD-1001，告诉我状态和快递单号。",
            "刚才那单买的是什么？哪家公司承运？",
            "请用一句话把刚才那单的订单号、状态、商品、承运商和快递单号总结给我。",
        ],
        "missing": [
            "请调用 query_order 查询订单 ORD-404，告诉我能否查到。",
            "那能告诉我它的快递单号吗？不要猜测。",
            "总结一下这次查单的结果，并告诉我接下来应该提供什么信息。",
        ],
    }
    thread_ids = []
    try:
        for scenario, questions in scenarios.items():
            thread_id = f"order-{scenario}-{run_id}"
            thread_ids.append(thread_id)
            run_conversation(agent, args.project, thread_id, questions)
    finally:
        opik.flush_tracker()
    print("\n多轮追踪已发送：", ", ".join(thread_ids), flush=True)
    if not args.no_evaluate:
        evaluate_sessions(opener, base_url, args.project, thread_ids, args.model, args.evaluation_timeout)
    else:
        _, rule_name = ensure_evaluator(opener, base_url, project["id"], args.model)
        print(f"已准备暂停的 Threads 规则：{rule_name}；可在页面勾选会话后点击 Evaluate。", flush=True)
    print("查看会话和评分：", threads_url, flush=True)


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        # 只报告状态，不回显网关可能返回的认证上下文。
        print(f"运行失败：Opik HTTP {error.code}，请检查服务和 Online evaluation 日志。", file=sys.stderr)
        sys.exit(1)
    except (URLError, RuntimeError, ValueError, KeyError) as error:
        print(f"运行失败：{error}", file=sys.stderr)
        sys.exit(1)
