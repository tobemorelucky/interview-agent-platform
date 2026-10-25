"""Versioned, on-demand interviewer configuration. No models, network or DB writes."""
from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from interview_api.core.exceptions import AppError
from interview_api.modules.candidate_profile.evidence import digest

from interview_api.modules.interview.skill_registry import SkillId, discover
RoundId = Literal["technical_screen", "project_deep_dive"]
StyleId = Literal["gentle", "standard", "strict"]
ROOT = Path(__file__).with_name("interviewer_skills")
DIMENSIONS = {"technical_correctness", "coverage", "depth", "relevance", "communication"}


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: SkillId
    version: str
    label: str
    priority_topics: list[str] = Field(min_length=1, max_length=15)
    domains: list[str] = Field(min_length=1, max_length=10)
    key_capabilities: list[str] = Field(min_length=1, max_length=10)
    project_strategy: list[str] = Field(min_length=1, max_length=10)
    level_probes: dict[str, str]
    evaluation_anchors: dict[str, str]


class SkillRegistry:
    def __init__(self, root: Path = ROOT):
        self.root = root

    def _json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def options(self):
        # Catalog metadata only; do not load all SKILL.md bodies.
        entries, errors = discover(self.root)
        policies = self._json(self.root / "policies.json")
        return dict(version="1.1.0", quarantined_count=len(errors),
            skills=[{key: item["manifest"][key] for key in ("id", "label", "version", "professional_category", "scope")} for _, item in sorted(entries.items())],
            rounds=[dict(id=key, label=value["label"], description=value["focus"]) for key, value in policies["rounds"].items()],
            styles=[dict(id=key, label=value["label"], description={"gentle": "允许适度表达提示", "standard": "正常技术面试", "strict": "减少提示，关注边界与取舍"}[key]) for key, value in policies["styles"].items()])

    def freeze(self, skill_id, round_id, style_id, *, requirements=(), jd_source=None):
        try:
            entries, _ = discover(self.root)
            entry = entries[skill_id]
            metadata = entry["manifest"]
            version = metadata["version"]
            if not re.fullmatch(r"\d+\.\d+\.\d+", version):
                raise ValueError("Invalid skill version")
            directory = Path(entry["directory"])
            skill = SkillDefinition.model_validate(self._json(directory / metadata["definition_path"]))
            if skill.id != skill_id or skill.version != version or set(skill.evaluation_anchors) != DIMENSIONS:
                raise ValueError("Invalid skill identity/rubric")
            instructions = (directory / metadata["instructions_path"]).read_text(encoding="utf-8")
            policies = self._json(self.root / "policies.json")
            round_policy = dict(id=round_id, version=policies["version"], **policies["rounds"][round_id])
            style_policy = dict(id=style_id, version=policies["version"], **policies["styles"][style_id])
            if abs(sum(round_policy["category_weights"].values()) - 1) > 1e-6:
                raise ValueError("Invalid category weights")
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise AppError("INTERVIEWER_CONFIG_UNAVAILABLE", "面试官配置不可用，请联系维护者；未创建会话", 503) from exc
        # Source identity is metadata, not proof of a company's interview preferences.
        sourced = bool(jd_source and requirements)
        background = dict(mode="SOURCED_JOB_CONTEXT" if sourced else "GENERIC_SKILL_FALLBACK",
            source=deepcopy(jd_source) if sourced else None,
            requirement_ids=[r["id"] for r in requirements] if sourced else [],
            instruction="仅依照本场已发布 JD 和已审核题源，不推断公司必考题或内部面试偏好。" if sourced else
                        "公司资料依据不足，使用通用岗位 Skill；岗位名称不能证明公司面试偏好。")
        definition = skill.model_dump()
        frozen = dict(engine_version="1.1.0", registry_manifest=metadata, skill={**definition, "instructions": instructions,
            "content_fingerprint": digest([definition, instructions])}, round=round_policy, style=style_policy,
            company_background=background,
            prompt_versions={"interview_main": "1.1.0", "answer_evaluation": "1.1.0"})
        frozen["content_fingerprint"] = digest(frozen)
        return frozen


def selection_coverage(context, pacing):
    """Round weighting changes selection, never original caps/counters."""
    config = context.get("interviewer_config")
    if not config:
        return pacing
    return {**pacing, "category_weights": config["round"]["category_weights"],
            "skill_priority_topics": config["skill"]["priority_topics"]}


def choose_skill_source(config, technical, pacing):
    if not technical:
        return None
    priorities = config["skill"]["priority_topics"]
    opening = config["round"]["opening_category"]
    count = pacing["main_question_count"]
    def key(item):
        topic, category = item["target_topic"], item["target_category"]
        deficit = pacing["category_weights"].get(category, 0) * (count + 1) - pacing["category_counts"].get(category, 0)
        return (topic not in priorities and topic != "project_opening",
                category != opening if count == 0 else -deficit,
                topic == pacing["recent_topic"] and topic != "unknown",
                pacing["topic_counts"].get(topic, 0),
                priorities.index(topic) if topic in priorities else len(priorities), item["source_id"])
    return min(technical, key=key)


def interaction_policy(context, policy):
    config = context.get("interviewer_config")
    if not config:
        return policy
    return {**policy, "interaction": dict(probe_mode=config["style"]["probe_mode"],
        guidance=config["style"]["guidance"], round_focus=config["round"]["focus"])}


def style_recommendation(context, recommendation, evaluation):
    """Change type of deepening, not scores, difficulty or follow-up allowance."""
    config = context.get("interviewer_config")
    if not config or not evaluation or recommendation not in ("FOLLOW_UP", "DEEP_DIVE"):
        return recommendation
    if not evaluation.get("missing_points"):
        return recommendation
    requested = config["style"]["missing_point_action"]
    return recommendation if requested == "AS_RECOMMENDED" else requested


def evaluation_contract(config):
    # Intentionally excludes style and the style-dependent bundle fingerprint.
    return dict(skill_id=config["skill"]["id"], skill_version=config["skill"]["version"],
        skill_fingerprint=config["skill"]["content_fingerprint"],
        domains=config["skill"]["domains"], evaluation_anchors=config["skill"]["evaluation_anchors"],
        round_id=config["round"]["id"], round_version=config["round"]["version"],
        prompt_version=config["prompt_versions"]["answer_evaluation"],
        scoring_policy="SHARED_FIVE_DIMENSIONS_1_TO_5_NO_STYLE_ADJUSTMENT")


def agent_payload(payload, route):
    """Evaluation receives a style-blind contract, including during retry hashing."""
    result = deepcopy(payload)
    if route == "interview_main" and "memory_guided_plan" in result.get("snapshot", {}):
        result["snapshot"].pop("recent_performance_observations", None)
    if route == "answer_evaluation" and "memory_guided_plan" in result.get("snapshot", {}):
        # Historical inference must not anchor independent grading, even without a Skill.
        for key in ("memory_guided_plan", "approved_memory_context", "recent_performance_observations"):
            result.get("snapshot", {}).pop(key, None)
    config = result.get("snapshot", {}).get("interviewer_config")
    if route != "answer_evaluation" or not config:
        return result
    result["snapshot"].pop("interviewer_config")
    result["snapshot"]["interviewer_evaluation"] = evaluation_contract(config)
    # Style-generated process hints are UI assistance, never grading evidence.
    current = result.get("current_question") or {}
    current.pop("interviewer_guidance", None)
    if isinstance(current.get("evidence"), dict):
        current["evidence"].pop("interviewer_guidance", None)
    return result
