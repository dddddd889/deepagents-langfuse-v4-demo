"""PDF 附件上传、存储验证，以及 Docker 内外网地址适配。"""

import base64
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path

import httpx
from langfuse import Langfuse
from langfuse.media import LangfuseMedia


class MediaEndpointTransport(httpx.BaseTransport):
    """把指定媒体站点的签名请求转到容器内网，保留签名使用的 Host。"""

    def __init__(
        self,
        public_endpoint: str,
        internal_endpoint: str,
        transport: httpx.BaseTransport | None = None,
    ):
        """保存需要映射的公开地址及内网地址，不改写其他服务请求。"""
        self.public = httpx.URL(public_endpoint)
        self.internal = httpx.URL(internal_endpoint)
        if self.public.scheme not in ("http", "https") or self.internal.scheme not in (
            "http",
            "https",
        ):
            raise ValueError("媒体地址必须使用 http 或 https")
        self.transport = transport or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """仅改写匹配的 S3 签名请求，路径、参数、请求体和原始 Host 均保留。"""
        origin = (request.url.scheme, request.url.host, request.url.port)
        public_origin = (self.public.scheme, self.public.host, self.public.port)
        if origin == public_origin and "X-Amz-Signature" in request.url.params:
            original_host = request.headers.get("host", request.url.netloc.decode())
            request.url = request.url.copy_with(
                scheme=self.internal.scheme,
                host=self.internal.host,
                port=self.internal.port,
            )
            request.headers["host"] = original_host
        return self.transport.handle_request(request)

    def close(self) -> None:
        """释放下层连接池。"""
        self.transport.close()


def create_media_http_client() -> httpx.Client:
    """宿主机直接访问公开地址；容器按显式配置转发媒体签名请求。"""
    internal = os.getenv("LANGFUSE_MEDIA_INTERNAL_ENDPOINT")
    if not internal:
        return httpx.Client(timeout=30)
    public = os.getenv("LANGFUSE_MEDIA_PUBLIC_ENDPOINT") or os.getenv("S3_PUBLIC_URL")
    if not public:
        raise RuntimeError(
            "设置媒体内网地址时，还需配置 LANGFUSE_MEDIA_PUBLIC_ENDPOINT"
        )
    return httpx.Client(timeout=30, transport=MediaEndpointTransport(public, internal))


@dataclass(frozen=True)
class PdfAttachment:
    """同一份 PDF 的媒体对象、原始字节和校验信息，避免读取不同版本。"""

    media: LangfuseMedia
    content: bytes = field(repr=False)
    sha256_hex: str
    sha256_base64: str


def prepare_pdf_attachment(pdf_path: Path) -> PdfAttachment:
    """读取用户提供的 PDF，并按 Langfuse 官方方式包装为附件。"""
    content = pdf_path.read_bytes()
    if not content:
        raise ValueError("PDF 文件为空，无法上传附件")
    digest = hashlib.sha256(content).digest()
    return PdfAttachment(
        media=LangfuseMedia(content_bytes=content, content_type="application/pdf"),
        content=content,
        sha256_hex=digest.hex(),
        # 媒体 API 使用 Base64 SHA256；文件验证及界面元数据使用十六进制。
        sha256_base64=base64.b64encode(digest).decode(),
    )


def verify_pdf_attachment(
    langfuse: Langfuse, attachment: PdfAttachment, trace_id: str, observation_id: str
) -> dict:
    """等待 SDK 上传并校验文件可下载，成功后返回可定位的存储信息。"""
    langfuse.flush()
    # 同一内容的上传地址接口会返回已有媒体编号，已成功上传时不再提供上传 URL。
    receipt = langfuse.api.media.get_upload_url(
        content_type="application/pdf",
        content_length=len(attachment.content),
        sha256hash=attachment.sha256_base64,
        trace_id=trace_id,
        observation_id=observation_id,
        field="input",
    )
    if receipt.upload_url:
        raise RuntimeError("PDF 附件尚未成功上传，请检查 Langfuse 与 MinIO 连接")
    media = langfuse.api.media.get(receipt.media_id)
    if media.content_type != "application/pdf" or media.content_length != len(
        attachment.content
    ):
        raise RuntimeError("PDF 附件的类型或长度校验失败")
    # 分段下载验证对象实际存在，避免仅凭数据库中有媒体记录就判定上传完成。
    with create_media_http_client() as client:
        with client.stream(
            "GET", media.url, headers={"Range": "bytes=0-4"}
        ) as response:
            response.raise_for_status()
            prefix = next(response.iter_bytes(chunk_size=5), b"")
            if prefix != attachment.content[:5]:
                raise RuntimeError("PDF 附件下载内容校验失败")
    return {
        "pdf_storage": "langfuse_media",
        "pdf_upload_status": "uploaded",
        "pdf_media_id": receipt.media_id,
        "pdf_sha256": attachment.sha256_hex,
        "pdf_size_bytes": len(attachment.content),
        # 不保存会过期的签名查询参数，仅记录实际对象路径供定位。
        "pdf_object_path": httpx.URL(media.url).path,
    }
