"""验证实际子代理委派、逐页覆盖和用户提供的 PDF 输入。"""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from pdf_demo import PDF_READER_NAME, build_pdf_agent, get_read_coverage
from pdf_tools import build_pdf_tools


class FixedToolModel(FakeMessagesListChatModel):
    """给流程测试提供固定工具请求，不连接模型服务。"""

    def bind_tools(self, tools, **kwargs):
        """工具请求已固定，保留此模型供真实 Agent 图调用。"""
        return self


class PdfWorkflowTests(unittest.TestCase):
    """验证主代理实际执行 task，工具由子代理执行且消息相互隔离。"""

    def test_task_runs_reader_and_returns_to_main(self):
        """实际运行 DeepAgents 图，不生成 PDF，也不调用外部模型。"""
        events = []

        @tool
        def get_pdf_info() -> str:
            """流程测试的内存工具，返回两页文档信息。"""
            return json.dumps({"page_count": 2, "filename": "provided.pdf"})

        @tool
        def read_pdf_page(page_number: int, offset: int = 0) -> str:
            """流程测试的内存工具，记录子代理的实际工具执行。"""
            result = {
                "page_count": 2,
                "page_number": page_number,
                "total_chars": 2,
                "offset": offset,
                "text": "正文",
                "next_offset": None,
                "requires_ocr": False,
            }
            events.append(result)
            return json.dumps(result)

        main_model = FixedToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "description": "阅读全部页面并带页码返回事实",
                                "subagent_type": PDF_READER_NAME,
                            },
                            "id": "delegate-reader",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="主代理汇总分析【第 1 页】【第 2 页】"),
            ]
        )
        reader_model = FixedToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_pdf_info",
                            "args": {},
                            "id": "info",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_pdf_page",
                            "args": {"page_number": 1},
                            "id": "page-1",
                            "type": "tool_call",
                        },
                        {
                            "name": "read_pdf_page",
                            "args": {"page_number": 2},
                            "id": "page-2",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="阅读事实【第 1 页】【第 2 页】"),
            ]
        )
        agent = build_pdf_agent(main_model, [get_pdf_info, read_pdf_page], reader_model)
        result = agent.invoke(
            {"messages": [{"role": "user", "content": "分析提供的 PDF"}]},
            config={"recursion_limit": 60},
        )
        self.assertEqual(
            result["messages"][-1].content, "主代理汇总分析【第 1 页】【第 2 页】"
        )
        task_results = [
            message
            for message in result["messages"]
            if isinstance(message, ToolMessage)
            and message.tool_call_id == "delegate-reader"
        ]
        self.assertEqual(len(task_results), 1)
        self.assertIn("阅读事实", task_results[0].content)
        self.assertEqual(get_read_coverage(events)["read_pages"], [1, 2])
        # 子代理内部读取消息不直接塞给主代理，主代理只接收 task 的总结。
        self.assertFalse(
            any(
                isinstance(message, ToolMessage) and message.name == "read_pdf_page"
                for message in result["messages"]
            )
        )

    def test_read_coverage_detects_gaps_and_ocr(self):
        """读取长页中间有缺口时，不应被判断成完整覆盖。"""
        events = [
            {
                "page_count": 3,
                "page_number": 1,
                "offset": 0,
                "text": "ab",
                "total_chars": 4,
                "requires_ocr": False,
            },
            {
                "page_count": 3,
                "page_number": 1,
                "offset": 2,
                "text": "cd",
                "total_chars": 4,
                "requires_ocr": False,
            },
            {
                "page_count": 3,
                "page_number": 2,
                "offset": 2,
                "text": "cd",
                "total_chars": 4,
                "requires_ocr": False,
            },
            {
                "page_count": 3,
                "page_number": 3,
                "offset": 0,
                "text": "",
                "total_chars": 0,
                "requires_ocr": True,
            },
        ]
        coverage = get_read_coverage(events)
        self.assertEqual(coverage["read_pages"], [1])
        self.assertEqual(coverage["incomplete_pages"], [2])
        self.assertEqual(coverage["ocr_pages"], [3])

    def test_missing_read_and_file_are_rejected(self):
        """没有执行读取或输入文件缺失时必须报错。"""
        with self.assertRaisesRegex(RuntimeError, "没有实际读取"):
            get_read_coverage([])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "不存在"):
                build_pdf_tools(Path(directory) / "missing.pdf")


@unittest.skipUnless(
    os.getenv("PDF_TEST_FILE"), "需要 PDF_TEST_FILE 指向用户提供的 PDF"
)
class ProvidedPdfTests(unittest.TestCase):
    """在收到真实文件后，使用该文件核对读取和长页分页。"""

    def setUp(self):
        """工具只绑定用户提供的文件。"""
        self.tools = {
            item.name: item
            for item in build_pdf_tools(Path(os.environ["PDF_TEST_FILE"]))
        }

    def test_metadata_and_page_bounds(self):
        """真实文档应能读出页数，非法页码和偏移应被拒绝。"""
        info = json.loads(self.tools["get_pdf_info"].invoke({}))
        self.assertGreater(info["page_count"], 0)
        first = json.loads(self.tools["read_pdf_page"].invoke({"page_number": 1}))
        self.assertEqual(first["page_number"], 1)
        for arguments in (
            {"page_number": 0},
            {"page_number": info["page_count"] + 1},
            {"page_number": 1, "offset": -1},
        ):
            with self.assertRaises(ValueError):
                self.tools["read_pdf_page"].invoke(arguments)

    def test_long_page_can_be_read_without_loss(self):
        """多次读取 next_offset 后恢复真实文档第一页的全部文字。"""
        chunks, offset = [], 0
        with patch("pdf_tools.PAGE_CHUNK_CHARS", 100):
            while True:
                page = json.loads(
                    self.tools["read_pdf_page"].invoke(
                        {"page_number": 1, "offset": offset}
                    )
                )
                self.assertLessEqual(len(page["text"]), 100)
                chunks.append(page["text"])
                if page["next_offset"] is None:
                    break
                self.assertGreater(page["next_offset"], offset)
                offset = page["next_offset"]
        self.assertEqual(len("".join(chunks)), page["total_chars"])


if __name__ == "__main__":
    unittest.main()
