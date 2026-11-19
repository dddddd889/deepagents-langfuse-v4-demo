"""保留 DeepSeek 思考内容的 OpenAI 兼容 Chat Completions 客户端。"""

from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_deepseek import ChatDeepSeek


class ChatDeepSeekWithThinking(ChatDeepSeek):
    """沿用官方适配器，补齐多轮工具调用所需的 reasoning_content 字段。"""

    # 该兼容处理对应 Chat Completions 消息格式。
    use_responses_api: bool | None = False

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """把每条助手消息的思考内容原样回传，包含工具调用前后的消息。"""
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        for message, encoded in zip(messages, payload["messages"], strict=True):
            if isinstance(message, AIMessage):
                reasoning_content = message.additional_kwargs.get("reasoning_content")
                if reasoning_content is not None:
                    encoded["reasoning_content"] = reasoning_content
        return payload
