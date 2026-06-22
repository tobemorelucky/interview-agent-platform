"""Reliability Agent LLM wrapper for extracted interview experiences."""

from __future__ import annotations

import json

from interview_api.infrastructure.llm import LLMProvider
from interview_api.modules.experience.agents.extraction_agent import parse_json_object
from interview_api.modules.experience.agents.schemas import ReliabilityResult
from interview_api.modules.experience.agents.state import ExperienceAgentState


RELIABILITY_SYSTEM_PROMPT = """You are the Reliability Agent for an offline interview-experience ingestion pipeline.
Evaluate whether the extracted content looks like a real interview experience and whether it is suitable for admin review.
Do not decide final publishing. Do not create new questions.
Return only one JSON object with the requested schema."""


class ReliabilityAgent:
    def __init__(self, llm: LLMProvider, *, model_name: str | None = None):
        self.llm = llm
        self.model_name = model_name

    async def run(self, state: ExperienceAgentState) -> ReliabilityResult:
        payload = {
            "source": {
                "url": state["source_url"],
                "title": state.get("title"),
                "snippet": state.get("snippet"),
                "platform": state.get("platform"),
                "raw_text_chars": len(state["raw_text"]),
                "raw_text_sample": state["raw_text"][:5000],
            },
            "extraction_result": state.get("extraction_result"),
            "routing_result": state.get("routing_result"),
            "schema": {
                "is_reliable": True,
                "reliability_score": 0.75,
                "content_quality_score": 0.8,
                "source_quality_score": 0.7,
                "spam_risk_score": 0.1,
                "ad_or_training_risk": False,
                "outdated_risk": False,
                "hallucination_risk_note": None,
                "risk_flags": ["example_risk_flag"],
                "quality_flags": ["example_quality_flag"],
                "publish_recommendation": "NEEDS_REVIEW",
                "reason": "short reason",
            },
        }
        response = await self.llm.chat(
            [
                {"role": "system", "content": RELIABILITY_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return ReliabilityResult.model_validate(parse_json_object(response))
