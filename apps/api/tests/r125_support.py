"""R125 scripted-interview harness: real source, real gate, no HTTP.

The point of these conversations is *context continuity*: every turn is decided
from the payload the running session actually produced (history, the previous
answer, the evaluation's open gaps, the remaining strategies), and every
question goes through the product's own ``validate_decision`` +
``SessionController``. Only the model text is scripted, exactly like the
existing Replay providers — nothing bypasses the contract.
"""
import json
from uuid import uuid4

import interview_api.modules.models  # noqa: F401  (register every mapper)
from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse
from interview_api.modules.candidate_profile.agent import ResumeProfileAgent
from interview_api.modules.candidate_profile.evidence import evidence_rows
from interview_api.modules.experience.models import InterviewExperience, InterviewQuestion
from interview_api.modules.public_career.models import Company, Job, JobDescriptionVersion, JobRequirement
from test_m3_candidate_profile import seed as seed_resume, model_router as resume_router

DIMENSIONS = ("technical_correctness", "coverage", "depth", "relevance", "communication")


def _decode(payload):
    """Explode the shared target/source dictionaries into the documented DTO shape."""
    adaptive = payload.get("adaptive") or {}
    if adaptive.get("targets"):
        adaptive["candidates"] = [
            dict(candidate, label=adaptive["targets"][candidate["target_knowledge_id"]]["label"],
                 topic=adaptive["targets"][candidate["target_knowledge_id"]]["topic"],
                 source=adaptive["sources"][candidate["source"]["ref"]])
            for candidate in adaptive["candidates"]]
    return payload


class ScriptedInterviewer(ModelProvider):
    """Speak the planned line for this turn, but only through a legal candidate."""

    provider_name, model_name = "replay", "R125_SCRIPTED_INTERVIEWER"

    def __init__(self, plan):
        self.plan = list(plan)
        self.calls = []
        self.made = []

    def _pick(self, adaptive, step):
        candidates = adaptive["candidates"]
        topic = step.get("topic")
        for candidate in candidates:
            if topic and candidate["topic"] != topic:
                continue
            if step["strategy"] not in candidate["strategies"]:
                continue
            if step.get("source") and candidate["source"]["source_type"] != step["source"]:
                continue
            return candidate
        raise AssertionError(f"no legal candidate for step {step}: "
                             f"{[(c['topic'], c['source']['source_type'], c['strategies']) for c in candidates]}")

    async def generate(self, messages, *, schema):
        payload = _decode(json.loads(messages[-1]["content"])["input"])
        title = schema["title"]
        self.calls.append(title)
        assert title in ("AdaptiveSelection", "AdaptiveDecision"), title
        assert len(self.made) < len(self.plan), (
            f"the session asked for turn {len(self.made) + 1} but the script only has {len(self.plan)} turns")
        step = self.plan[len(self.made)]
        candidate = self._pick(payload["adaptive"], step)
        text = step["text"](payload) if callable(step["text"]) else step["text"]
        # The server re-derives FOLLOW_UP / DEEP_DIVE provenance; the artifact must
        # still name a real selected source so the pair contract can resolve it.
        provenance = candidate["source"]["ref"]
        value = dict(decision_version="2.1.0", strategy=step["strategy"],
                     target_knowledge_id=candidate["target_knowledge_id"],
                     question_source_ref=provenance,
                     supporting_refs=[payload["adaptive"]["current_answer_ref"] or provenance]
                     if payload["adaptive"].get("current_answer_ref") else [provenance],
                     reason_tags=[step.get("reason", "COVERAGE_SWITCH" if payload.get("history") else "OPENING")],
                     proposed_question=dict(question=text, expected_points=step.get("expected_points") or ["机制", "实际做法"],
                                            dimension=candidate["label"], difficulty=payload["desired_difficulty"]))
        if "candidate_id" in schema.get("properties", {}):
            from v21_support import selection_artifact
            value = selection_artifact(value, payload, scoped="focus_gap_ids" in schema.get("properties", {}))
        self.made.append(dict(step=step, text=text, topic=candidate["topic"],
                              source=candidate["source"]["source_type"], strategy=step["strategy"]))
        return ModelResponse(json.dumps(value, ensure_ascii=False),
                             dict(input_tokens=0, output_tokens=0, total_tokens=0))


class ScriptedEvaluation(ModelProvider):
    """Grade from the answer text; shallow answers leave a real open gap."""

    provider_name, model_name = "replay", "R125_SCRIPTED_EVALUATION"

    def __init__(self, rules):
        self.rules = list(rules)
        self.calls = []
        self.rows = []

    async def generate(self, messages, *, schema):
        payload = _decode(json.loads(messages[-1]["content"])["input"])
        assert schema["title"] == "AdaptiveEvaluation", schema["title"]
        self.calls.append(schema["title"])
        answer = payload["answer"]
        rule = next((r for r in self.rules if r["match"] in answer), self.rules[-1])
        kid = payload["adaptive"]["active_knowledge_id"]
        gaps = {g["id"] for g in payload["adaptive"]["knowledge_state"]["items"].get(kid, {}).get("open_gaps", [])}
        focus = payload["adaptive"].get("focus_gap_ids") or []
        quote = rule.get("quote") or answer[:60]
        state = rule["state"]
        resolved = [gap for gap in gaps if gap in focus] if state != "INSUFFICIENT_EVIDENCE" else []
        value = dict(**{key: rule["score"] for key in DIMENSIONS}, evaluation=rule["evaluation"],
                     covered_points=rule.get("covered", []), missing_points=rule.get("missing", []),
                     factual_or_reasoning_issues=[], follow_up_focus=rule.get("missing", []),
                     next_action_recommendation="NEXT", reference_answer=None,
                     knowledge_observations=[dict(knowledge_id=kid, state=state,
                         answer_quotes=[] if state == "INSUFFICIENT_EVIDENCE" else [quote],
                         missing_mechanisms=rule.get("missing_mechanisms", []) if state != "DEMONSTRATED_IN_SESSION" else [],
                         resolved_gap_ids=resolved)])
        self.rows.append(dict(answer=answer, state=state, score=rule["score"]))
        return ModelResponse(json.dumps(value, ensure_ascii=False),
                             dict(input_tokens=0, output_tokens=0, total_tokens=0))


async def seed_persona(factory, raw, facts):
    """A candidate whose profile is generated by the real ResumeProfileAgent.

    ``facts`` maps a profile category to ``(label, evidence fragment)`` pairs; the
    persisted statement is the real resume excerpt, never model-authored prose.
    """
    user_id, resume_id = await seed_resume(factory, raw)
    rows = evidence_rows(resume_id, raw)

    def ref(fragment):
        return "E%02d" % (next(index for index, row in enumerate(rows, 1) if fragment in row["excerpt"]))

    body = {category: [dict(label=label, evidence_ids=[ref(fragment)]) for label, fragment in items]
            for category, items in facts.items()}
    body["unknowns"] = []
    router, _ = resume_router([body])
    profile = await ResumeProfileAgent(factory, router).run(resume_id)
    assert profile["status"] == "COMPLETED", profile
    return user_id


async def seed_job(factory, title, statements, family=None):
    async with factory() as db:
        company = Company(canonical_name="R125 脚本面试 " + uuid4().hex)
        db.add(company)
        await db.flush()
        job = Job(company_id=company.id, canonical_title=title, job_family=family)
        db.add(job)
        await db.flush()
        jd = JobDescriptionVersion(job_id=job.id, version_number=1,
                                   raw_text="R125 脚本面试 JD：" + "；".join(statements),
                                   publication_status="PUBLISHED")
        db.add(jd)
        await db.flush()
        for statement in statements:
            db.add(JobRequirement(job_description_version_id=jd.id, statement=statement,
                                  requirement_type="SKILL", normalized_value=None))
        experience = InterviewExperience(company_id=company.id, job_id=job.id, review_status="APPROVED",
                                         summary="R125 fixture", content_text="R125 fixture content")
        db.add(experience)
        await db.flush()
        db.add(InterviewQuestion(company_id=company.id, job_id=job.id, experience_id=experience.id,
                                 question="R125 公共题占位：请说明一个技术决策的取舍",
                                 standard_answer="取舍\n边界", review_status="APPROVED",
                                 category="工程", difficulty="MEDIUM"))
        await db.commit()
        return dict(company_id=company.id, job_id=job.id)


async def converse(core, user_id, session_id, answers):
    """Drive one continuous session; return the transcript with real decisions."""
    first = await core.start(session_id, user_id)
    transcript = [dict(turn=1, role="interviewer", text=first["question"],
                       kind="OPENING", question_id=first["question_id"])]
    question_id = first["question_id"]
    for index, answer in enumerate(answers):
        events = dict(await core.answer(session_id, user_id, question_id, uuid4().hex, answer))
        evaluation = events["evaluation"]
        transcript.append(dict(turn=index + 1, role="candidate", text=answer,
                               score_in_review=evaluation["score"], action=evaluation["action"]))
        if "complete" in events:
            transcript.append(dict(turn=index + 2, role="system", kind="COMPLETE",
                                   text=events["complete"].get("completion_reason")))
            break
        next_question = events.get("follow_up") or events.get("question")
        transcript.append(dict(turn=index + 2, role="interviewer", text=next_question["question"],
                               kind="FOLLOW_UP" if "follow_up" in events else "QUESTION",
                               question_id=next_question["question_id"]))
        question_id = next_question["question_id"]
    return transcript


def spoken(transcript):
    return [f"{row['role']}: {row['text']}" for row in transcript]
