"""Deterministic exact-offset evidence and candidate-side resolver."""
from hashlib import sha256
import json
import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from interview_api.modules.public_career.fingerprints import normalize_text
from interview_api.modules.candidate_profile.models import ResumeEvidence


HEADINGS = {
    "projects": ("项目经历", "项目经验", "项目", "projects", "project experience"),
    "work": ("工作经历", "工作经验", "work experience", "employment"),
    "internship": ("实习经历", "实习经验", "internships", "internship experience"),
    "education": ("教育经历", "教育背景", "教育", "education"),
    "skills": ("专业技能", "技术技能", "技能", "skills", "technical skills"),
}


def digest(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def evidence_rows(resume_id: int, raw_text: str) -> list[dict]:
    rows = []
    section = "general"
    for match in re.finditer(r"[^\r\n]+", raw_text):
        line = match.group()
        heading = normalize_text(line).strip(" #:：-•")
        for name, aliases in HEADINGS.items():
            if heading in aliases:
                section = name
                break
        # Fixed-size exact slices, not LLM segmentation or inferred page locations.
        for offset in range(0, len(line), 1200):
            excerpt = line[offset:offset + 1200]
            if not excerpt.strip():
                continue
            start = match.start() + offset
            end = start + len(excerpt)
            content_hash = sha256(normalize_text(excerpt).encode()).hexdigest()
            key = "re_" + digest([resume_id, section, content_hash, start, end])
            rows.append(dict(resume_id=resume_id, evidence_key=key, section=section,
                             page=None, start_offset=start, end_offset=end,
                             excerpt=excerpt, content_hash=content_hash))
    return rows


async def ensure_resume_evidence(db, resume_id: int, raw_text: str) -> list[ResumeEvidence]:
    rows = evidence_rows(resume_id, raw_text)
    for offset in range(0, len(rows), 300):
        await db.execute(insert(ResumeEvidence).values(rows[offset:offset + 300]).on_conflict_do_nothing(index_elements=["evidence_key"]))
    keys = [row["evidence_key"] for row in rows]
    return list((await db.scalars(select(ResumeEvidence).where(ResumeEvidence.evidence_key.in_(keys)).order_by(ResumeEvidence.start_offset))).all())


SKILL_STATES = ("MENTIONED", "DEMONSTRATED", "STRONG_EVIDENCE")
SKILL_RULE = "MENTIONED:valid mention; DEMONSTRATED:1 distinct cited concrete-use excerpt; STRONG_EVIDENCE:2"


def maximum_supported_skill_state(skill, evidence: dict[str, dict]) -> str:
    # Only actually cited, existing evidence counts. Never manufacture or attach extra references.
    sources = [evidence[key] for key in set(skill.evidence_ids)]
    concrete = [item for item in sources if item["section"] in ("projects", "work", "internship")
                and normalize_text(skill.label) in normalize_text(item["excerpt"])]
    count = len({item["key"] for item in concrete})
    return SKILL_STATES[min(count, 2)]


class ProfileEvidenceError(ValueError):
    def __init__(self, code: str, diagnostics: dict):
        super().__init__(code)
        self.code = code
        self.diagnostics = diagnostics


def normalize_skill_evidence_states(delta, evidence: dict[str, dict]) -> list[dict]:
    diagnostics = []
    for skill in delta.skills:
        record = dict(skill_label=skill.label, proposed_state=skill.evidence_state,
            maximum_supported_state=None, referenced_evidence_ids=list(skill.evidence_ids), validation_rule=SKILL_RULE)
        if not skill.evidence_ids or any(key not in evidence for key in skill.evidence_ids):
            raise ProfileEvidenceError("UNKNOWN_CANDIDATE_EVIDENCE", record)
        # A legitimate reference is not enough: the skill itself must be in the cited text.
        if not any(normalize_text(skill.label) in normalize_text(evidence[key]["excerpt"]) for key in skill.evidence_ids):
            raise ProfileEvidenceError("UNSUPPORTED_CANDIDATE_FACT", record)
        maximum = maximum_supported_skill_state(skill, evidence)
        final = SKILL_STATES[min(SKILL_STATES.index(skill.evidence_state), SKILL_STATES.index(maximum))]
        record.update(maximum_supported_state=maximum, final_state=final,
                      normalized=skill.evidence_state != final)
        skill.evidence_state = final
        diagnostics.append(record)
    return diagnostics


def resolve_and_validate(delta, evidence: dict[str, dict]) -> None:
    facts = [("candidate_summary", delta.candidate_summary)] if delta.candidate_summary else []
    for category in ("skills", "projects", "education", "work_experience", "internship_experience", "strengths"):
        facts.extend((category, item) for item in getattr(delta, category))
    for category, fact in facts:
        if any(key not in evidence for key in fact.evidence_ids):
            raise ValueError("UNKNOWN_CANDIDATE_EVIDENCE")
        sources = [evidence[key] for key in set(fact.evidence_ids)]
        # Conservative extractive facts: valid IDs alone cannot support invented ability.
        for value in (fact.statement, getattr(fact, "label", None)):
            if value and not any(normalize_text(value) in normalize_text(item["excerpt"]) for item in sources):
                raise ValueError("UNSUPPORTED_CANDIDATE_FACT" if category != "strengths" else "UNSUPPORTED_STRENGTH")
        if category == "skills" and fact.evidence_state != "MENTIONED":
            concrete = [item for item in sources if item["section"] in ("projects", "work", "internship")]
            required = 2 if fact.evidence_state == "STRONG_EVIDENCE" else 1
            # Label must occur in actual concrete-use excerpts, not unrelated references.
            concrete = [item for item in concrete if normalize_text(fact.label) in normalize_text(item["excerpt"])]
            if len({item["key"] for item in concrete}) < required:
                raise ProfileEvidenceError("UNSUPPORTED_SKILL_STATE", dict(skill_label=fact.label,
                    proposed_state=fact.evidence_state, maximum_supported_state=maximum_supported_skill_state(fact,evidence),
                    referenced_evidence_ids=list(fact.evidence_ids), validation_rule=SKILL_RULE))
    for topic in delta.unknowns:
        if len(topic) > 100 or re.search(r"不会|不具备|缺乏能力|不能|does not|cannot|can't", topic, re.I):
            raise ValueError("UNKNOWN_IS_NOT_A_CAPABILITY_JUDGMENT")
