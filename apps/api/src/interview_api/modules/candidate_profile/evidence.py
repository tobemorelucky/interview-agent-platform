"""Deterministic exact-offset evidence and candidate-side resolver."""
from hashlib import sha256
import json
import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from interview_api.modules.public_career.fingerprints import normalize_text
from interview_api.modules.candidate_profile.models import ResumeEvidence
# The heading table lives with the resume section walker so that evidence rows
# and the employment-kind resolver cannot drift apart.
from interview_api.modules.resume.sections import HEADINGS, section_lines


def digest(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def evidence_rows(resume_id: int, raw_text: str) -> list[dict]:
    rows = []
    for match, section in section_lines(raw_text):
        line = match.group()
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


def normalize_skill_evidence_states(delta, evidence: dict[str, dict], *, evidence_aliases=None) -> list[dict]:
    diagnostics = []
    for index, skill in enumerate(delta.skills):
        record = dict(skill_label=skill.label, proposed_state=skill.evidence_state,
            maximum_supported_state=None, referenced_evidence_ids=list(skill.evidence_ids), validation_rule=SKILL_RULE)
        if not skill.evidence_ids or any(key not in evidence for key in skill.evidence_ids):
            raise ProfileEvidenceError("UNKNOWN_CANDIDATE_EVIDENCE", record)
        # A legitimate reference is not enough: the skill itself must be in the cited text.
        if not any(normalize_text(skill.label) in normalize_text(evidence[key]["excerpt"]) for key in skill.evidence_ids):
            diagnostic = literal_failure_diagnostics(skill.label, category="skills", path=f"skills.{index}",
                field="label", keys=skill.evidence_ids, evidence=evidence, aliases=evidence_aliases)
            diagnostic.update(proposed_state=skill.evidence_state, maximum_supported_state=None)
            raise ProfileEvidenceError("UNSUPPORTED_CANDIDATE_FACT", diagnostic)
        maximum = maximum_supported_skill_state(skill, evidence)
        final = SKILL_STATES[min(SKILL_STATES.index(skill.evidence_state), SKILL_STATES.index(maximum))]
        record.update(maximum_supported_state=maximum, final_state=final,
                      normalized=skill.evidence_state != final)
        skill.evidence_state = final
        diagnostics.append(record)
    return diagnostics


def literal_failure_diagnostics(value, *, category, path, field, keys, evidence, aliases=None):
    """Describe failed support without storing candidate text or repairing refs."""
    aliases = aliases or {}
    cited = set(keys)
    normalized = normalize_text(value)
    matches = [key for key, item in evidence.items()
               if key not in cited and normalized in normalize_text(item["excerpt"])]
    return dict(field_path=path + "." + field, category=category, value_field=field,
        value_length=len(value), value_fingerprint=sha256(normalized.encode()).hexdigest(),
        cited_aliases=[aliases.get(key, key) for key in keys],
        cited_literal_match=any(normalized in normalize_text(evidence[key]["excerpt"])
                                for key in cited if key in evidence),
        uncited_literal_match=bool(matches),
        possible_wrong_evidence_reference=bool(matches),
        validation_rule="NORMALIZED_CONTIGUOUS_QUOTE_IN_CITED_ATOMIC_EVIDENCE")


def resolve_and_validate(delta, evidence: dict[str, dict], *, stable_only=False, evidence_aliases=None) -> None:
    # Legacy callers keep the old extractive checks; new writes validate only
    # stable fields. This does not relax ANY stable-fact support rule.
    facts = [("candidate_summary", "candidate_summary", delta.candidate_summary)] if delta.candidate_summary and not stable_only else []
    categories = ("skills", "projects", "education", "work_experience", "internship_experience")
    if not stable_only:
        categories += ("strengths",)
    for category in categories:
        facts.extend((category, f"{category}.{index}", item) for index, item in enumerate(getattr(delta, category)))
    for category, path, fact in facts:
        if any(key not in evidence for key in fact.evidence_ids):
            raise ValueError("UNKNOWN_CANDIDATE_EVIDENCE")
        sources = [evidence[key] for key in set(fact.evidence_ids)]
        # Conservative extractive facts: valid IDs alone cannot support invented ability.
        for field, value in (("statement", fact.statement), ("label", getattr(fact, "label", None))):
            if value and not any(normalize_text(value) in normalize_text(item["excerpt"]) for item in sources):
                raise ProfileEvidenceError("UNSUPPORTED_CANDIDATE_FACT" if category != "strengths" else "UNSUPPORTED_STRENGTH",
                    literal_failure_diagnostics(value, category=category, path=path, field=field,
                        keys=fact.evidence_ids, evidence=evidence, aliases=evidence_aliases))
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


EMPLOYMENT_CATEGORIES = {"work_experience": "work", "internship_experience": "internship"}
EMPLOYMENT_POLICY = "EMPLOYMENT_KIND_FROM_CITED_SECTIONS_V1"
_EMPLOYMENT_SECTIONS = ("work", "internship")


def normalize_employment_categories(delta, evidence: dict[str, dict]) -> dict:
    """The server decides 工作/实习 from the cited excerpts' own sections.

    A fact is credited as full-time work or as an internship only when *every*
    employment-section excerpt it cites agrees. Conflicting sections, project
    only support, or support that never sits under an employment heading is not
    credited to either bucket: the fact is dropped with a diagnostic instead of
    inventing a kind. When the resume has no employment heading at all there is
    nothing to check against, so the proposal is kept and marked unverified.

    Only categories are touched. Text, labels, evidence links and skill states
    are never rewritten.
    """
    has_heading = any(item["section"] in _EMPLOYMENT_SECTIONS for item in evidence.values())
    decisions, dropped, unverified, topics = [], [], [], []
    for category, expected in EMPLOYMENT_CATEGORIES.items():
        kept = []
        for index, fact in enumerate(list(getattr(delta, category))):
            sections = {evidence[key]["section"] for key in fact.evidence_ids if key in evidence}
            employment = sections & set(_EMPLOYMENT_SECTIONS)
            record = dict(category=category, field_path=f"{category}.{index}", sections=sorted(sections))
            if employment == {expected}:
                kept.append(fact)
                decisions.append({**record, "resolution": "CONFIRMED"})
            elif len(employment) == 1:
                target = "work_experience" if "work" in employment else "internship_experience"
                existing = {item.label for item in getattr(delta, target)}
                if fact.label not in existing:
                    getattr(delta, target).append(fact)
                decisions.append({**record, "resolution": "MOVED", "moved_to": target})
            elif employment:
                dropped.append({**record, "resolution": "DROPPED_CONFLICTING_SECTIONS"})
                topics.append(_employment_topic(fact, "雇佣类型（原文指向两个不同小节）"))
            elif "projects" in sections:
                dropped.append({**record, "resolution": "DROPPED_PROJECT_EVIDENCE_ONLY"})
                topics.append(_employment_topic(fact, "是否属于工作或实习（只来自项目小节）"))
            elif has_heading or sections - {"general"}:
                # The resume does have section headings, so an entry that sits
                # under none of them is not evidence of either employment kind.
                dropped.append({**record, "resolution": "DROPPED_NO_EMPLOYMENT_SECTION"})
                topics.append(_employment_topic(fact, "雇佣类型（工作/实习）"))
            else:
                kept.append(fact)
                unverified.append({**record, "resolution": "UNVERIFIED_NO_EMPLOYMENT_HEADING"})
        setattr(delta, category, kept)
    for topic in topics[:3]:
        if topic not in delta.unknowns:
            delta.unknowns.append(topic)
    return dict(policy=EMPLOYMENT_POLICY, decisions=decisions + unverified,
                dropped=dropped, unverified=unverified, topics=topics[:3])


def _employment_topic(fact, reason: str) -> str:
    """A short 'please confirm' topic built from the fact's own verbatim label."""
    label = " ".join(str(getattr(fact, "label", "") or "").split())[:40]
    return (f"{label} {reason}" if label else reason)[:100]


