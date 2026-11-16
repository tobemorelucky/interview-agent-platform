"""Controlled vocabulary for job-requirement type and strength.

Two problems this module exists for, both observed on real imports:

1. `JobJDExtraction.requirement_type` / `importance` are free text. One provider
   returned `REQUIRED` + `SKILL`, another `required` + `学历/专业`. Downstream
   consumers that test membership of a fixed set then silently disagree.
2. A compound sentence ending in `优先` was classified wholly as PREFERRED, so
   its mandatory clause (`熟悉Linux操作系统`) lost its strength.

The mapping is explicit and closed. Unknown or ambiguous labels never fall back
to "technical skill": they surface as UNKNOWN/AMBIGUOUS so Matching can decline
to treat them as technical evidence and review can decide.

Nothing here rewrites stored model output; callers decide where to apply it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from interview_api.modules.public_career.fingerprints import normalize_text


class RequirementType(str, Enum):
    EDUCATION = "EDUCATION"
    MAJOR = "MAJOR"
    GRADUATION_YEAR = "GRADUATION_YEAR"
    LOCATION = "LOCATION"
    ELIGIBILITY = "ELIGIBILITY"
    SKILL = "SKILL"
    EXPERIENCE = "EXPERIENCE"
    SOFT_SKILL = "SOFT_SKILL"
    CERTIFICATE = "CERTIFICATE"
    LANGUAGE = "LANGUAGE"
    PORTFOLIO = "PORTFOLIO"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class RequirementStrength(str, Enum):
    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"
    UNKNOWN = "UNKNOWN"


# Hard eligibility conditions are handled outside technical alignment.
HARD_ELIGIBILITY = frozenset({
    RequirementType.EDUCATION, RequirementType.MAJOR, RequirementType.GRADUATION_YEAR,
    RequirementType.LOCATION, RequirementType.ELIGIBILITY,
})

# Explicit synonyms only. A label that is not listed stays UNKNOWN on purpose.
_TYPE_SYNONYMS: dict[str, RequirementType] = {
    # English
    "education": RequirementType.EDUCATION,
    "degree": RequirementType.EDUCATION,
    "major": RequirementType.MAJOR,
    "field_of_study": RequirementType.MAJOR,
    "graduation_year": RequirementType.GRADUATION_YEAR,
    "graduation": RequirementType.GRADUATION_YEAR,
    "location": RequirementType.LOCATION,
    "work_location": RequirementType.LOCATION,
    "eligibility": RequirementType.ELIGIBILITY,
    "skill": RequirementType.SKILL,
    "skills": RequirementType.SKILL,
    "technical_skill": RequirementType.SKILL,
    "technical": RequirementType.SKILL,
    "experience": RequirementType.EXPERIENCE,
    "soft_skill": RequirementType.SOFT_SKILL,
    "soft": RequirementType.SOFT_SKILL,
    "certificate": RequirementType.CERTIFICATE,
    "certification": RequirementType.CERTIFICATE,
    "language": RequirementType.LANGUAGE,
    "portfolio": RequirementType.PORTFOLIO,
    "other": RequirementType.OTHER,
    # Chinese
    "学历": RequirementType.EDUCATION,
    "学历要求": RequirementType.EDUCATION,
    "专业": RequirementType.MAJOR,
    "专业要求": RequirementType.MAJOR,
    "毕业年份": RequirementType.GRADUATION_YEAR,
    "届别": RequirementType.GRADUATION_YEAR,
    "地点": RequirementType.LOCATION,
    "地区": RequirementType.LOCATION,
    "工作地点": RequirementType.LOCATION,
    "资格": RequirementType.ELIGIBILITY,
    "硬性条件": RequirementType.ELIGIBILITY,
    "技能": RequirementType.SKILL,
    "技术技能": RequirementType.SKILL,
    "技术": RequirementType.SKILL,
    "编程能力": RequirementType.SKILL,
    "计算机基础": RequirementType.SKILL,
    "后端开发": RequirementType.SKILL,
    "ai/agent技术知识": RequirementType.SKILL,
    "ai/agent工程实践": RequirementType.SKILL,
    "经验": RequirementType.EXPERIENCE,
    "领域兴趣/经验": RequirementType.EXPERIENCE,
    "领域理解": RequirementType.EXPERIENCE,
    "软技能": RequirementType.SOFT_SKILL,
    "通用能力": RequirementType.SOFT_SKILL,
    "证书": RequirementType.CERTIFICATE,
    "语言": RequirementType.LANGUAGE,
    "作品": RequirementType.PORTFOLIO,
    "其他": RequirementType.OTHER,
}

# Labels that name more than one category. Resolved conservatively and flagged.
_AMBIGUOUS_SYNONYMS: dict[str, RequirementType] = {
    "学历/专业": RequirementType.EDUCATION,
    "学历或专业": RequirementType.EDUCATION,
    "education/major": RequirementType.EDUCATION,
    "学历/专业/毕业年份": RequirementType.EDUCATION,
}

_STRENGTH_SYNONYMS: dict[str, RequirementStrength] = {
    "required": RequirementStrength.REQUIRED,
    "must": RequirementStrength.REQUIRED,
    "mandatory": RequirementStrength.REQUIRED,
    "硬性": RequirementStrength.REQUIRED,
    "必须": RequirementStrength.REQUIRED,
    "强制": RequirementStrength.REQUIRED,
    "必备": RequirementStrength.REQUIRED,
    "preferred": RequirementStrength.PREFERRED,
    "plus": RequirementStrength.PREFERRED,
    "nice_to_have": RequirementStrength.PREFERRED,
    "优先": RequirementStrength.PREFERRED,
    "加分": RequirementStrength.PREFERRED,
    "加分项": RequirementStrength.PREFERRED,
    "优先考虑": RequirementStrength.PREFERRED,
}


def _key(value: object) -> str:
    return "".join(normalize_text(str(value or "")).split())


@dataclass(frozen=True)
class NormalizedType:
    value: RequirementType
    status: Literal["KNOWN", "AMBIGUOUS", "UNKNOWN"]
    matched: str | None

    @property
    def is_hard_eligibility(self) -> bool:
        return self.value in HARD_ELIGIBILITY

    @property
    def can_be_technical(self) -> bool:
        """Only a confidently non-eligibility label may enter technical alignment."""
        return self.status != "UNKNOWN" and self.value not in HARD_ELIGIBILITY


def normalize_requirement_type(raw: object) -> NormalizedType:
    key = _key(raw)
    if not key:
        return NormalizedType(RequirementType.UNKNOWN, "UNKNOWN", None)
    if key in _AMBIGUOUS_SYNONYMS:
        return NormalizedType(_AMBIGUOUS_SYNONYMS[key], "AMBIGUOUS", key)
    if key in _TYPE_SYNONYMS:
        return NormalizedType(_TYPE_SYNONYMS[key], "KNOWN", key)
    # Enum value form, e.g. "SOFT_SKILL" already canonical.
    for member in RequirementType:
        if key == _key(member.value):
            return NormalizedType(member, "KNOWN", key)
    return NormalizedType(RequirementType.UNKNOWN, "UNKNOWN", None)


def normalize_strength(raw: object) -> RequirementStrength:
    key = _key(raw)
    if not key:
        return RequirementStrength.UNKNOWN
    if key in _STRENGTH_SYNONYMS:
        return _STRENGTH_SYNONYMS[key]
    for member in RequirementStrength:
        if key == _key(member.value):
            return member
    return RequirementStrength.UNKNOWN


# ── Compound-sentence strength ──────────────────────────────────────────────
# Only strength markers are recognised here; no technology keyword lists.
_PREFERRED_TAIL = re.compile(r"(优先|者优先|优先考虑|preferred|is a plus)\s*$", re.IGNORECASE)
_SOFT_LEAD = re.compile(r"^(条件优秀者|表现优秀者|同等条件下).*优先", re.IGNORECASE)
_CLAUSE_SEPARATORS = re.compile(r"[；;。\n]")
_SUB_SEPARATORS = re.compile(r"[，,]")
# A bare qualifier is not a requirement of its own; it belongs to what follows.
_QUALIFIER_ONLY = re.compile(r"^(在?同等条件下?|条件优秀者|表现优秀者|特别优秀者)$")


@dataclass(frozen=True)
class StrengthClause:
    text: str
    strength: RequirementStrength
    offset: int
    marker: str | None


def split_strength_clauses(statement: str) -> list[StrengthClause]:
    """Split a requirement into clauses that carry their own strength marker.

    Returns a single clause when the sentence does not mix strengths, so callers
    can use the length as the "is this really compound?" signal.
    """
    text = statement or ""
    clauses: list[StrengthClause] = []
    cursor = 0
    for segment in _CLAUSE_SEPARATORS.split(text):
        base = cursor
        cursor += len(segment) + 1
        if not segment.strip():
            continue
        parts = _SUB_SEPARATORS.split(segment)
        merged: list[str] = []
        for part in parts:
            if merged and _QUALIFIER_ONLY.match(merged[-1].strip()):
                merged[-1] = merged[-1] + "，" + part
            else:
                merged.append(part)
        parts = merged
        if len(parts) == 1:
            value = segment.strip()
            marker = "优先" if (_PREFERRED_TAIL.search(value) or _SOFT_LEAD.search(value)) else None
            clauses.append(StrengthClause(value, RequirementStrength.PREFERRED if marker else RequirementStrength.REQUIRED,
                                          base + segment.find(value), marker))
            continue
        local = 0
        for part in parts:
            value = part.strip()
            start = base + local + (len(part) - len(part.lstrip()))
            local += len(part) + 1
            if not value:
                continue
            marker = "优先" if (_PREFERRED_TAIL.search(value) or _SOFT_LEAD.search(value)) else None
            clauses.append(StrengthClause(value, RequirementStrength.PREFERRED if marker else RequirementStrength.REQUIRED,
                                          start, marker))
    return clauses


def is_compound_mixed(statement: str) -> bool:
    clauses = split_strength_clauses(statement)
    strengths = {clause.strength for clause in clauses}
    return len(clauses) > 1 and len(strengths) > 1


def effective_strength(statement: str, stored: object) -> RequirementStrength:
    """Strength to use for consumers.

    A compound sentence that mixes a mandatory clause with a `优先` clause is
    treated as REQUIRED: the mandatory part still has to be met. Only a sentence
    whose every clause is preferred stays PREFERRED.
    """
    clauses = split_strength_clauses(statement)
    strengths = {clause.strength for clause in clauses}
    if len(clauses) <= 1 or len(strengths) == 1:
        if strengths == {RequirementStrength.PREFERRED}:
            return RequirementStrength.PREFERRED
        if strengths == {RequirementStrength.REQUIRED}:
            # A plain sentence with no marker: fall back to what was recorded.
            recorded = normalize_strength(stored)
            return recorded if recorded is not RequirementStrength.UNKNOWN else RequirementStrength.REQUIRED
        return normalize_strength(stored)
    # mixed
    return RequirementStrength.REQUIRED


def technical_clauses(statement: str) -> list[str]:
    """Mandatory clauses only — used by review/audit, never to rewrite storage."""
    return [clause.text for clause in split_strength_clauses(statement)
            if clause.strength is RequirementStrength.REQUIRED]


def describe(statement: str, stored_type: object, stored_strength: object) -> dict:
    normalized = normalize_requirement_type(stored_type)
    return {
        "type": normalized.value.value,
        "type_status": normalized.status,
        "is_hard_eligibility": normalized.is_hard_eligibility,
        "can_be_technical": normalized.can_be_technical,
        "stored_strength": str(stored_strength),
        "effective_strength": effective_strength(statement, stored_strength).value,
        "compound_mixed": is_compound_mixed(statement),
        "clauses": [{"text": c.text, "strength": c.strength.value} for c in split_strength_clauses(statement)],
    }


def consumer_strength(statement: str, stored: object) -> RequirementStrength:
    """Strength for a row whose recorded value may legitimately be UNKNOWN.

    User-supplied targets record UNKNOWN when a qualification is only *listed*
    under 任职要求; promoting that to REQUIRED would invent a hard filter the
    employer never wrote. Only two transitions are allowed here:

      PREFERRED + compound mixed -> REQUIRED   (the mandatory clause still applies)
      anything else             -> unchanged
    """
    recorded = normalize_strength(stored)
    if recorded is RequirementStrength.PREFERRED and is_compound_mixed(statement):
        return RequirementStrength.REQUIRED
    return recorded
