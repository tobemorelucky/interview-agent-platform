"""Prompts for the Phase 4 Step 7A Extraction Agent."""

import json


EXTRACTION_SYSTEM_PROMPT = """你是面试经验结构化抽取 Agent。

你的任务：
1. 判断输入内容是否为真实面试经验。
2. 抽取公司、岗位、轮次、面经摘要。
3. 抽取所有明确面试问题。
4. 原文有答案时保留 original_answer。
5. 原文没有答案时可以补充 standard_answer，但 answer_source 必须标记为 LLM_GENERATED。
6. 标记 answer_source：ORIGINAL / LLM_GENERATED / HYBRID / NONE。
7. evidence 必须尽量是 raw_text 中的短片段，不要复制整段 raw_text。
8. 不要编造公司和岗位，不确定就填 null。
9. 不要把广告、招聘公告、课程推广、帮助文档当成面经。
10. 如果 raw_text 过短或内容无关，返回 is_interview_experience=false，questions=[]。

只输出一个 JSON 对象，不要输出 Markdown，不要解释。"""


def build_extraction_prompt(
    *,
    title: str | None,
    url: str,
    snippet: str | None,
    raw_text: str,
) -> str:
    schema_hint = {
        "is_interview_experience": True,
        "company": "公司名或 null",
        "position": "岗位或 null",
        "round_name": "轮次或 null",
        "experience_summary": "简短面经摘要",
        "questions": [
            {
                "question": "面试问题",
                "question_type": "TECHNICAL/PROJECT/ALGORITHM/SYSTEM_DESIGN/HR/OTHER 或 null",
                "original_answer": "原文答案或 null",
                "standard_answer": "标准答案或 null",
                "answer_source": "ORIGINAL/LLM_GENERATED/HYBRID/NONE",
                "evidence": "raw_text 中的短证据片段或 null",
                "confidence": 0.8,
            }
        ],
        "source_quality_note": "质量说明或 null",
        "extraction_confidence": 0.8,
    }
    clipped_text = raw_text[:12000]
    return (
        "请从下面网页正文中抽取面试经验结构化信息。\n\n"
        f"title: {title or ''}\n"
        f"url: {url}\n"
        f"snippet: {snippet or ''}\n\n"
        "输出 JSON schema 示例：\n"
        f"{json.dumps(schema_hint, ensure_ascii=False, indent=2)}\n\n"
        "raw_text:\n"
        f"{clipped_text}"
    )
