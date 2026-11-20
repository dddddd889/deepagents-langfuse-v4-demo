"""验证 PDF 自动采集开关、去重、人工答案保护和成功后才入库。"""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from langchain_core.messages import AIMessage, ToolMessage
from langfuse.api import NotFoundError

from pdf_dataset import collect_pdf_candidate, get_candidate_item_id
from pdf_demo import build_argument_parser, main


class PdfDatasetTests(unittest.TestCase):
    """只模拟数据集接口，不生成 PDF 或连接模型服务。"""

    def setUp(self):
        """准备同一原件的摘要与已封装媒体对象。"""
        self.attachment = SimpleNamespace(sha256_hex="a" * 64, media=object())
        self.client = Mock()
        self.client.api.dataset_items.get.side_effect = NotFoundError(body={})
        self.client.api.datasets.get.return_value = SimpleNamespace(id="dataset-test")
        self.client.create_dataset_item.return_value = SimpleNamespace(id="item-test")

    def collect(self, **changes):
        """用固定运行信息调用采集函数，允许各测试覆盖关键参数。"""
        arguments = {
            "enabled": True,
            "attachment": self.attachment,
            "filename": "provided.pdf",
            "question": "总结局限并引用页码",
            "answer": "候选答案【第 1 页】",
            "trace_id": "trace-test",
            "observation_id": "workflow-test",
        }
        return collect_pdf_candidate(self.client, **{**arguments, **changes})

    def test_collection_flag_is_opt_in(self):
        """默认不采集，只有显式传入开关才开启。"""
        parser = build_argument_parser()
        self.assertFalse(parser.parse_args([]).collect_dataset)
        self.assertTrue(parser.parse_args(["--collect-dataset"]).collect_dataset)
        self.assertEqual(
            self.collect(enabled=False), {"dataset_collection_status": "disabled"}
        )
        self.assertEqual(self.client.mock_calls, [])

    def test_identity_uses_file_content_and_original_question(self):
        """同文件同问题编号稳定，文件内容或问题改变时生成新编号。"""
        first = get_candidate_item_id("a" * 64, "问题:一")
        self.assertEqual(first, get_candidate_item_id("a" * 64, "问题:一"))
        self.assertNotEqual(first, get_candidate_item_id("b" * 64, "问题:一"))
        self.assertNotEqual(first, get_candidate_item_id("a" * 64, "问题:二"))

    def test_new_candidate_keeps_reference_empty_and_links_media(self):
        """新案例包含真实媒体对象和来源，模型回答不进入参考答案字段。"""
        result = self.collect()
        arguments = self.client.create_dataset_item.call_args.kwargs
        self.assertEqual(arguments["dataset_name"], "pdf-qa-candidates")
        self.assertEqual(
            arguments["id"], get_candidate_item_id("a" * 64, "总结局限并引用页码")
        )
        self.assertIs(arguments["input"]["pdf"], self.attachment.media)
        self.assertEqual(arguments["input"]["question"], "总结局限并引用页码")
        self.assertIsNone(arguments["expected_output"])
        self.assertEqual(
            arguments["metadata"]["candidate_answer"], "候选答案【第 1 页】"
        )
        self.assertFalse(arguments["metadata"]["reviewed"])
        self.assertEqual(arguments["source_trace_id"], "trace-test")
        self.assertEqual(arguments["source_observation_id"], "workflow-test")
        self.assertEqual(result["dataset_collection_status"], "created")
        self.client.create_dataset.assert_not_called()

    def test_missing_dataset_is_created_only_when_collecting(self):
        """目标数据集不存在时才创建，不覆盖已有数据集的描述或配置。"""
        self.client.api.datasets.get.side_effect = NotFoundError(body={})
        self.client.create_dataset.return_value = SimpleNamespace(id="new-dataset")
        result = self.collect()
        self.assertEqual(result["dataset_id"], "new-dataset")
        self.assertEqual(
            self.client.create_dataset.call_args.kwargs["name"], "pdf-qa-candidates"
        )

    def test_duplicate_never_overwrites_reviewed_or_unreviewed_item(self):
        """重复项无论是否审核均跳过，人工参考答案、候选答案和审核状态保持原值。"""
        for reviewed in (False, True):
            with self.subTest(reviewed=reviewed):
                self.client.reset_mock()
                existing = SimpleNamespace(
                    id="existing-item",
                    dataset_id="dataset-test",
                    expected_output="人工参考答案",
                    metadata={"reviewed": reviewed, "candidate_answer": "原始候选"},
                )
                self.client.api.dataset_items.get.side_effect = None
                self.client.api.dataset_items.get.return_value = existing
                result = self.collect(answer="后来产生的新答案")
                self.assertEqual(
                    result["dataset_collection_status"], "skipped_existing"
                )
                self.client.create_dataset_item.assert_not_called()
                self.client.create_dataset.assert_not_called()
                self.client.api.datasets.get.assert_not_called()
                self.assertEqual(existing.expected_output, "人工参考答案")
                self.assertEqual(
                    existing.metadata,
                    {"reviewed": reviewed, "candidate_answer": "原始候选"},
                )

    def test_lookup_failure_does_not_become_an_upsert(self):
        """权限或网络错误必须上报，不能误当不存在并覆盖已有记录。"""
        self.client.api.dataset_items.get.side_effect = RuntimeError("网络失败")
        with self.assertRaisesRegex(RuntimeError, "网络失败"):
            self.collect()
        self.client.create_dataset_item.assert_not_called()
        self.client.create_dataset.assert_not_called()


class PdfDatasetWorkflowTests(unittest.TestCase):
    """检查命令入口是否在实际委派和页码检查成功后才调用采集。"""

    def run_demo(
        self, directory, *, enabled=False, incomplete=False, collect_error=None
    ):
        """替换模型与 HTTP 边界，保留主流程检查和实际报告写入。"""
        report = Path(directory) / "report.md"
        arguments = ["pdf_demo.py", "--pdf", "provided.pdf", "--output", str(report)]
        if enabled:
            arguments.append("--collect-dataset")
        root = MagicMock()
        root.__enter__.return_value = root
        root.trace_id, root.id = "trace-test", "workflow-test"
        client = Mock()
        client.start_as_current_observation.return_value = root
        agent = Mock()
        agent.invoke.return_value = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "delegate",
                            "name": "task",
                            "args": {"subagent_type": "pdf-reader-agent"},
                        }
                    ],
                ),
                ToolMessage(content="阅读事实", tool_call_id="delegate"),
                AIMessage(content="已完成分析【第 1 页】"),
            ]
        }
        collection = Mock(
            return_value={
                "dataset_collection_status": "created",
                "dataset_id": "dataset-test",
                "dataset_item_id": "item-test",
            },
            side_effect=collect_error,
        )
        patches = {
            "build_pdf_tools": [],
            "prepare_pdf_attachment": SimpleNamespace(
                media=object(), sha256_hex="a" * 64
            ),
            "create_runtime": (object(), client),
            "build_pdf_agent": agent,
            "verify_pdf_attachment": {
                "pdf_media_id": "media-test",
                "pdf_object_path": "/test.pdf",
                "pdf_upload_status": "uploaded",
            },
            "get_public_trace_url": "http://localhost:3000/project/demo-project/traces/trace-test",
            "get_read_coverage": {
                "read_pages": [1],
                "incomplete_pages": [2] if incomplete else [],
                "ocr_pages": [],
                "page_count": 2 if incomplete else 1,
            },
        }
        with ExitStack() as stack:
            stack.enter_context(patch("sys.argv", arguments))
            stack.enter_context(patch("builtins.print"))
            stack.enter_context(patch("pdf_demo.collect_pdf_candidate", collection))
            for name, value in patches.items():
                stack.enter_context(patch("pdf_demo." + name, return_value=value))
            try:
                main()
            except RuntimeError:
                if not incomplete and collect_error is None:
                    raise
        return report, client, collection, root

    def test_success_requires_flag_and_persists_report(self):
        """同样的成功分析只有开启标记才采集，报告保存照常完成。"""
        for enabled in (False, True):
            with (
                self.subTest(enabled=enabled),
                tempfile.TemporaryDirectory() as directory,
            ):
                report, client, collection, _ = self.run_demo(
                    directory, enabled=enabled
                )
                self.assertTrue(report.exists())
                self.assertEqual(collection.call_count, 1 if enabled else 0)
                self.assertEqual(
                    client.start_as_current_observation.call_count, 2 if enabled else 1
                )
                if enabled:
                    self.assertEqual(
                        collection.call_args.kwargs["answer"], "已完成分析【第 1 页】"
                    )
                client.flush.assert_called_once()

    def test_incomplete_analysis_is_not_collected(self):
        """存在未读页面时，即使开启采集也不写入候选数据集。"""
        with tempfile.TemporaryDirectory() as directory:
            report, client, collection, _ = self.run_demo(
                directory, enabled=True, incomplete=True
            )
            collection.assert_not_called()
            self.assertFalse(report.exists())
            client.flush.assert_called_once()

    def test_collection_failure_preserves_report_and_marks_trace(self):
        """采集失败保留已经完成的报告，并在追踪中明确标记失败。"""
        with tempfile.TemporaryDirectory() as directory:
            report, client, collection, root = self.run_demo(
                directory, enabled=True, collect_error=RuntimeError("写入失败")
            )
            self.assertTrue(report.exists())
            collection.assert_called_once()
            failed = [
                call.kwargs
                for call in root.update.call_args_list
                if call.kwargs.get("level") == "ERROR"
            ]
            self.assertEqual(
                failed[0]["metadata"]["dataset_collection_status"], "failed"
            )
            client.flush.assert_called_once()


if __name__ == "__main__":
    unittest.main()
