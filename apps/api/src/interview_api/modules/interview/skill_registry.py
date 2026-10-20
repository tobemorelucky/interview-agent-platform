"""Trusted local metadata discovery. External job text never becomes instructions."""
from copy import deepcopy
import json
import logging
from pathlib import Path
from time import monotonic
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

SkillId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
Version = Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
ROOT = Path(__file__).with_name("interviewer_skills")
logger = logging.getLogger(__name__)


class MatchingRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: list[str] = Field(min_length=1)
    title_aliases: list[str] = Field(min_length=1)
    responsibility_terms: list[str] = Field(min_length=1)
    capability_groups: list[list[str]] = Field(min_length=1)
    exclusions: list[str] = Field(default_factory=list)


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: SkillId
    label: str = Field(min_length=1, max_length=100)
    version: Version
    professional_category: str = Field(min_length=1)
    applicable_roles: list[str] = Field(min_length=1)
    capability_tags: list[str] = Field(min_length=1)
    scope: str = Field(min_length=1)
    matching: MatchingRules
    definition_path: str = "skill.json"
    instructions_path: str = "SKILL.md"
    # Metadata is not permission. V1.1 grants NO executable tools.
    required_tools: list[str] = Field(default_factory=list)


_cache: dict[str, tuple[float, dict, dict]] = {}


def discover(root=ROOT, *, refresh=False):
    root = Path(root).resolve()
    cache_key = str(root)
    old = _cache.get(cache_key)
    if not refresh and old and monotonic() - old[0] < 2:
        return deepcopy(old[1]), deepcopy(old[2])
    entries, errors = {}, {}
    # One manifest per immutable skill version. Root V1 manifest is historical only.
    for path in sorted(root.glob("*/*/manifest.json")):
        try:
            manifest = SkillManifest.model_validate_json(path.read_text(encoding="utf-8"))
            directory = path.parent
            if directory.name != manifest.version or directory.parent.name != manifest.id:
                raise ValueError("Manifest identity must match directory")
            if manifest.required_tools:
                raise ValueError("UNSUPPORTED_TOOL_CAPABILITY: V1.1 grants no tools")
            for relative in (manifest.definition_path, manifest.instructions_path):
                target = (directory / relative).resolve()
                if Path(relative).is_absolute() or not target.is_relative_to(directory.resolve()) or not target.is_relative_to(root) or not target.is_file():
                    raise ValueError("Invalid or missing local asset path")
            key = (manifest.id, manifest.version)
            if key in entries:
                raise ValueError("Duplicate Skill ID/version")
            entries[key] = dict(manifest=manifest.model_dump(), directory=str(directory))
        except (OSError, ValueError, TypeError) as exc:
            errors[str(path.relative_to(root))] = type(exc).__name__ + ": " + str(exc)[:200]
            logger.warning("Skill quarantined: %s (%s)", path, type(exc).__name__)
    selected = {}
    for (identity, version), entry in entries.items():
        previous = selected.get(identity)
        if not previous or tuple(map(int, version.split("."))) > tuple(map(int, previous["manifest"]["version"].split("."))):
            selected[identity] = entry
    _cache[cache_key] = (monotonic(), selected, errors)
    return deepcopy(selected), deepcopy(errors)


def public_session_view(data):
    """API-only projection. Internal immutable Snapshot remains complete in DB."""
    result = deepcopy(data)
    snapshot = result.get("context_snapshot") or {}
    config = snapshot.get("interviewer_config")
    if config:
        snapshot["interviewer_config"] = dict(
            engine_version=config["engine_version"], content_fingerprint=config["content_fingerprint"],
            skill={k: config["skill"][k] for k in ("id", "label", "version", "domains")},
            round={k: config["round"][k] for k in ("id", "label", "version", "focus")},
            style={k: config["style"][k] for k in ("id", "label", "version")},
            company_background=deepcopy(config["company_background"]))
    for question in snapshot.get("public_questions", []):
        question.pop("expected_points", None)
    return result
