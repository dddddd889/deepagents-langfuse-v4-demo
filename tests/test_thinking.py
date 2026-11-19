"""验证 DeepSeek 思考模式的消息回传及模型客户端选择。"""

import json
import os
import unittest
from unittest.mock import Mock, patch

import httpx
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from deepseek_model import ChatDeepSeekWithThinking
from demo_common import create_runtime


class DeepSeekThinkingTests(unittest.TestCase):
    """通过真正的客户端序列化检查，不发送真实模型请求。"""

    def test_reasoning_survives_tool_and_conversation_turns(self):
        """工具调用前后两种助手消息的思考字段都应在后续请求中保留。"""
        requests = []
        responses = [
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "tool-reasoning-test-marker",
                "tool_calls": [
                    {
                        "id": "lookup-1",
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "arguments": '{"order_id":"ORD-1001"}',
                        },
                    }
                ],
            },
            {
                "role": "assistant",
                "content": "已发货",
                "reasoning_content": "answer-reasoning-test-marker",
            },
            {
                "role": "assistant",
                "content": "订单已发货。",
                "reasoning_content": "followup-reasoning-test-marker",
            },
        ]

        def handle_request(request):
            """记录客户端最终发送的 JSON，并返回固定的 OpenAI 格式响应。"""
            requests.append(json.loads(request.content))
            message = responses[len(requests) - 1]
            return httpx.Response(
                200,
                json={
                    "id": "thinking-test",
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
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
            )

        schema = {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "查询订单",
                "parameters": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                },
            },
        }
        with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
            model = ChatDeepSeekWithThinking(
                model="deepseek-flash",
                api_key="test-not-a-real-key",
                base_url="https://api.deepseek.com",
                extra_body={"thinking": {"type": "enabled"}},
                http_client=client,
                max_retries=0,
            )
            bound = model.bind_tools([schema])
            messages = [HumanMessage(content="查询订单")]
            first = bound.invoke(messages)
            self.assertEqual(
                first.additional_kwargs["reasoning_content"],
                "tool-reasoning-test-marker",
            )
            messages.extend(
                [first, ToolMessage(content="已发货", tool_call_id="lookup-1")]
            )
            second = bound.invoke(messages)
            messages.extend([second, HumanMessage(content="再给一句结论")])
            bound.invoke(messages)
        self.assertEqual(len(requests), 3)
        self.assertTrue(
            all(request["thinking"]["type"] == "enabled" for request in requests)
        )
        assistants = [
            message
            for message in requests[2]["messages"]
            if message["role"] == "assistant"
        ]
        self.assertEqual(
            [message["reasoning_content"] for message in assistants],
            ["tool-reasoning-test-marker", "answer-reasoning-test-marker"],
        )
        self.assertEqual(assistants[0]["tool_calls"][0]["id"], "lookup-1")

    def test_runtime_selects_provider_and_keeps_thinking_flag(self):
        """DeepSeek 使用支持思考回传的客户端，其他模型仍走原客户端。"""
        environment = {
            "LANGFUSE_BASE_URL": "http://127.0.0.1:3000",
            "LANGFUSE_PUBLIC_KEY": "test-public",
            "LANGFUSE_SECRET_KEY": "test-secret",
            "LANGFUSE_MEDIA_INTERNAL_ENDPOINT": "",
            "OPENAI_API_KEY": "test-not-a-real-key",
            "OPENAI_EXTRA_BODY": '{"thinking":{"type":"enabled"}}',
        }
        langfuse = Mock()
        langfuse.auth_check.return_value = True
        for model_name, base_url, expected in (
            ("deepseek-flash", "https://api.deepseek.com", ChatDeepSeekWithThinking),
            ("gpt-4.1-mini", "https://api.openai.com/v1", ChatOpenAI),
        ):
            with (
                self.subTest(model=model_name),
                patch.dict(
                    os.environ,
                    {
                        **environment,
                        "OPENAI_MODEL": model_name,
                        "OPENAI_BASE_URL": base_url,
                    },
                ),
                patch("demo_common.get_client", return_value=langfuse),
            ):
                model, tracing = create_runtime()
                self.assertIsInstance(model, expected)
                self.assertIs(tracing, langfuse)
                self.assertEqual(model.extra_body["thinking"]["type"], "enabled")


if __name__ == "__main__":
    unittest.main()
