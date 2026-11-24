"""用本地 MCP 和模拟模型响应验证流程，不请求真实模型或 Langfuse 服务。"""

import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from mcp import Client
from mcp.types import CallToolResult, TextContent
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from deepseek_model import ChatDeepSeekWithThinking
from full_trace_agent import (
    WorkflowEvents,
    evaluate_answer,
    mcp_server_parameters,
    order_mcp_tool,
    run_workflow,
)
from full_trace_data import ANSWER_FIELDS, CONTACT_CHANNEL, DEMO_ORDER


def correct_answer() -> str:
    """构造任务参考答案；测试会单独破坏各项事实来验证评分能识别失败。"""
    return json.dumps(
        {
            **{
                key: DEMO_ORDER[key]
                for key in ANSWER_FIELDS
                if key != "contact_channel"
            },
            "contact_channel": CONTACT_CHANNEL,
        },
        ensure_ascii=False,
    )


def completed_events() -> WorkflowEvents:
    """构造评分单测的成功执行证据，不作为真实运行结果上报。"""
    return WorkflowEvents(
        compressions=[{"summary": "用户选定在线客服作为售后联系渠道"}],
        skills=[{"name": "order-status"}],
        mcp_calls=[{"arguments": {"order_id": "ORD-1001"}, "result": dict(DEMO_ORDER)}],
    )


def message_text(message: dict) -> str:
    """兼容实际模型请求中的字符串正文和文本内容块。"""
    content = message.get("content", "")
    return (
        content
        if isinstance(content, str)
        else "".join(block.get("text", "") for block in content)
    )


class EvaluationTests(unittest.TestCase):
    """分别验证格式、订单真实性和压缩事实保留，避免只检验成功路径。"""

    def test_correct_answer_passes(self):
        """正确答案与完整执行证据应通过全部规则。"""
        self.assertTrue(
            all(
                item["value"] == 1
                for item in evaluate_answer(correct_answer(), completed_events())
            )
        )

    def test_wrong_tracking_number_fails(self):
        """模型生成错误物流号时，格式通过但订单准确性必须失败。"""
        answer = json.loads(correct_answer())
        answer["tracking_no"] = "编造的快递号"
        scores = {
            item["name"]: item["value"]
            for item in evaluate_answer(json.dumps(answer), completed_events())
        }
        self.assertEqual(scores["answer_format"], 1)
        self.assertEqual(scores["order_accuracy"], 0)

    def test_answer_without_mcp_evidence_fails(self):
        """即使文字答案碰巧正确，没有本轮实际查询也不能判订单准确。"""
        events = completed_events()
        events.mcp_calls.clear()
        scores = {
            item["name"]: item["value"]
            for item in evaluate_answer(correct_answer(), events)
        }
        self.assertEqual(scores["order_accuracy"], 0)
        self.assertEqual(scores["workflow_execution"], 0)

    def test_lost_summary_fact_fails_even_if_answer_guesses_right(self):
        """最终回答正确仍不能掩盖摘要丢失历史事实。"""
        events = completed_events()
        events.compressions[0]["summary"] = "用户曾经询问过订单。"
        scores = {
            item["name"]: item["value"]
            for item in evaluate_answer(correct_answer(), events)
        }
        self.assertEqual(scores["compression_fact_retention"], 0)

    def test_invalid_json_or_extra_fields_fail_format(self):
        """自然语言、代码围栏、非对象和额外字段都不符合 Skill 的输出约定。"""
        for answer in (
            "已发货",
            f"```json\n{correct_answer()}\n```",
            "[]",
            correct_answer()[:-1] + ', "extra": 1}',
        ):
            with self.subTest(answer=answer):
                self.assertEqual(
                    evaluate_answer(answer, completed_events())[0]["value"], 0
                )


class FullTraceTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 DeepAgents、Langfuse SDK 与 MCP 子进程，模型和导出均留在内存。"""

    async def asyncSetUp(self):
        """独立的内存导出器收集真实 SDK span，HTTP 传输器拒绝意外联网。"""
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()

        def reject_request(request):
            """测试期间任何 Langfuse HTTP 请求都视为错误，不连接本地或线上服务。"""
            raise AssertionError(f"测试不应发送 Langfuse HTTP 请求：{request.url}")

        self.http_client = httpx.Client(transport=httpx.MockTransport(reject_request))
        self.public_key = f"pk-offline-{uuid4().hex}"
        self.langfuse = Langfuse(
            public_key=self.public_key,
            secret_key="offline-test-secret",
            base_url="http://langfuse.invalid",
            tracer_provider=self.provider,
            span_exporter=self.exporter,
            httpx_client=self.http_client,
        )

    async def asyncTearDown(self):
        """刷新并回收本测试的内存追踪资源。"""
        self.langfuse.shutdown()
        self.http_client.close()

    async def test_real_mcp_known_and_unknown_order(self):
        """真实子进程完成发现及两次协议调用，未知订单不得伪装成成功。"""
        async with Client(mcp_server_parameters()) as client:
            catalog = await client.list_tools()
            self.assertIn("query_order", [item.name for item in catalog.tools])
            known = await client.call_tool("query_order", {"order_id": "ORD-1001"})
            self.assertFalse(known.is_error)
            self.assertEqual(
                {key: known.structured_content[key] for key in DEMO_ORDER}, DEMO_ORDER
            )
            unknown = await client.call_tool("query_order", {"order_id": "ORD-UNKNOWN"})
            self.assertFalse(unknown.is_error)
            self.assertFalse(unknown.structured_content["found"])
            invalid = await client.call_tool("query_order", {"order_id": None})
            self.assertTrue(invalid.is_error)

    async def test_full_graph_records_compression_skill_mcp_and_scores(self):
        """完整图用固定响应执行，核对正文、摘要、协议输入输出及同一条追踪的父子关系。"""
        requests = []

        def model_response(request):
            """截获模型 HTTP 请求，并返回摘要、工具请求或最终回答。"""
            payload = json.loads(request.content)
            requests.append(payload)
            self.assertEqual(request.url.host, "model.invalid")
            is_summary = message_text(payload["messages"][0]).startswith(
                "你负责压缩对话"
            )
            if is_summary:
                message = {
                    "role": "assistant",
                    "content": "用户选择在线客服作为售后联系渠道，后续订单查询需保留。",
                }
            elif not any(item["role"] == "tool" for item in payload["messages"]):
                message = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "mcp-order-1",
                            "type": "function",
                            "function": {
                                "name": "query_order_mcp",
                                "arguments": '{"order_id":"ORD-1001"}',
                            },
                        }
                    ],
                }
            else:
                message = {"role": "assistant", "content": correct_answer()}
            message["reasoning_content"] = f"offline-reasoning-{len(requests)}"
            return httpx.Response(
                200,
                json={
                    "id": f"fake-{len(requests)}",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "deepseek-flash",
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": "tool_calls"
                            if "tool_calls" in message
                            else "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 25,
                        "total_tokens": 125,
                    },
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(model_response)
        ) as client:
            model = ChatDeepSeekWithThinking(
                model="deepseek-flash",
                api_key="offline-test-key",
                base_url="https://model.invalid/v1",
                http_async_client=client,
                use_responses_api=False,
                extra_body={"thinking": {"type": "enabled"}},
                max_retries=0,
            )
            # 评估接口留在内存；既不写本地项目，也不把模拟模型评估当成真实评估。
            handler = CallbackHandler(public_key=self.public_key)
            handler.run_inline = True
            with patch.object(self.langfuse, "create_score") as create_score:
                report = await run_workflow(model, self.langfuse, [handler])
        self.langfuse.flush()
        self.assertTrue(report["passed"], report["scores"])
        self.assertEqual(create_score.call_count, 4)
        self.assertEqual(len(report["events"]["compressions"]), 1)
        self.assertEqual(len(report["events"]["skills"]), 1)
        self.assertEqual(len(report["events"]["mcp_calls"]), 1)
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(item["thinking"]["type"] == "enabled" for item in requests))
        tool_request = next(
            item for item in requests[2]["messages"] if item.get("tool_calls")
        )
        self.assertEqual(tool_request["reasoning_content"], "offline-reasoning-2")
        agent_input = requests[1]["messages"]
        self.assertTrue(
            any(
                "最终回答必须是一个 JSON 对象" in message_text(item)
                for item in agent_input
            )
        )
        self.assertTrue(any("在线客服" in message_text(item) for item in agent_input))
        self.assertFalse(
            any("本段是演示历史" in message_text(item) for item in agent_input)
        )

        spans = self.exporter.get_finished_spans()
        by_id = {span.context.span_id: span for span in spans}
        names = [span.name for span in spans]
        for name in (
            "context_compression",
            "skill.load",
            "mcp.call_tool",
            "evaluate-order-answer",
        ):
            self.assertEqual(names.count(name), 1, names)
        self.assertEqual(len({span.context.trace_id for span in spans}), 1)
        root = next(span for span in spans if span.name == "order-full-trace-workflow")
        # Agent 的内联回调可能留下收尾节点上下文；评估必须直接归属于工作流根节点。
        evaluation = next(span for span in spans if span.name == "evaluate-order-answer")
        self.assertEqual(evaluation.parent.span_id, root.context.span_id)
        for span in spans:
            if span is not root:
                self.assertIsNotNone(span.parent, span.name)
                self.assertIn(span.parent.span_id, by_id, span.name)
        discovery = next(span for span in spans if span.name == "mcp.discover_tools")
        self.assertFalse(
            any(
                span.parent and span.parent.span_id == discovery.context.span_id
                for span in spans
            )
        )
        rpc = next(span for span in spans if span.name == "mcp.call_tool")
        self.assertEqual(by_id[rpc.parent.span_id].name, "query_order_mcp")
        generations = [
            span
            for span in spans
            if span.attributes.get("langfuse.observation.type") == "generation"
        ]
        self.assertEqual(len(generations), 3)
        self.assertTrue(
            any(
                by_id[span.parent.span_id].name == "context_compression"
                for span in generations
            )
        )
        for call in create_score.call_args_list:
            self.assertEqual(call.kwargs["trace_id"], report["trace_id"])
            self.assertEqual(
                call.kwargs["observation_id"], f"{root.context.span_id:016x}"
            )
            self.assertEqual(call.kwargs["data_type"], "BOOLEAN")

    async def test_mcp_protocol_error_does_not_increment_success_count(self):
        """协议错误必须保留失败节点，不能写入成功调用计数。"""
        events = WorkflowEvents()
        async with order_mcp_tool(self.langfuse, events) as mcp_tool:
            failure = CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text="测试用的服务端错误")],
            )
            with (
                patch.object(Client, "call_tool", new=AsyncMock(return_value=failure)),
                self.assertRaisesRegex(RuntimeError, "MCP 工具执行失败"),
            ):
                await mcp_tool.ainvoke({"order_id": "ORD-1001"})
        self.assertFalse(events.mcp_calls)
        self.langfuse.flush()
        failed_span = next(
            span
            for span in self.exporter.get_finished_spans()
            if span.name == "mcp.call_tool"
        )
        self.assertEqual(failed_span.attributes["langfuse.observation.level"], "ERROR")


if __name__ == "__main__":
    unittest.main()
