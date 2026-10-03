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
    PublicInterviewExtraction, PublicRoutingResult, PublicReliabilityResult,
)


class RoutedLegacyLLM:
    """Existing wrappers use chat(); all calls get structured validation/audit here."""

    def __init__(self, calls, *, prompt, route, schema):
        self.calls, self.prompt, self.route, self.schema = calls, prompt, route, schema

    async def chat(self, messages, **_kwargs):
        # Source metadata is deterministic input, never a model-owned evidence field.
        value = await self.calls.call(self.prompt, self.route, {"legacy_input": messages[-1]["content"]}, self.schema)
        return value.model_dump_json()


class InterviewPipelineAdapter:
    def __init__(self, calls):
        self.calls = calls

    async def run(self, catalog, raw_text: str, source_url: str, title: str | None):
        public_extraction = None

        async def extraction_node(state):
            nonlocal public_extraction
            public_extraction = await self.calls.call(
                "interview_extract", EXTRACT_ROUTE,
                {"evidence": catalog.model_input()}, PublicInterviewExtraction,
            )
            catalog.resolve(public_extraction.evidence_ids)
            if public_extraction.company or public_extraction.position:
                catalog.resolve(public_extraction.identity_evidence_ids)
            questions = []
            for item in public_extraction.questions:
                spans = catalog.resolve(item.evidence_ids)
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
        return public_extraction, result
