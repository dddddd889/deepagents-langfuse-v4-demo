"""验证真实 HTTP/MCP 通信和快照保留；不调用任何收费模型。"""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import ProxyHandler, build_opener

REPOSITORY_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_DIR))
sys.path.insert(0, str(REPOSITORY_DIR / "opik-local"))

from evidence_runtime import EvidenceStore
from mcp_pdf import PdfEvidenceService, McpPdfClient, adapt_mcp_tools
from pdf_tools import build_pdf_tools
from evidence_judge import EvidenceScore, EvaluationVerdict, METRIC_NAMES, REQUIRED_READS, validate_verdict, fetch_trace_evidence


class SnapshotTests(unittest.TestCase):
    """修改上下文后应生成新编号，先前证据仍可原样读取。"""

    def test_changed_context_keeps_original_snapshot(self):
        """相同内容复用快照；压缩后的新内容不能覆盖原文。"""
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            before = store.save("context", {"messages": [{"content": "旧要求：只分析前三页"}]})
            repeated = store.save("context", {"messages": [{"content": "旧要求：只分析前三页"}]})
            after = store.save("context", {"messages": [{"content": "压缩后的摘要"}]})
            self.assertEqual(before, repeated)
            self.assertNotEqual(before["snapshot_id"], after["snapshot_id"])
            self.assertIn("只分析前三页", (Path(directory) / before["file"]).read_text())


class EvaluationEvidenceTests(unittest.TestCase):
    """评分必须有实际取证，并覆盖超过第一页的业务节点。"""

    def test_reject_unread_or_invented_evidence_and_allow_failure_score(self):
        """模型不能跳过取证或引用假节点；有证据的 0 分必须可以正常保存。"""
        verdict = EvaluationVerdict(scores=[EvidenceScore(name=name, value=False,
            reason="实际工具证据不足，因此本项判定不通过。", evidence_span_ids=["real-span"]) for name in sorted(METRIC_NAMES)])
        with self.assertRaisesRegex(RuntimeError, "未完成四类取证"):
            validate_verdict(verdict, {"real-span"}, set(), {1, 2})
        with self.assertRaisesRegex(RuntimeError, "不存在"):
            validate_verdict(verdict, {"another-span"}, REQUIRED_READS, {1, 2})
        validate_verdict(verdict, {"real-span"}, REQUIRED_READS, {1, 2})
        self.assertFalse(any(score.value for score in verdict.scores))

    def test_collects_spans_after_first_hundred(self):
        """本次真实轨迹有 127 个节点，必须继续读取第二页，不能漏掉最早的加载和压缩。"""
        pages = []

        def request(opener, base_url, method, path):
            """模拟原生分页契约，不访问网络或模型。"""
            if "/traces/" in path:
                return {"project_id": "project", "end_time": "done"}, {}
            page = 2 if "page=2" in path else 1
            pages.append(page)
            start, end = (0, 100) if page == 1 else (100, 127)
            return {"total": 127, "content": [{"id": str(index), "project_id": "project"} for index in range(start, end)]}, {}

        with patch("evidence_judge.request_api", side_effect=request):
            _, spans = fetch_trace_evidence(None, "local", "project", "trace")
        self.assertEqual(len(spans), 127)
        self.assertEqual(pages, [1, 2])


class McpIntegrationTests(unittest.TestCase):
    """使用仓库已有 PDF，与本机随机端口上的官方 MCP 服务实际通信。"""

    def test_http_skill_mcp_discovery_execution_and_error(self):
        """验证正文哈希、工具参数格式、真实请求编号和 isError 的失败语义。"""
        events = []
        skill_path = REPOSITORY_DIR / "opik-local/skills/pdf-review/SKILL.md"
        tools = build_pdf_tools(REPOSITORY_DIR / "2507.21504v1.pdf", events)
        with PdfEvidenceService(tools, skill_path) as service:
            with build_opener(ProxyHandler({})).open(service.url + "/skills/pdf-review/SKILL.md") as response:
                content = response.read()
                self.assertEqual(response.headers["ETag"], hashlib.sha256(content).hexdigest())
                self.assertEqual(content, skill_path.read_bytes())
            with McpPdfClient(service.url + "/mcp") as client:
                client.catalog = client.discover()
                self.assertEqual({item["name"] for item in client.catalog}, {"get_pdf_info", "read_pdf_page"})
                page_schema = next(item["inputSchema"] for item in client.catalog if item["name"] == "read_pdf_page")
                self.assertIn("page_number", page_schema["properties"])
                self.assertNotIn("ctx", page_schema["properties"])
                adapted = {item.name: item for item in adapt_mcp_tools(client, "catalog:test")}
                info = json.loads(adapted["get_pdf_info"].invoke({}))
                page = json.loads(adapted["read_pdf_page"].invoke({"page_number": 1}))
                self.assertEqual(info["page_count"], 11)
                self.assertEqual(page["page_number"], 1)
                self.assertTrue(page["text"])
                self.assertEqual(page["_mcp"]["request_id"], service.calls[-1]["request_id"])
                self.assertEqual(adapted["read_pdf_page"].metadata["tool_origin"], "mcp")
                with self.assertRaisesRegex(RuntimeError, "MCP 工具 read_pdf_page 失败"):
                    client.call("read_pdf_page", {"page_number": 999})
                self.assertEqual(len(events), 1)
            self.assertEqual(service.skill_requests, 1)


if __name__ == "__main__":
    unittest.main()
