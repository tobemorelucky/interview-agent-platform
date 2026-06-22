"""Deterministic validation for Extraction Agent outputs."""

from dataclasses import dataclass, field

from interview_api.modules.experience.agents.schemas import (
    AnswerSource,
    ExtractionExperience,
    QuestionType,
)

ANSWER_SOURCES = set(AnswerSource.__args__)
QUESTION_TYPES = set(QuestionType.__args__)
NEGATIVE_TERMS = {
    "公务员",
    "事业单位",
    "法官助理",
    "考试录用",
    "招聘公告",
    "课程推广",
    "帮助中心",
    "使用手册",
}
INTERVIEW_TERMS = {
    "面经",
    "面试",
    "一面",
    "二面",
    "三面",
    "笔试",
    "技术面",
    "offer",
}


@dataclass
class ExtractionValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_extraction_result(
    result: ExtractionExperience,
    raw_text: str,
) -> ExtractionValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if not 0 <= result.extraction_confidence <= 1:
        errors.append("extraction_confidence_out_of_range")

    content_for_negative_check = (
        f"{result.experience_summary}\n{raw_text[:1000]}"
    )
    if result.is_interview_experience:
        has_interview_signal = any(term in content_for_negative_check for term in INTERVIEW_TERMS)
        has_negative_signal = any(term in content_for_negative_check for term in NEGATIVE_TERMS)
        if has_negative_signal and not has_interview_signal:
            errors.append("unrelated_or_negative_content")
        if not result.experience_summary.strip():
            errors.append("missing_experience_summary")
        if not result.questions:
            errors.append("missing_questions_for_interview_experience")
    elif result.questions:
        warnings.append("questions_present_for_non_interview_experience")

    for index, question in enumerate(result.questions):
        prefix = f"questions[{index}]"
        q_text = question.question.strip()
        if len(q_text) < 5 or len(q_text) > 200:
            errors.append(f"{prefix}.question_length_invalid")
        if question.question_type and question.question_type not in QUESTION_TYPES:
            errors.append(f"{prefix}.question_type_invalid")
        if question.answer_source not in ANSWER_SOURCES:
            errors.append(f"{prefix}.answer_source_invalid")
        if not 0 <= question.confidence <= 1:
            errors.append(f"{prefix}.confidence_out_of_range")
        if question.evidence and len(question.evidence) > 500:
            errors.append(f"{prefix}.evidence_too_long")
        if question.answer_source == "ORIGINAL" and not question.original_answer:
            warnings.append(f"{prefix}.original_answer_missing_for_original_source")
        if question.answer_source == "LLM_GENERATED" and not question.standard_answer:
            warnings.append(f"{prefix}.standard_answer_missing_for_llm_generated_source")

    return ExtractionValidationResult(is_valid=not errors, errors=errors, warnings=warnings)
