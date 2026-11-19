"""PDF 附件上传及签名地址映射的针对性测试。"""

import base64
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from langfuse.media import LangfuseMedia

from pdf_media import MediaEndpointTransport, PdfAttachment, verify_pdf_attachment


class PdfMediaTests(unittest.TestCase):
    """测试真实 HTTP 请求编码和附件失败处理，不生成任何 PDF 文件。"""

    def test_internal_route_preserves_signed_request(self):
        """容器可连内网地址，但签名 Host、查询参数和上传字节必须保持原值。"""
        captured = []

        def receive(request):
            """记录到达下层传输的请求。"""
            captured.append(request)
            return httpx.Response(200)

        transport = MediaEndpointTransport(
            "http://localhost:9090", "http://minio:9000", httpx.MockTransport(receive)
        )
        with httpx.Client(transport=transport) as client:
            client.put(
                "http://localhost:9090/langfuse/media/test.pdf?X-Amz-Signature=test%2Fsignature&X-Amz-Date=20261119",
                content=b"test-upload-bytes",
            )
        request = captured[0]
        self.assertEqual(
            str(request.url),
            "http://minio:9000/langfuse/media/test.pdf?X-Amz-Signature=test%2Fsignature&X-Amz-Date=20261119",
        )
        self.assertEqual(request.headers["host"], "localhost:9090")
        self.assertEqual(request.content, b"test-upload-bytes")

    def test_api_and_other_origins_are_not_redirected(self):
        """Langfuse API 和其他媒体站点的请求保持原地址。"""
        captured = []

        def receive(request):
            """记录原请求，避免在测试中连接任何实际服务。"""
            captured.append(str(request.url))
            return httpx.Response(200)

        urls = [
            "http://langfuse-web:3000/api/public/projects",
            "https://other.example/file.pdf?X-Amz-Signature=test",
            "http://localhost:9090/health",
        ]
        transport = MediaEndpointTransport(
            "http://localhost:9090", "http://minio:9000", httpx.MockTransport(receive)
        )
        with httpx.Client(transport=transport) as client:
            for url in urls:
                client.get(url)
        self.assertEqual(captured, urls)

    def test_unfinished_upload_fails_before_analysis(self):
        """SDK 未真正上传成功时，不能把已有媒体编号当成成功附件。"""
        content = b"in-memory-test-data"
        digest = hashlib.sha256(content).digest()
        attachment = PdfAttachment(
            LangfuseMedia(content_bytes=content, content_type="application/pdf"),
            content,
            digest.hex(),
            base64.b64encode(digest).decode(),
        )
        langfuse = Mock()
        langfuse.api.media.get_upload_url.return_value = SimpleNamespace(
            upload_url="http://localhost:9090/unfinished", media_id="test"
        )
        with self.assertRaisesRegex(RuntimeError, "尚未成功上传"):
            verify_pdf_attachment(langfuse, attachment, "trace", "span")
        langfuse.flush.assert_called_once()
        langfuse.api.media.get.assert_not_called()

    def test_uploaded_attachment_is_downloadable(self):
        """上传完成后还要下载校验，并只保留无签名参数的对象位置。"""
        content = b"in-memory-test-data"
        digest = hashlib.sha256(content).digest()
        attachment = PdfAttachment(
            LangfuseMedia(content_bytes=content, content_type="application/pdf"),
            content,
            digest.hex(),
            base64.b64encode(digest).decode(),
        )
        langfuse = Mock()
        langfuse.api.media.get_upload_url.return_value = SimpleNamespace(
            upload_url=None, media_id="test-media"
        )
        langfuse.api.media.get.return_value = SimpleNamespace(
            content_type="application/pdf",
            content_length=len(content),
            url="http://localhost:9090/langfuse/media/test.pdf?X-Amz-Signature=temporary",
        )

        def receive(request):
            """模拟对象存储分段返回文件开头的字节。"""
            self.assertEqual(request.headers["range"], "bytes=0-4")
            return httpx.Response(206, content=content[:5])

        with patch(
            "pdf_media.create_media_http_client",
            return_value=httpx.Client(transport=httpx.MockTransport(receive)),
        ):
            result = verify_pdf_attachment(langfuse, attachment, "trace", "span")
        self.assertEqual(result["pdf_media_id"], "test-media")
        self.assertEqual(result["pdf_upload_status"], "uploaded")
        self.assertEqual(result["pdf_object_path"], "/langfuse/media/test.pdf")
        self.assertEqual(result["pdf_sha256"], digest.hex())


if __name__ == "__main__":
    unittest.main()
