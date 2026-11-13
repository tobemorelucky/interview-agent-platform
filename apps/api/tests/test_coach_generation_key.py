"""What a Coach report's identity is made of, and why the harness needed more.

The round-13 harness added tool policy and a report-time capability capture, but
the generation key still described only the session snapshot, the evaluations
and the prompt. Two consequences, both silent:

* turning the tool budget down (or changing a tool's contract) reused a report
  written under the old rules;
* memory changing after a report was written reused a report whose training
  suggestion had been validated against different capability states.

These tests pin the fixed formula. The first one is the important one: OFF must
reproduce the historical key byte for byte, otherwise every existing completed
report stops matching itself and a page load displaces it.
"""
import asyncio


from interview_api.core.config import settings
from interview_api.infrastructure.models.provider import ReplayModelProvider
from interview_api.infrastructure.models.router import INTERVIEW_COACH_ROUTE, ModelRouter
from interview_api.modules.candidate_profile.evidence import digest
from interview_api.modules.interview.models import InterviewContextSnapshot
from interview_api.modules.interview_report import tools
from interview_api.modules.interview_report.models import FinalInterviewReport
from interview_api.modules.interview_report.prompts import COACH_PROMPT, coach_prompt_signature
from interview_api.modules.interview_report.service import InterviewReportService, coach_generation_key

from test_coach_harness import adaptive_completed, coach_settings, offer_only, refresh_memory, response_for

SESSION = 41
CONTENT = "a" * 64
EVALS = "b" * 64


def legacy_key(request_key=None):
    """The formula exactly as it was before this round, written out by hand."""
    return digest(["interview_coach", SESSION, CONTENT, EVALS,
                   COACH_PROMPT.version, COACH_PROMPT.content_hash, request_key])


def test_off_reproduces_the_historical_key():
    assert coach_generation_key(session_id=SESSION, content_fingerprint=CONTENT, evaluation_fp=EVALS,
        prompt_version=COACH_PROMPT.version, prompt_hash=COACH_PROMPT.content_hash) == legacy_key()


def test_off_ignores_harness_only_inputs():
    """Passing them must not change an OFF key; only the flag may."""
    with_extra = coach_generation_key(session_id=SESSION, content_fingerprint=CONTENT, evaluation_fp=EVALS,
        prompt_version=COACH_PROMPT.version, prompt_hash=COACH_PROMPT.content_hash,
        policy="p" * 64, capability_fingerprint="c" * 64)
    assert with_extra == legacy_key()


def test_on_and_off_never_share_a_key():
    off = coach_generation_key(session_id=SESSION, content_fingerprint=CONTENT, evaluation_fp=EVALS,
        prompt_version="1.0.3", prompt_hash="h")
    on = coach_generation_key(session_id=SESSION, content_fingerprint=CONTENT, evaluation_fp=EVALS,
        prompt_version="1.1.0", prompt_hash="h", harness=True, policy="p", capability_fingerprint="c")
    assert off != on


def test_on_key_moves_when_the_capability_capture_moves():
    first = coach_generation_key(session_id=SESSION, content_fingerprint=CONTENT, evaluation_fp=EVALS,
        prompt_version="1.1.0", prompt_hash="h", harness=True, policy="p", capability_fingerprint="c1")
    second = coach_generation_key(session_id=SESSION, content_fingerprint=CONTENT, evaluation_fp=EVALS,
        prompt_version="1.1.0", prompt_hash="h", harness=True, policy="p", capability_fingerprint="c2")
    assert first != second, "new memory must not resolve to the report written against old memory"


def test_on_key_moves_when_the_tool_policy_moves():
    first = coach_generation_key(session_id=SESSION, content_fingerprint=CONTENT, evaluation_fp=EVALS,
        prompt_version="1.1.0", prompt_hash="h", harness=True, policy="p1", capability_fingerprint="c")
    second = coach_generation_key(session_id=SESSION, content_fingerprint=CONTENT, evaluation_fp=EVALS,
        prompt_version="1.1.0", prompt_hash="h", harness=True, policy="p2", capability_fingerprint="c")
    assert first != second


def test_policy_signature_tracks_the_configured_limits():
    original = tools.policy_signature()
    assert tools.policy_signature() == original, "must be stable for the same configuration"
    from unittest.mock import patch
    with patch.object(settings, "interview_coach_tool_limit", 0):
        assert tools.policy_signature() != original
    with patch.object(settings, "interview_coach_model_limit", 1):
        assert tools.policy_signature() != original


def run(database_url, scenario):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def main():
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await scenario(factory)
        finally:
            await engine.dispose()
    asyncio.run(main())


def test_the_stored_off_key_is_the_legacy_formula(database_url):
    """End to end: the row the OFF path writes matches the historical identity."""
    async def scenario(factory):
        user_id, session_id, evaluations = await adaptive_completed(factory)
        provider = ReplayModelProvider([response_for([e["id"] for e in evaluations])])
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
        with coach_settings(harness=False):
            report = await service.generate(session_id, user_id)
        row, content = await stored_report(factory, report["id"])
        assert row.prompt_version == COACH_PROMPT.version
        assert row.generation_key == digest(["interview_coach", session_id, content,
            row.evaluation_fingerprint, COACH_PROMPT.version, COACH_PROMPT.content_hash, None])
    run(database_url, scenario)


def test_the_stored_on_key_carries_policy_and_capture(database_url):
    """ON: the row's key is the formula including both harness-only inputs."""
    async def scenario(factory):
        user_id, session_id, evaluations = await adaptive_completed(factory)
        await refresh_memory(factory, user_id)
        provider = ReplayModelProvider([response_for([e["id"] for e in evaluations])])
        service = InterviewReportService(factory, ModelRouter({INTERVIEW_COACH_ROUTE: provider}))
        with coach_settings(harness=True), offer_only():
            report = await service.generate(session_id, user_id)
        audit = report["coach_analysis"]["audit"]
        assert audit["capability_capture"], "this fixture has a refreshed memory to capture"
        version, content_hash = coach_prompt_signature(True)
        row, content = await stored_report(factory, report["id"])
        assert row.prompt_version == version
        assert row.generation_key == coach_generation_key(session_id=session_id,
            content_fingerprint=content, evaluation_fp=row.evaluation_fingerprint,
            prompt_version=version, prompt_hash=content_hash, harness=True,
            policy=tools.policy_signature(), capability_fingerprint=audit["capability_capture"])
    run(database_url, scenario)


async def stored_report(factory, report_id):
    """(report row, its snapshot's content fingerprint)."""
    async with factory() as db:
        row = await db.get(FinalInterviewReport, report_id)
        snapshot = await db.get(InterviewContextSnapshot, row.context_snapshot_id)
        return row, snapshot.content_fingerprint
