"""把真实摘要压缩、Skill 加载、MCP 协议调用和规则评估接入同一条追踪。"""

import hashlib
import json
import sys
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.backends.utils import create_file_data
from deepagents.middleware.skills import SkillsMiddleware
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from mcp import Client, StdioServerParameters

from full_trace_data import ANSWER_FIELDS, CONTACT_CHANNEL, DEMO_ORDER

SKILL_PATH = "/skills/order-status/SKILL.md"
QUESTION = "请查询订单 ORD-1001 的状态和物流，并带上我之前选定的联系渠道。"


@contextmanager
def traced_observation(langfuse, **kwargs):
    """保留异常并显式标记 Langfuse 错误等级，方便在面板筛选失败动作。"""
    with langfuse.start_as_current_observation(**kwargs) as observation:
        try:
            yield observation
        except Exception as error:
            observation.update(
                level="ERROR", status_message=f"{type(error).__name__}: {error}"
            )
            raise


@dataclass
class WorkflowEvents:
    """只收集本轮实际完成的动作，供报告和评估核对执行路径。"""

    compressions: list[dict] = field(default_factory=list)
    skills: list[dict] = field(default_factory=list)
    mcp_calls: list[dict] = field(default_factory=list)


def demo_history() -> list:
    """构造明确标记的演示历史，让旧消息达到压缩阈值且包含待保留事实。"""
    messages = [
        HumanMessage(
            content=f"本段是演示历史。我选择的售后联系渠道是{CONTACT_CHANNEL}。"
        ),
        AIMessage(content="已记录你的联系渠道，后续查询订单时会保留。"),
    ]
    for index in range(8):
        messages.extend(
            [
                HumanMessage(
                    content=f"历史第 {index + 1} 轮：请说明订单查询的一般流程。"
                ),
                AIMessage(
                    content="先确认订单编号，再查询订单服务；不猜测状态或快递信息。" * 3
                ),
            ]
        )
    return [*messages, HumanMessage(content=QUESTION)]


def mcp_server_parameters() -> StdioServerParameters:
    """用当前虚拟环境启动本地 MCP 子进程，不额外传入模型或追踪密钥。"""
    return StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("full_trace_mcp_server.py"))],
        cwd=str(Path(__file__).parent),
    )


@asynccontextmanager
async def order_mcp_tool(langfuse, events: WorkflowEvents):
    """保持 MCP 会话到 Agent 执行结束，并把已发现的工具交给 Agent 调用。"""
    async with AsyncExitStack() as stack:
        with traced_observation(
            langfuse,
            name="mcp.discover_tools",
            input={"server": "demo-orders", "transport": "stdio"},
        ) as discovery:
            # 连接、初始化、发现均经过 MCP 协议，连接失败会标记当前节点为 ERROR。
            client = await stack.enter_async_context(
                Client(mcp_server_parameters(), read_timeout_seconds=20)
            )
            catalog = await client.list_tools()
            discovery.update(output=catalog.model_dump(mode="json", exclude_none=True))
            names = {item.name for item in catalog.tools}
            if "query_order" not in names:
                raise RuntimeError("MCP 服务没有提供 query_order 工具")

        # 发现节点先结束，连接由退出栈继续持有，后续 Agent 不挂在发现节点下面。
        @tool
        async def query_order_mcp(order_id: str) -> str:
            """通过 MCP 查询订单状态与物流，返回服务端的真实 JSON。"""
            arguments = {"order_id": order_id}
            with traced_observation(
                langfuse,
                name="mcp.call_tool",
                input={
                    "server": "demo-orders",
                    "tool": "query_order",
                    "arguments": arguments,
                },
                metadata={"transport": "stdio", "mcp_method": "tools/call"},
            ) as observation:
                result = await client.call_tool(
                    "query_order", arguments, read_timeout_seconds=20
                )
                observation.update(
                    output=result.model_dump(mode="json", exclude_none=True)
                )
                if result.is_error:
                    raise RuntimeError(f"MCP 工具执行失败：{result.content}")
                payload = result.structured_content
                if not isinstance(payload, dict) or "found" not in payload:
                    raise RuntimeError("MCP 工具返回值缺少订单查询结构")
                events.mcp_calls.append(
                    {"tool": "query_order", "arguments": arguments, "result": payload}
                )
                return json.dumps(payload, ensure_ascii=False)

        yield query_order_mcp


class TracedSkillsMiddleware(SkillsMiddleware):
    """在原生 pinned Skill 加载前后记录实际读取的正文，而非只记录技能名称。"""

    def __init__(self, *, backend, langfuse, events: WorkflowEvents):
        """沿用原生 Skill 扫描和正文注入，只增加追踪。"""
        super().__init__(backend=backend, sources=["/skills/"])
        self.tracing = langfuse
        self.events = events

    @property
    def name(self) -> str:
        """用原生名称替换默认中间件，避免同一个 Skill 被重复加载。"""
        return "SkillsMiddleware"

    async def abefore_model(self, state, runtime):
        """只有明确请求加载 Skill 时才建节点，并校验正文确实加入模型消息。"""
        names = state.get("pinned_skills")
        if not names:
            return None
        with traced_observation(
            self.tracing,
            name="skill.load",
            input={"names": names, "source": SKILL_PATH},
        ) as observation:
            update = await super().abefore_model(state, runtime)
            loaded = [
                {
                    "name": message.additional_kwargs["skill"]["name"],
                    "body": message.content,
                }
                for message in (update or {}).get("messages", [])
                if "skill" in message.additional_kwargs
            ]
            if set(names) != {item["name"] for item in loaded}:
                raise RuntimeError(
                    "Skill 正文未完整加载，请检查 demo_skills/order-status/SKILL.md"
                )
            self.events.skills.extend(loaded)
            observation.update(output=loaded, metadata={"loaded": True})
            return update


def build_full_trace_agent(model, summary_model, mcp_tool, langfuse, callbacks, events):
    """使用原生压缩调度和 Skill 系统构建 Agent；只替换实际写摘要的函数。"""
    backend = StateBackend()

    async def summarize(messages: list) -> str:
        """达到原生阈值后才调用模型写摘要，每次真实执行产生一个压缩节点。"""
        with traced_observation(
            langfuse,
            name="context_compression",
            input=[message.model_dump(mode="json") for message in messages],
            metadata={"trigger": "messages>=12", "token_counts": "approximate"},
        ) as observation:
            response = await summary_model.ainvoke(
                [
                    SystemMessage(
                        content=(
                            "你负责压缩对话。只输出不超过150字的事实摘要。"
                            "必须保留用户已选择的联系渠道、订单号和未完成请求；不要添加历史里没有的事实。"
                        )
                    ),
                    *messages,
                ],
                config={"callbacks": callbacks, "run_name": "write-context-summary"},
            )
            summary = response.text.strip()
            if not summary:
                raise RuntimeError("压缩模型返回空摘要")
            event = {
                "summary": summary,
                "input_messages": len(messages),
                "estimated_input_tokens": count_tokens_approximately(messages),
                "estimated_summary_tokens": count_tokens_approximately(
                    [HumanMessage(content=summary)]
                ),
            }
            events.compressions.append(event)
            observation.update(
                output=summary, metadata={**event, "compression_status": "completed"}
            )
            return summary

    summarization = SummarizationMiddleware(
        model=summary_model,
        backend=backend,
        trigger=("messages", 12),
        keep=("messages", 4),
        summarizer=RunnableLambda(summarize),
    )
    return create_deep_agent(
        model=model,
        backend=backend,
        tools=[mcp_tool],
        middleware=[
            summarization,
            TracedSkillsMiddleware(backend=backend, langfuse=langfuse, events=events),
        ],
        name="full-order-agent",
        system_prompt=(
            "你是中文订单助手。必须调用 query_order_mcp 查询本轮订单，并遵守已加载的 order-status Skill。"
            "从对话或摘要读取联系渠道，按 Skill 指定格式回答。"
            "这是单轮订单演示，无需计划、委派子代理、修改文件或再次读取已加载的 Skill。"
        ),
    )


def evaluate_answer(answer: str, events: WorkflowEvents) -> list[dict]:
    """用固定任务的可核对事实评分；不把规则检查包装成模型裁判。"""
    try:
        parsed = json.loads(answer)
    except (ValueError, TypeError):
        parsed = None
    format_ok = isinstance(parsed, dict) and set(parsed) == set(ANSWER_FIELDS)
    # 只有本次成功查询目标订单的返回值，才能充当订单字段的评估依据。
    queried = [
        item["result"]
        for item in events.mcp_calls
        if item["arguments"].get("order_id") == DEMO_ORDER["order_id"]
        and item["result"].get("found") is True
    ]
    order_ok = (
        bool(queried)
        and isinstance(parsed, dict)
        and all(
            parsed.get(key) == queried[-1].get(key)
            for key in ANSWER_FIELDS
            if key != "contact_channel"
        )
    )
    # 同时检查摘要和最终回答，防止模型碰巧猜对渠道却掩盖摘要丢失事实。
    history_ok = (
        bool(events.compressions)
        and all(CONTACT_CHANNEL in item["summary"] for item in events.compressions)
        and isinstance(parsed, dict)
        and parsed.get("contact_channel") == CONTACT_CHANNEL
    )
    workflow_ok = bool(events.compressions and events.skills and events.mcp_calls)
    return [
        {
            "name": "answer_format",
            "value": int(format_ok),
            "comment": "最终回答必须是仅含 Skill 指定五个字段的 JSON 对象",
        },
        {
            "name": "order_accuracy",
            "value": int(order_ok),
            "comment": "订单四个字段必须与本次成功的 MCP 查询一致",
        },
        {
            "name": "compression_fact_retention",
            "value": int(history_ok),
            "comment": "摘要和最终回答都必须保留历史联系渠道：在线客服",
        },
        {
            "name": "workflow_execution",
            "value": int(workflow_ok),
            "comment": "必须实际执行摘要压缩、Skill 正文加载和 MCP 调用",
        },
    ]


async def run_workflow(
    model, langfuse, callbacks, *, summary_model=None
) -> dict[str, Any]:
    """执行一轮完整流程；模型与追踪可注入，以便测试时完全隔离外部请求。"""
    events = WorkflowEvents()
    skill_body = (
        Path(__file__)
        .with_name("demo_skills")
        .joinpath("order-status/SKILL.md")
        .read_text(encoding="utf-8")
    )
    with traced_observation(
        langfuse,
        as_type="agent",
        name="order-full-trace-workflow",
        input=QUESTION,
        metadata={
            "history_source": "synthetic_demo",
            "skill_sha256": hashlib.sha256(skill_body.encode()).hexdigest(),
        },
    ) as root:
        async with order_mcp_tool(langfuse, events) as mcp_tool:
            agent = build_full_trace_agent(
                model, summary_model or model, mcp_tool, langfuse, callbacks, events
            )
            result = await agent.ainvoke(
                {
                    "messages": demo_history(),
                    "files": {SKILL_PATH: create_file_data(skill_body)},
                    "pinned_skills": ["order-status"],
                },
                config={
                    "callbacks": callbacks,
                    "run_name": "full-order-agent",
                    "recursion_limit": 20,
                },
            )
        answer = result["messages"][-1].text
        with traced_observation(
            langfuse,
            as_type="evaluator",
            name="evaluate-order-answer",
            input={"answer": answer},
            # 显式绑定工作流根节点，避免继承 Agent 内联回调遗留的收尾节点上下文。
            trace_context={"trace_id": root.trace_id, "parent_span_id": root.id},
        ) as evaluation:
            scores = evaluate_answer(answer, events)
            for score in scores:
                langfuse.create_score(
                    **score,
                    data_type="BOOLEAN",
                    trace_id=root.trace_id,
                    observation_id=root.id,
                    score_id=f"{root.trace_id}-{score['name']}",
                )
            evaluation.update(
                output=scores, metadata={"method": "deterministic_reference_checks"}
            )
        passed = all(score["value"] == 1 for score in scores)
        report = {
            "trace_id": root.trace_id,
            "question": QUESTION,
            "answer": answer,
            "evaluation_method": "deterministic_reference_checks",
            "scores": scores,
            "passed": passed,
            "events": {
                "compressions": events.compressions,
                "skills": events.skills,
                "mcp_calls": events.mcp_calls,
            },
        }
        root.update(
            output=answer,
            metadata={
                "compression_count": len(events.compressions),
                "skill_load_count": len(events.skills),
                "mcp_call_count": len(events.mcp_calls),
                "evaluation_passed": passed,
            },
            level="DEFAULT" if passed else "WARNING",
        )
        return report
