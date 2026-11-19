"""当前 PDF 示例自定义的文件信息和逐页读取工具。"""

import json
from pathlib import Path

from langchain_core.tools import BaseTool, tool
from pypdf import PdfReader

# 单次返回有上限，长页通过 next_offset 继续读取，避免静默截断。
PAGE_CHUNK_CHARS = 12000


def build_pdf_tools(
    pdf_path: Path, read_events: list[dict] | None = None
) -> list[BaseTool]:
    """绑定指定 PDF；可选的 read_events 记录子代理真实读取结果。"""
    pdf_path = pdf_path.expanduser().resolve()
    if not pdf_path.is_file():
        raise ValueError(f"PDF 文件不存在：{pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("请提供 .pdf 文件")
    with pdf_path.open("rb") as stream:
        reader = PdfReader(stream)
        if reader.is_encrypted:
            raise ValueError("本示例不读取加密 PDF，请先提供解密后的文件")
        page_count = len(reader.pages)
        if not page_count:
            raise ValueError("PDF 没有页面")
        title = str((reader.metadata or {}).get("/Title") or "")

    @tool
    def get_pdf_info() -> str:
        """获取当前 PDF 的文件名、标题和总页数；分析前先调用此工具。"""
        return json.dumps(
            {"filename": pdf_path.name, "title": title, "page_count": page_count},
            ensure_ascii=False,
        )

    @tool
    def read_pdf_page(page_number: int, offset: int = 0) -> str:
        """读取当前 PDF 指定页的文字；页码从 1 开始，长页使用 next_offset 继续。"""
        if page_number < 1 or page_number > page_count:
            raise ValueError(f"页码必须在 1 到 {page_count} 之间")
        if offset < 0:
            raise ValueError("字符偏移 offset 不能小于 0")
        # 每次读取使用独立文件句柄，允许 Agent 并行读取不同页。
        with pdf_path.open("rb") as stream:
            reader = PdfReader(stream)
            text = (
                reader.pages[page_number - 1].extract_text(extraction_mode="layout")
                or ""
            ).strip()
        if offset > len(text):
            raise ValueError(f"字符偏移超过该页长度 {len(text)}")
        end = min(offset + PAGE_CHUNK_CHARS, len(text))
        result = {
            "filename": pdf_path.name,
            "page_number": page_number,
            "page_count": page_count,
            "total_chars": len(text),
            "offset": offset,
            "next_offset": end if end < len(text) else None,
            "text": text[offset:end],
            "requires_ocr": not bool(text),
        }
        if read_events is not None:
            read_events.append(result)
        return json.dumps(result, ensure_ascii=False)

    return [get_pdf_info, read_pdf_page]
