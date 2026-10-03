"""Database-derived numbers. Neither completion_json nor Coach output is a source."""
from sqlalchemy import select
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.interview.artifacts import DIMENSIONS
from interview_api.modules.interview.models import InterviewAnswer, InterviewEvaluation, InterviewMessage, InterviewSessionQuestion


async def interview_records(db, session_id):
    questions = list((await db.scalars(select(InterviewSessionQuestion).where(
        InterviewSessionQuestion.session_id == session_id).order_by(InterviewSessionQuestion.question_index, InterviewSessionQuestion.id))).all())
    rows = (await db.execute(select(InterviewEvaluation, InterviewAnswer, InterviewMessage, InterviewSessionQuestion)
        .join(InterviewAnswer, InterviewAnswer.id == InterviewEvaluation.answer_id)
        .join(InterviewMessage, InterviewMessage.id == InterviewAnswer.message_id)
        .join(InterviewSessionQuestion, InterviewSessionQuestion.id == InterviewEvaluation.session_question_id)
        .where(InterviewEvaluation.session_id == session_id, InterviewAnswer.session_id == session_id,
               InterviewSessionQuestion.session_id == session_id, InterviewMessage.session_id == session_id,
               InterviewAnswer.status == "COMPLETED", InterviewSessionQuestion.status == "COMPLETED",
               InterviewAnswer.session_question_id == InterviewEvaluation.session_question_id)
        .order_by(InterviewEvaluation.id))).all()
    evaluations = [dict(id=e.id, evidence_ref=f"evaluation:{e.id}", session_question_id=q.id, question_id=q.id,
        answer_id=a.id, question=q.question, answer=m.content, evaluation=e.artifact_json.get("evaluation", ""),
        scores={key: getattr(e, key) for key in DIMENSIONS},
        covered_points=e.artifact_json.get("covered_points", []), missing_points=e.artifact_json.get("missing_points", []),
        issues=e.artifact_json.get("factual_or_reasoning_issues", []),
        factual_or_reasoning_issues=e.artifact_json.get("factual_or_reasoning_issues", []),
        follow_up_focus=e.artifact_json.get("follow_up_focus", []), provider=e.provider, model=e.model,
        source=q.source, question_kind=q.question_kind) for e, a, m, q in rows]
    return questions, evaluations


def deterministic_statistics(questions, evaluations):
    count = len(evaluations)
    means = {key: round(sum(e["scores"][key] for e in evaluations) / count, 4) if count else None for key in DIMENSIONS}
    return dict(answered_count=len({e["session_question_id"] for e in evaluations}), evaluated_count=count,
        skipped_count=sum(q.status == "SKIPPED" for q in questions),
        follow_up_count=sum(q.question_kind == "FOLLOW_UP" for q in questions),
        deep_dive_count=sum(q.question_kind == "DEEP_DIVE" for q in questions),
        dynamic_question_count=sum(q.is_dynamic for q in questions), dimension_averages=means,
        **{f"{key}_average": value for key, value in means.items()},
        overall_average=round(sum(sum(e["scores"].values()) for e in evaluations) / (count * len(DIMENSIONS)), 4) if count else None,
        total_questions=len(questions), provenance="PERSISTED_VALID_INTERVIEW_EVALUATIONS")


def evaluation_fingerprint(evaluations):
    return digest(sorted(evaluations, key=lambda e: e["id"]))
