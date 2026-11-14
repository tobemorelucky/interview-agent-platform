"""The admission ledger must say which feature spent the tokens.

`governed_call` falls back to route `legacy_llm` when no `model_scope` is set,
which is exactly what happened to private-JD extraction: every parse was booked
under a generic bucket, so the ledger could not attribute spend to the feature.
"""
import asyncio

from interview_api.infrastructure.models.governance import model_scope
from interview_api.infrastructure.models.router import EXTRACT_ROUTE
from interview_api.infrastructure.models.provider import ModelProvider, ModelResponse
from interview_api.modules.job_target.schemas import ExtractedJobDescription
from interview_api.modules.job_target.service import JobTargetService

JD = "岗位职责：负责交易系统的接口开发与维护。\n任职要求：必须熟悉 Python，至少两年后端开发经验。"


class _ScopeRecordingProvider(ModelProvider):
    """Records what model_scope looked like while the model call ran."""

    provider_name = "recording"
    model_name = "recording-model"
    timeout = 5.0
    max_output_tokens = 512

    def __init__(self):
        self.seen = []

    async def generate(self, messages, *, schema):
        self.seen.append(model_scope.get())
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


def test_extract_books_the_spend_under_the_extract_route():
    provider = _ScopeRecordingProvider()
    service = _service_with(provider)
    parsed, mode, error = asyncio.run(service.extract(JD, None, None, user_id=7))
    assert mode == "LLM" and error is None and parsed["requirements"]
    assert provider.seen and provider.seen[0] is not None
    factory, owner, uid, route = provider.seen[0]
    assert route == EXTRACT_ROUTE and owner == "user:7" and uid == 7
    # The scope must not leak past the call.
    assert model_scope.get() is None


def test_extract_without_a_user_keeps_the_previous_behaviour():
    provider = _ScopeRecordingProvider()
    service = _service_with(provider)
    asyncio.run(service.extract(JD, None, None))
    assert provider.seen and provider.seen[0] is None
    assert model_scope.get() is None
