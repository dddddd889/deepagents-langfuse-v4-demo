"""运行带只读取证工具的评估，并保存 Opik 中的真实反馈分数。"""

import json


def evaluate_full_trace(opener, base_url, project_id, trace_id, model_name, timeout=300, *, project_name):
    """当前自定义 provider 的原生裁判没有取证工具，默认改用项目内的只读评估 Agent。"""
    from evidence_judge import evaluate_with_evidence
    return evaluate_with_evidence(opener, base_url, project_id, project_name, trace_id, model_name, timeout)


def append_evaluation_report(path, result, project_url):
    """将实际评分及原生评分调用链链接附在同一份本地报告。"""
    result_path = path.with_suffix(".evaluation.json")
    if result_path.exists() and result.get("evaluation_mode") == "read_only_evidence_agent":
        previous = json.loads(result_path.read_text(encoding="utf-8"))
        if previous.get("evaluation_mode") != "read_only_evidence_agent":
            path.with_suffix(".native-evaluation.json").write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8")
            text = path.read_text(encoding="utf-8").replace("## Opik 原生评估结果", "## 原生初评（只读到根记录，不能作为完整取证结论）")
            path.write_text(text, encoding="utf-8")
    lines = ["\n## 带只读取证工具的评估结果\n"]
    for score in result["scores"]:
        lines.append(f"- **{score['name']} = {score['value']}**：{score.get('reason', '')}\n")
    for trace_id in result["judge_trace_ids"]:
        lines.append(f"- 评分调用链：{project_url}/logs?logsType=traces&trace={trace_id}&time_range=past30days\n")
    with path.open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
