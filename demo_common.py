"""订单与 PDF 示例共用的模型配置和追踪连接。"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# 本地以项目 .env 为准；镜像不包含 .env，容器使用 Compose 注入的配置。
load_dotenv(Path(__file__).with_name(".env"), override=True)

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from deepseek_model import ChatDeepSeekWithThinking
from langfuse import Langfuse, get_client
from pdf_media import create_media_http_client


def create_runtime() -> tuple[BaseChatModel, Langfuse]:
    """校验配置并创建 OpenAI 兼容模型与已认证的 Langfuse 客户端。"""
    for key in (
        "LANGFUSE_BASE_URL",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "OPENAI_API_KEY",
    ):
        if not os.getenv(key) or "CHANGE_ME" in os.environ[key]:
            raise RuntimeError(f"请先在 .env 中填写 {key}")
    # 额外请求体用于 DeepSeek 思考开关等兼容服务参数。
    try:
        extra_body = json.loads(os.getenv("OPENAI_EXTRA_BODY") or "{}")
    except json.JSONDecodeError as error:
        raise RuntimeError("OPENAI_EXTRA_BODY 必须是合法的 JSON 对象") from error
    if not isinstance(extra_body, dict):
        raise RuntimeError("OPENAI_EXTRA_BODY 必须是 JSON 对象，例如 {}")
    if os.getenv("LANGFUSE_MEDIA_INTERNAL_ENDPOINT"):
        langfuse = Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            base_url=os.environ["LANGFUSE_BASE_URL"],
            httpx_client=create_media_http_client(),
        )
    else:
        langfuse = get_client()
    if not langfuse.auth_check():
        raise RuntimeError(
            "Langfuse 认证失败：请确认服务已就绪，且项目公私钥与服务端一致"
        )
    model_name = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    # DeepSeek 需要额外保留思考字段；其他 OpenAI 兼容模型沿用原客户端。
    model_class = (
        ChatDeepSeekWithThinking if model_name.startswith("deepseek-") else ChatOpenAI
    )
    model = model_class(
        model=model_name,
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        temperature=0,
        extra_body=extra_body,
        timeout=60,
        max_retries=1,
    )
    return model, langfuse


def get_public_trace_url(langfuse: Langfuse, trace_id: str) -> str | None:
    """把容器上报地址转换为用户浏览器能访问的追踪链接。"""
    trace_url = langfuse.get_trace_url(trace_id=trace_id)
    public_url = os.getenv("LANGFUSE_PUBLIC_URL")
    if trace_url and public_url:
        trace_url = trace_url.replace(
            os.environ["LANGFUSE_BASE_URL"].rstrip("/"), public_url.rstrip("/"), 1
        )
    return trace_url
