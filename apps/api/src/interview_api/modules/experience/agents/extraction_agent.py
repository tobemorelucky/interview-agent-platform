"""Extraction Agent LLM wrapper."""

from __future__ import annotations

import json
import re
from typing import Any

from interview_api.infrastructure.llm import LLMProvider
from interview_api.modules.experience.agents.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    build_extraction_prompt,
)
from interview_api.modules.experience.agents.schemas import ExtractionExperience
from interview_api.modules.experience.agents.state import ExperienceAgentState


class ExtractionAgent:
    def __init__(self, llm: LLMProvider, *, model_name: str | None = None):
        self.llm = llm
        self.model_name = model_name

    async def run(self, state: ExperienceAgentState) -> ExtractionExperience:
        prompt = build_extraction_prompt(
            title=state.get("title"),
            url=state["source_url"],
            snippet=state.get("snippet"),
            raw_text=state["raw_text"],
        )
        response = await self.llm.chat(
            [
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        payload = parse_json_object(response)
        return ExtractionExperience.model_validate(payload)


def parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("LLM response is not valid JSON")
        data = json.loads(match.group())
    if not isinstance(data, dict):
        raise ValueError("LLM response JSON must be an object")
    return data
