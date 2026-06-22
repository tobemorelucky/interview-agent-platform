"""Routing Agent for extracted interview questions."""

from __future__ import annotations

import json

from interview_api.infrastructure.llm import LLMProvider
from interview_api.modules.experience.agents.extraction_agent import parse_json_object
from interview_api.modules.experience.agents.schemas import RoutingResult
from interview_api.modules.experience.agents.state import ExperienceAgentState

ROUTING_SYSTEM_PROMPT = """你是面试题路由 Agent。

你根据 Extraction Agent 的结构化结果，为每个面试问题判断岗位方向、技术分类、题型、难度、目标题库和是否值得索引。

规则：
1. 不要编造公司和岗位；如果 Extraction 已有 company/position，可以谨慎修正。
2. 每个 question_results.question_index 必须对应 extraction_result.questions 的下标，从 0 开始。
3. should_index=false 用于太泛、不像面试题、重复口水话、没有学习价值的问题。
4. 输出必须是一个 JSON 对象，不要输出 Markdown。"""


class RoutingAgent:
    def __init__(self, llm: LLMProvider, *, model_name: str | None = None):
        self.llm = llm
        self.model_name = model_name

    async def run(self, state: ExperienceAgentState) -> RoutingResult:
        extraction = state.get("extraction_result") or {}
        prompt = {
            "source": {
                "title": state.get("title"),
                "url": state.get("source_url"),
                "platform": state.get("platform"),
            },
            "extraction_result": extraction,
            "output_schema": {
                "overall_job_direction": "BACKEND/FRONTEND/AI_APPLICATION/ALGORITHM/DATA/TEST/PRODUCT/OTHER 或 null",
                "company": "公司或 null",
                "position": "岗位或 null",
                "question_results": [
                    {
                        "question_index": 0,
                        "normalized_question": "规范化后的问题",
                        "job_direction": "BACKEND",
                        "technical_categories": ["Redis"],
                        "question_type": "BASIC_KNOWLEDGE/PROJECT_DEEP_DIVE/SYSTEM_DESIGN/ALGORITHM/SCENARIO/HR/OTHER",
                        "difficulty": "EASY/MEDIUM/HARD/UNKNOWN",
                        "target_banks": ["backend", "redis"],
                        "should_index": True,
                        "routing_confidence": 0.8,
                    }
                ],
                "suggested_tags": ["Redis", "后端"],
                "routing_summary": "路由摘要",
                "routing_confidence": 0.8,
            },
        }
        response = await self.llm.chat(
            [
                {"role": "system", "content": ROUTING_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return RoutingResult.model_validate(parse_json_object(response))
