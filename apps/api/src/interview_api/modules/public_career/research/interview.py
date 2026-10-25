"""Adapter over existing Extraction -> Validation -> Routing -> Reliability -> Gate."""

import json

from interview_api.infrastructure.models.router import EXTRACT_ROUTE, QUALITY_ROUTE
from interview_api.modules.experience.agents.graph import build_experience_extraction_graph
from interview_api.modules.experience.agents.routing_agent import RoutingAgent
from interview_api.modules.experience.agents.reliability_agent import ReliabilityAgent
from interview_api.modules.experience.agents.schemas import ExtractionExperience, RoutingResult, ReliabilityResult
from interview_api.modules.experience.agents.validators import validate_extraction_result
from interview_api.modules.experience.agents.quality_gate import evaluate_quality_gate
from interview_api.modules.public_career.research.schemas import (
    PublicInterviewExtraction, PublicRoutingResult, PublicReliabilityResult, FragmentInterviewExtraction,
)
from interview_api.modules.public_career.research.evidence import EvidenceValidationError
from interview_api.modules.public_career.research.fragments import InterviewSourceFragments


def resolve_fragment_extraction(selected, fragments):
    """Resolve real source anchors before downstream classification/quality.

    normalized_question is explicitly derived, never used as a verbatim Claim.
    A human must check its meaning against the displayed original excerpt.
    """
    fragments.resolve(selected.evidence_ids)
    if selected.company or selected.position:
        fragments.resolve(selected.identity_evidence_ids)
        fragments.validate_identity(selected.identity_evidence_ids, selected.company, selected.position)
    questions, anchors, topics = [], [], []
    seen = set()
    for index, item in enumerate(selected.questions):
        try:
            span = fragments.question_span(item.fragment_id, item.kind)
        except EvidenceValidationError as exc:
            raise EvidenceValidationError(f'questions[{index}].fragment_id: {exc}') from exc
        anchor = dict(fragment_id=span.evidence_id, source_snapshot_id=span.snapshot_id,
                      raw_excerpt=span.excerpt, location=span.location, kind=item.kind)
        if item.kind == 'REPORTED_TOPIC':
            if item.normalized_question is not None:
                raise EvidenceValidationError(f'questions[{index}]: REPORTED_TOPIC must not invent a normalized question')
            if anchor not in topics:
                topics.append(anchor)
            continue
        if not item.normalized_question or not item.normalized_question.strip():
            raise EvidenceValidationError(f'questions[{index}].normalized_question is required')
        from interview_api.modules.public_career.fingerprints import normalize_text
        key = (span.evidence_id, normalize_text(item.normalized_question))
        if key in seen:
            continue
        seen.add(key)
        questions.append(dict(question=item.normalized_question, question_type=item.question_type,
                              confidence=item.confidence, evidence_ids=[span.evidence_id]))
        anchors.append({**anchor, 'normalized_question': item.normalized_question,
                        'normalization_status': 'DERIVED_REQUIRES_REVIEW'})
    value = selected.model_dump(exclude={'questions'})
    value['questions'] = questions
    # A topic-only article is retained in audit, not turned into exact questions.
    value['is_interview_experience'] = selected.is_interview_experience and bool(questions)
    return PublicInterviewExtraction.model_validate(value), dict(
        contract_version='source-fragments@1.0.0', questions=anchors, reported_topics=topics,
        excluded_fragments=fragments.excluded)


class RoutedLegacyLLM:
    """Existing wrappers use chat(); all calls get structured validation/audit here."""

    def __init__(self, calls, *, prompt, route, schema):
        self.calls, self.prompt, self.route, self.schema = calls, prompt, route, schema

    async def chat(self, messages, **_kwargs):
        # Source metadata is deterministic input, never a model-owned evidence field.
        value = await self.calls.call(self.prompt, self.route, {"legacy_input": messages[-1]["content"]}, self.schema)
        return value.model_dump_json()


class InterviewPipelineAdapter:
    def __init__(self, calls, *, requested_identity=None, require_literal_questions=False):
        self.calls = calls
        self.requested_identity = requested_identity
        self.require_literal_questions = require_literal_questions

    async def run(self, catalog, raw_text: str, source_url: str, title: str | None):
        public_extraction = None
        source_anchors = None

        async def extraction_node(state):
            nonlocal public_extraction, source_anchors
            anchored = isinstance(catalog, InterviewSourceFragments)
            payload = {"source_fragments" if anchored else "evidence": catalog.model_input()}
            if self.requested_identity:
                payload["requested_identity"] = self.requested_identity
            selected = await self.calls.call(
                "interview_extract", EXTRACT_ROUTE,
                payload, FragmentInterviewExtraction if anchored else PublicInterviewExtraction,
            )
            if anchored:
                public_extraction, source_anchors = resolve_fragment_extraction(selected, catalog)
            else:
                public_extraction = selected
            catalog.resolve(public_extraction.evidence_ids)
            if public_extraction.company or public_extraction.position:
                catalog.resolve(public_extraction.identity_evidence_ids)
            questions = []
            for index, item in enumerate(public_extraction.questions):
                spans = catalog.resolve(item.evidence_ids)
                if self.require_literal_questions and not anchored and not catalog.supports_literal(item.evidence_ids, item.question):
                    elsewhere = catalog.supports_literal(list(catalog.spans), item.question)
                    raise EvidenceValidationError(
                        f"Web question must be extractive: questions[{index}].question={item.question[:160]!r}; "
                        f"evidence_ids={item.evidence_ids}; literal_in_cited=False; literal_elsewhere={elsewhere}"
                    )
                value = item.model_dump(exclude={"evidence_ids", "evidence"})
                value["evidence"] = spans[0].excerpt[:500]
                questions.append(value)
            adapted = public_extraction.model_dump(exclude={"evidence_ids", "identity_evidence_ids", "questions"})
            adapted["questions"] = questions
            return {"extraction_result": adapted}

        async def validation_node(state):
            extraction = ExtractionExperience.model_validate(state["extraction_result"])
            validation = validate_extraction_result(extraction, raw_text)
            if not validation.is_valid:
                raise ValueError("Invalid interview extraction: " + ",".join(validation.errors))
            return {"is_valid": True}

        async def routing_node(state):
            if not state["extraction_result"]["is_interview_experience"]:
                return {}
            llm = RoutedLegacyLLM(self.calls, prompt="interview_routing", route=EXTRACT_ROUTE, schema=PublicRoutingResult)
            result = await RoutingAgent(llm).run(state)
            extraction = state["extraction_result"]
            if result.company not in (None, extraction.get("company")) or result.position not in (None, extraction.get("position")):
                raise ValueError("Routing changed supported company/job identity")
            indexes = [item.question_index for item in result.question_results]
            if sorted(indexes) != list(range(len(extraction["questions"]))):
                raise ValueError("Routing must cover each extracted question exactly once")
            for item in result.question_results:
                # Routing controls classification; question fact stays extraction-owned.
                item.normalized_question = extraction["questions"][item.question_index]["question"]
            return {"routing_result": result.model_dump()}

        async def reliability_node(state):
            if not state["extraction_result"]["is_interview_experience"]:
                return {}
            llm = RoutedLegacyLLM(self.calls, prompt="interview_reliability", route=QUALITY_ROUTE, schema=PublicReliabilityResult)
            result = await ReliabilityAgent(llm).run(state)
            return {"reliability_result": result.model_dump()}

        async def gate_node(state):
            extraction = ExtractionExperience.model_validate(state["extraction_result"])
            routing = RoutingResult.model_validate(state["routing_result"]) if state.get("routing_result") else None
            reliability = ReliabilityResult.model_validate(state["reliability_result"]) if state.get("reliability_result") else None
            gate = evaluate_quality_gate(extraction=extraction, routing=routing, reliability=reliability, raw_text=raw_text)
            return {"quality_gate_result": gate.model_dump()}

        async def save_node(_state):
            return {}  # Public service writes provenance and drafts atomically.

        graph = build_experience_extraction_graph(
            extraction_node=extraction_node, extraction_validation_node=validation_node,
            routing_node=routing_node, reliability_node=reliability_node,
            quality_gate_node=gate_node, save_result_node=save_node,
        )
        result = await graph.ainvoke({"raw_text": raw_text, "source_url": source_url, "title": title,
                                     "platform": None, "snippet": None})
        if source_anchors is not None:
            result['source_anchors'] = source_anchors
        return public_extraction, result
