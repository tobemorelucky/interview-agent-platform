"""Deterministic job -> Skill alignment; no Agent, model, network or writes."""
import re
import unicodedata
from copy import deepcopy

from sqlalchemy import select

from interview_api.modules.interview.skill_registry import ROOT, discover
from interview_api.modules.public_career.models import Job, JobDescriptionVersion, JobRequirement, JobTag

POLICY_VERSION = "1.1.0"
WEIGHTS = dict(category=10, title=8, responsibility=6, capability_group=3)
MIN_SCORE, MIN_MARGIN = 9, 3


def normalized(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value)).casefold()).strip()


def contains(text, term):
    text, term = normalized(text), normalized(term)
    # Java != JavaScript; repeated keyword stuffing never increases the score.
    pattern = re.escape(term)
    if term and term[0].isascii() and term[0].isalnum():
        pattern = r"(?<![a-z0-9])" + pattern
    if term and term[-1].isascii() and term[-1].isalnum():
        pattern += r"(?![a-z0-9])"
    return bool(term and re.search(pattern, text))


def recommend(job, *, root=ROOT):
    entries, _ = discover(root)
    # Company name is deliberately absent. Only whitelisted public job fields.
    fields = [("title", job.get("title", job.get("job_title", "")), None),
              ("job_family", job.get("job_family") or "", None)]
    fields += [("responsibilities", text, None) for text in job.get("responsibilities", []) if isinstance(text, str)]
    fields += [("requirements", r.get("statement", ""), r.get("id")) for r in job.get("requirements", [])]
    fields += [("tags", tag, None) for tag in job.get("tags", []) if isinstance(tag, str)]
    ranked = []
    for identity, entry in sorted(entries.items()):
        m = entry["manifest"]
        rules = m["matching"]
        evidence, groups, score = [], set(), 0
        excluded = any(contains(text, term) for field, text, _ in fields if field in ("title", "responsibilities") for term in rules["exclusions"])
        for signal, field_names, terms in (
            ("category", ("job_family",), rules["categories"]),
            ("title", ("title",), rules["title_aliases"]),
            ("responsibility", ("responsibilities", "requirements"), rules["responsibility_terms"]),
        ):
            hits = [dict(field=field, requirement_id=ref, term=term, excerpt=text[:200])
                for field, text, ref in fields if field in field_names for term in terms if contains(text, term)]
            if hits:
                score += WEIGHTS[signal]
                evidence.extend(hits[:3])
                groups.add(signal)
        for aliases in rules["capability_groups"]:
            hit = next((dict(field=field, requirement_id=ref, term=term, excerpt=text[:200])
                for field, text, ref in fields if field in ("requirements", "responsibilities", "tags")
                for term in aliases if contains(text, term)), None)
            if hit:
                score += WEIGHTS["capability_group"]
                evidence.append(hit)
        ranked.append(dict(skill_id=identity, version=m["version"], label=m["label"],
            ranking_points=score, excluded=excluded, contextual=bool(groups), evidence=evidence,
            reason="岗位的" + "、".join(dict.fromkeys(e["term"] for e in evidence[:3])) + "与该专业规则相符。" if evidence else "当前岗位字段缺少该专业的明确依据。"))
    ranked.sort(key=lambda r: (r["excluded"], -r["ranking_points"], r["skill_id"]))
    for index, item in enumerate(ranked, 1):
        item["rank"] = index
    eligible = [r for r in ranked if not r["excluded"]]
    first = eligible[0] if eligible else None
    margin = first["ranking_points"] - eligible[1]["ranking_points"] if len(eligible) > 1 else 999
    content = bool(job.get("requirements") or job.get("responsibilities"))
    status = ("INSUFFICIENT_DATA" if not content else "NO_APPLICABLE_SKILL" if not first or not first["contextual"] or first["ranking_points"] < MIN_SCORE else
              "AMBIGUOUS" if margin < MIN_MARGIN else "RECOMMENDED")
    automatic = status == "RECOMMENDED"
    messages = dict(INSUFFICIENT_DATA="岗位职责或要求不足，请手动选择专业 Skill。",
        NO_APPLICABLE_SKILL="当前目录没有充分适用的专业规则，请手动选择或使用通用策略。",
        AMBIGUOUS="多个专业方向接近，请结合本次面试目标手动选择。")
    return dict(policy_version=POLICY_VERSION, job_id=job.get("job_id"), jd_id=job.get("jd_id"),
        jd_fingerprint=job.get("jd_fingerprint"), status=status, auto_select=automatic,
        recommended_skill_id=first["skill_id"] if automatic else None,
        recommended_version=first["version"] if automatic else None,
        reason=first["reason"] if automatic else messages[status], ranking=ranked)


async def recommend_jobs(db, job_ids):
    """Batch public reads; no N+1 queries and no full raw JD reparsing."""
    ids = sorted(set(job_ids))
    if not ids:
        return {}
    rows = (await db.execute(select(Job, JobDescriptionVersion).join(JobDescriptionVersion,
        Job.id == JobDescriptionVersion.job_id).where(Job.id.in_(ids), Job.status == "ACTIVE",
        JobDescriptionVersion.publication_status == "PUBLISHED")
        .order_by(Job.id, JobDescriptionVersion.version_number.desc(), JobDescriptionVersion.id.desc()))).all()
    latest = {}
    for job, jd in rows:
        latest.setdefault(job.id, (job, jd))
    jd_ids = [jd.id for _, jd in latest.values()]
    requirements = (await db.scalars(select(JobRequirement).where(JobRequirement.job_description_version_id.in_(jd_ids)).order_by(JobRequirement.id))).all()
    tags = (await db.scalars(select(JobTag).where(JobTag.job_id.in_(ids)).order_by(JobTag.id))).all()
    grouped, tag_groups = {}, {}
    for r in requirements:
        grouped.setdefault(r.job_description_version_id, []).append(dict(id=r.id, statement=r.statement))
    for tag in tags:
        tag_groups.setdefault(tag.job_id, []).append(tag.tag)
    result = {}
    for identity, (job, jd) in latest.items():
        structured = jd.structured_data_json if isinstance(jd.structured_data_json, dict) else {}
        # Older public extraction stores requirements only. Do not invent duties.
        duties = structured.get("responsibilities", [])
        if isinstance(duties, str): duties = [duties]
        duties = [d.get("statement", "") if isinstance(d, dict) else d for d in duties] if isinstance(duties, list) else []
        result[identity] = recommend(dict(job_id=identity, title=job.canonical_title, job_family=job.job_family,
            jd_id=jd.id, jd_fingerprint=jd.content_fingerprint, responsibilities=duties,
            requirements=grouped.get(jd.id, []), tags=tag_groups.get(identity, [])))
    return result


def attach_to_match(candidate):
    """Response-only annotation from the MatchRun's frozen inputs, never ranking."""
    return {**candidate, "interviewer_skill_recommendation": recommend(candidate)}
