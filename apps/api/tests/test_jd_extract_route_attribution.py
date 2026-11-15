"""The admission ledger must say which feature spent the tokens, for whom, where.

`governed_call` used to fall back to route `legacy_llm` and to the process-wide
session factory whenever no scope was set, so private-JD extraction was booked under
a generic bucket and a harness could bill the wrong database. The scope is now
explicit for every call, and a missing owner is a refusal rather than a guess.
"""
import asyncio

from interview_api.infrastructure.models.scope import current_scope
from interview_api.infrastructure.models.router import EXTRACT_ROUTE
from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse
from interview_api.modules.job_target.service import JobTargetService

JD = "岗位职责：负责交易系统的接口开发与维护。\n任职要求：必须熟悉 Python，至少两年后端开发经验。"


class _ScopeRecordingProvider(ModelProvider):
    """Records the attribution that was in force while the model call ran."""

    provider_name = "recording"
    model_name = "recording-model"
    timeout = 5.0
    max_output_tokens = 512

    def __init__(self):
        self.seen = []

    async def generate(self, messages, *, schema):
        self.seen.append(current_scope())
        import json
        return ModelResponse(json.dumps({
            "company_name": None, "job_title": "交易系统工程师",
            "responsibilities": [], "requirements": [
                {"statement": "必须熟悉 Python", "importance": "REQUIRED",
                 "evidence": "必须熟悉 Python", "topic": "Python"}]}, ensure_ascii=False),
            {"total_tokens": 10})


def _service_with(provider):
    from interview_api.infrastructure.models.router import ModelRouter

    return JobTargetService(None, router=ModelRouter({EXTRACT_ROUTE: provider}))


def test_extract_books_the_spend_under_its_own_route_and_owner():
    provider = _ScopeRecordingProvider()
    service = _service_with(provider)
    parsed, mode, error = asyncio.run(service.extract(JD, None, None, user_id=7))
    assert mode == "LLM" and error is None and parsed["requirements"]
    scope = provider.seen[0]
    assert scope is not None
    assert scope.route == EXTRACT_ROUTE and scope.owner == "user:7" and scope.user_id == 7
    assert scope.ledger_database, "the scope must name the database it will bill"
    # The scope must not leak past the call.
    assert current_scope() is None


def test_extract_without_a_user_is_an_explicit_system_task():
    provider = _ScopeRecordingProvider()
    service = _service_with(provider)
    asyncio.run(service.extract(JD, None, None))
    scope = provider.seen[0]
    assert scope is not None
    assert scope.owner == "system:job-target-extract" and scope.user_id is None
    assert scope.route == EXTRACT_ROUTE
    assert current_scope() is None
