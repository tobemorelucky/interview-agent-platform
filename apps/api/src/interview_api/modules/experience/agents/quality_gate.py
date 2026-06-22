"""Deterministic quality gate for extracted interview experiences."""

from __future__ import annotations

from interview_api.modules.experience.agents.schemas import (
    ExtractionExperience,
    QualityGateResult,
    ReliabilityResult,
    RoutingResult,
)


MIN_RAW_TEXT_CHARS_FOR_REVIEW = 300


def evaluate_quality_gate(
    *,
    extraction: ExtractionExperience | None,
    routing: RoutingResult | None,
    reliability: ReliabilityResult | None,
    raw_text: str,
) -> QualityGateResult:
    reasons: list[str] = []
    question_count = len(extraction.questions) if extraction else 0
    reliability_score = reliability.reliability_score if reliability else 0.0
    spam_risk_score = reliability.spam_risk_score if reliability else 0.0
    source_quality_score = reliability.source_quality_score if reliability else 0.0
    routing_confidence = routing.routing_confidence if routing else 0.0
    indexable_question_count = _count_indexable_questions(question_count, routing)

    if not extraction or not extraction.is_interview_experience:
        reasons.append("non_interview_experience")
    if question_count == 0:
        reasons.append("no_questions")
    if len(raw_text.strip()) < MIN_RAW_TEXT_CHARS_FOR_REVIEW:
        reasons.append("raw_text_too_short")
    if reliability_score < 0.35:
        reasons.append("reliability_below_reject_threshold")
    if spam_risk_score > 0.8:
        reasons.append("spam_risk_too_high")
    if reliability and reliability.ad_or_training_risk and question_count == 0:
        reasons.append("ad_or_training_without_questions")

    reject_reasons = {
        "non_interview_experience",
        "no_questions",
        "raw_text_too_short",
        "reliability_below_reject_threshold",
        "spam_risk_too_high",
        "ad_or_training_without_questions",
    }
    if any(reason in reject_reasons for reason in reasons):
        return QualityGateResult(
            passed=False,
            review_status="REJECTED",
            reasons=reasons,
            question_count=question_count,
            indexable_question_count=indexable_question_count,
            reliability_score=reliability_score,
            recommended_action="REJECT",
        )

    if reliability_score < 0.6:
        reasons.append("reliability_needs_manual_check")
    if question_count < 2:
        reasons.append("few_questions")
    if _generated_answer_count(extraction) >= max(2, question_count):
        reasons.append("many_generated_answers")
    if routing_confidence < 0.5:
        reasons.append("routing_confidence_low")
    if source_quality_score < 0.5:
        reasons.append("source_quality_low")
    if indexable_question_count == 0:
        reasons.append("no_indexable_questions")

    if reasons:
        return QualityGateResult(
            passed=False,
            review_status="NEEDS_MANUAL_CHECK",
            reasons=reasons,
            question_count=question_count,
            indexable_question_count=indexable_question_count,
            reliability_score=reliability_score,
            recommended_action="REVIEW",
        )

    return QualityGateResult(
        passed=True,
        review_status="WAITING_REVIEW",
        reasons=["ready_for_admin_review"],
        question_count=question_count,
        indexable_question_count=indexable_question_count,
        reliability_score=reliability_score,
        recommended_action="REVIEW",
    )


def _count_indexable_questions(question_count: int, routing: RoutingResult | None) -> int:
    if not routing or not routing.question_results:
        return question_count
    return sum(1 for item in routing.question_results if item.should_index)


def _generated_answer_count(extraction: ExtractionExperience | None) -> int:
    if not extraction:
        return 0
    return sum(
        1
        for question in extraction.questions
        if question.answer_source in {"LLM_GENERATED", "HYBRID"}
    )
