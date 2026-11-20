"""按文件内容和问题收集 PDF 待审核案例，保留已存在的人工修改。"""

import json
from uuid import NAMESPACE_URL, uuid5

from langfuse import Langfuse
from langfuse.api import NotFoundError

from pdf_media import PdfAttachment

PDF_CANDIDATE_DATASET = "pdf-qa-candidates"


def get_candidate_item_id(pdf_sha256: str, question: str) -> str:
    """用数据集名、原件 SHA256 和原始问题生成稳定编号，避免重复录入。"""
    # JSON 数组避免字符串拼接分隔符与问题正文发生歧义。
    identity = json.dumps(
        [PDF_CANDIDATE_DATASET, pdf_sha256, question],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return str(uuid5(NAMESPACE_URL, identity))


def collect_pdf_candidate(
    langfuse: Langfuse,
    *,
    enabled: bool,
    attachment: PdfAttachment,
    filename: str,
    question: str,
    answer: str | list,
    trace_id: str,
    observation_id: str,
) -> dict:
    """显式启用时收集一条成功案例；重复项一律跳过，不回写参考答案和审核状态。"""
    if not enabled:
        return {"dataset_collection_status": "disabled"}

    item_id = get_candidate_item_id(attachment.sha256_hex, question)
    try:
        existing = langfuse.api.dataset_items.get(item_id)
    except NotFoundError:
        existing = None
    if existing is not None:
        return {
            "dataset_collection_status": "skipped_existing",
            "dataset_name": PDF_CANDIDATE_DATASET,
            "dataset_id": existing.dataset_id,
            "dataset_item_id": existing.id,
        }

    try:
        dataset = langfuse.api.datasets.get(PDF_CANDIDATE_DATASET)
    except NotFoundError:
        dataset = langfuse.create_dataset(
            name=PDF_CANDIDATE_DATASET,
            description="PDF Demo 自动收集的真实成功案例；候选答案存于 Metadata，参考答案待人工审核。",
        )

    # 使用官方媒体对象建立数据集附件关联；不能只存宿主机文件路径。
    item = langfuse.create_dataset_item(
        id=item_id,
        dataset_name=PDF_CANDIDATE_DATASET,
        input={
            "question": question,
            "filename": filename,
            "pdf": attachment.media,
        },
        expected_output=None,
        metadata={
            "candidate_answer": answer,
            "reviewed": False,
            "pdf_sha256": attachment.sha256_hex,
            "collection_source": "pdf-demo",
        },
        source_trace_id=trace_id,
        source_observation_id=observation_id,
    )
    return {
        "dataset_collection_status": "created",
        "dataset_name": PDF_CANDIDATE_DATASET,
        "dataset_id": dataset.id,
        "dataset_item_id": item.id,
    }
