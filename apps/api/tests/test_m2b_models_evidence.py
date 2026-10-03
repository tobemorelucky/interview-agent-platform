import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from interview_api.infrastructure.models.provider import (
    CompatibleModelProvider, ReplayModelProvider, ModelProviderError, ModelResponse,
)
from interview_api.infrastructure.models.router import ModelRouter, EXTRACT_ROUTE, QUALITY_ROUTE
from interview_api.infrastructure.models.structured import generate_structured, StructuredOutputError
from interview_api.modules.public_career.models import SourceSnapshot
from interview_api.modules.public_career.research.evidence import EvidenceCatalog, EvidenceValidationError
from interview_api.modules.public_career.research.policy import job_publication_gate
from interview_api.modules.public_career.research.prompts import PromptRegistry
from interview_api.modules.public_career.research.schemas import (
    JobJDExtraction, ResearchQuality, PublicInterviewExtraction, PublicReliabilityResult, PublicRoutingResult,
)


def test_exact_evidence_catalog():
    snapshot = SourceSnapshot(id=123, source_id=8, content_text="精确正文。" * 200)
    catalog = EvidenceCatalog([snapshot], span_chars=50)
    duplicate = EvidenceCatalog([snapshot], span_chars=50)
    assert catalog.model_input() == duplicate.model_input()
    assert set(catalog.model_input()[0]) == {"evidence_id", "text"}
    for span in catalog.spans.values():
        assert snapshot.content_text[span.start:span.end] == span.excerpt
    first = next(iter(catalog.spans))
    assert catalog.resolve([first, first]) == catalog.resolve([first])
    with pytest.raises(EvidenceValidationError):
        catalog.resolve([])
    with pytest.raises(EvidenceValidationError):
        catalog.resolve(["ev_" + "0" * 24])


def test_evidence_budget_includes_multiple_sources():
    catalog = EvidenceCatalog([SourceSnapshot(id=i, source_id=i, content_text="x" * 500)
                               for i in range(1, 4)], max_chars=99, span_chars=30)
    assert {span.snapshot_id for span in catalog.spans.values()} == {1, 2, 3}
    assert sum(len(span.excerpt) for span in catalog.spans.values()) <= 99


def test_no_evidence_claim_and_model_owned_metadata_rejected():
    item = {"company_name": "Acme", "job_title": "Backend", "identity_evidence_ids": [],
            "jd_evidence_ids": [], "requirements": [{"statement": "Python", "evidence_ids": []}]}
    with pytest.raises(ValidationError):
        JobJDExtraction.model_validate(item)
    for schema in (ResearchQuality, PublicInterviewExtraction, PublicRoutingResult, PublicReliabilityResult):
        assert schema.model_config["extra"] == "forbid"


async def structured(responses, schema=ResearchQuality, provider=None):
    provider = provider or ReplayModelProvider(responses)
    router = ModelRouter({EXTRACT_ROUTE: provider})
    return await generate_structured(router, EXTRACT_ROUTE, system="test", payload={}, response_model=schema)


def test_structured_output_one_repair():
    provider = ReplayModelProvider([{"risk_flags": []}, {"suitable": True, "risk_flags": []}])
    result = asyncio.run(structured([], provider=provider))
    assert result.parsed.suitable and result.structured_retry == 1
    assert provider.calls == 2 and result.usage["total_tokens"] == 0


def test_malformed_output_constrained_repair():
    result = asyncio.run(structured(['{"suitable":true,"risk_flags":["some risk"],}',
                                    {"suitable": True, "risk_flags": ["some risk"]}]))
    assert result.structured_retry == 1


@pytest.mark.parametrize("responses", [
    [{}, {}],
    ['{"risk_flags":["original"]}', {"suitable": True, "risk_flags": ["invented"]}],
    ['not JSON', {"suitable": True, "risk_flags": ["invented"]}],
    ['{"risk_flags":["NASA"]}', {"suitable": True, "risk_flags": ["SQL"]}],
])
def test_repair_hard_fail_or_changed_fact(responses):
    provider = ReplayModelProvider(responses)
    with pytest.raises(StructuredOutputError) as err:
        asyncio.run(structured([], provider=provider))
    assert err.value.retried and provider.calls == 2


def test_repair_provider_failure_preserves_audit_metadata():
    provider = ReplayModelProvider([{}])
    with pytest.raises(ModelProviderError) as err:
        asyncio.run(structured([], provider=provider))
    assert err.value.retried and err.value.usage["total_tokens"] == 0


def test_repair_must_not_reassign_facts_or_evidence_ids():
    from pydantic import BaseModel

    class Identity(BaseModel):
        company_name: str
        job_title: str
        required_structure: bool

    provider = ReplayModelProvider([
        {"company_name": "Acme", "job_title": "Engineer"},
        {"company_name": "Engineer", "job_title": "Acme", "required_structure": True},
    ])
    with pytest.raises(StructuredOutputError):
        asyncio.run(structured([], schema=Identity, provider=provider))


def test_provider_guard_shared_between_routes_and_routers():
    class SlowProvider(ReplayModelProvider):
        active = 0
        maximum = 0

        async def generate(self, messages, *, schema):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return ModelResponse('{"suitable":true}')

    async def run():
        provider = SlowProvider([])
        routers = [ModelRouter({route: provider}, concurrency=1) for route in (EXTRACT_ROUTE, QUALITY_ROUTE)]
        await asyncio.gather(*[generate_structured(router, route, system="", payload={}, response_model=ResearchQuality)
                               for router, route in zip(routers, (EXTRACT_ROUTE, QUALITY_ROUTE))])
        assert provider.maximum == 1
    asyncio.run(run())


def test_compatible_provider_configuration_and_usage():
    async def run():
        def respond(request):
            assert request.url.path == "/v1/chat/completions"
            body = json.loads(request.content)
            assert body["model"] == "configured-model"
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"suitable":true}'}}],
                                             "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            provider = CompatibleModelProvider(base_url="https://model.invalid/v1", api_key="fixture",
                                               model="configured-model", timeout_seconds=1, max_output_tokens=200,
                                               client=client)
            result = await structured([], provider=provider)
            assert result.usage == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    asyncio.run(run())


@pytest.mark.parametrize("finish_reason, refusal, code", [
    ("length", None, "model_output_truncated"), ("stop", "refused", "model_refusal"),
])
def test_failed_provider_response_preserves_usage(finish_reason, refusal, code):
    async def run():
        def respond(request):
            return httpx.Response(200, json={"choices": [{"finish_reason": finish_reason,
                "message": {"content": "{}", "refusal": refusal}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            provider = CompatibleModelProvider(base_url="https://model.invalid/v1", api_key="fixture",
                                               model="configured-model", timeout_seconds=1, max_output_tokens=200,
                                               client=client)
            with pytest.raises(ModelProviderError) as error:
                await structured([], provider=provider)
            assert error.value.code == code and error.value.usage["total_tokens"] == 15
    asyncio.run(run())


def test_official_gate_uses_operator_trust_not_url_appearance():
    args = dict(company_name="Acme", evidence_complete=True, suitable=True, risk_flags=[],
                trusted_domains={"acme": ["acme.example"]})
    assert job_publication_gate(urls=["https://careers.acme.example/job"], **args).status == "APPROVED"
    assert job_publication_gate(urls=["https://acme.example.evil.invalid/job"], **args).status == "WAITING_REVIEW"
    assert job_publication_gate(urls=["https://community.example/job"], **args).status == "WAITING_REVIEW"
    assert job_publication_gate(urls=["https://careers.acme.example/job"], **{**args, "risk_flags": ["risk"]}).status == "WAITING_REVIEW"
    assert job_publication_gate(urls=[], **{**args, "evidence_complete": False}).status == "REJECTED"


def test_prompt_version_registry():
    first = PromptRegistry().get("job_jd")
    assert first.version == "1.0.0" and len(first.content_hash) == 64
    assert first == PromptRegistry().get("job_jd")
