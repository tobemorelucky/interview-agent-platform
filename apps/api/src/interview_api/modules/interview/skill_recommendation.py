"""Deterministic job -> Skill alignment; no Agent, model, network or writes."""
import hashlib
import json
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


def recommend(job, *, root=ROOT, full_evidence=False):
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
                # `full_evidence` is only for the private-JD response projection.
                # The default keeps the historical (public/frozen) envelope
                # byte-for-byte: ranking, score and status never change.
                evidence.extend(hits if full_evidence else hits[:3])
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


def recommend_for_target(target, *, root=ROOT, full_evidence=False):
    """Same deterministic rules, fed from a private target's confirmed requirements.

    A private target is never written into the public tables this module reads,
    and its own text is used verbatim; nothing is inferred from a company name.
    `root` only exists so tests can point at an empty registry directory; the
    product path always uses the real local registry.
    """
    requirements = [dict(id=row.get("id"), statement=row.get("statement", ""))
                    for row in (target.requirements_json or [])]
    structured = target.structured_json if isinstance(target.structured_json, dict) else {}
    duties = structured.get("responsibilities") or []
    if isinstance(duties, str):
        duties = [duties]
    return recommend(dict(job_id=None, title=target.job_title, job_family=None, jd_id=None,
        jd_fingerprint=target.content_fingerprint,
        responsibilities=[d.get("statement", "") if isinstance(d, dict) else d for d in duties] if isinstance(duties, list) else [],
        requirements=requirements, tags=[]), root=root, full_evidence=full_evidence)


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


# ── Round 16: private-JD response projection ───────────────────────────────
# Ranking, status and auto_select come verbatim from recommend_for_target().
# This layer does NOT re-rank, invent relevance or write to the registry. It
# only attaches the user's own verbatim JD evidence, a staleness key that also
# covers edited requirements, and honest uncertainty labels for the response.

TARGET_SKILL_PROJECTION = "PRIVATE_TARGET_SKILL_V1"
RANKING_SEMANTICS = (
    "ranking_points 是公开匹配规则的整数权重之和"
    "（category / title / responsibility / capability_group），不是概率、百分比或模型置信度；"
    "同分按 skill_id 固定打破，排序可复核。"
)
TARGET_SKILL_DISCLAIMER = (
    "推荐只依据你已确认的私有 JD 要求与本地合法注册的 Skill 清单做确定性文本匹配。"
    "JD 由你提供、未经企业核实；推荐不代表企业官方要求，也不代表你已掌握该方向。"
    "推荐不是强制选择，最终面试只冻结你实际选择的专业 Skill 与版本。"
)
STATUS_NOTES = dict(
    NOT_CONFIRMED="该 JD 尚未确认：只有已确认版本才提供正式推荐。",
    INSUFFICIENT_DATA="已确认的 JD 要求不足，无法给出正式推荐，请手动选择专业 Skill。",
    NO_APPLICABLE_SKILL="当前 JD 要求与已注册的专业规则没有充分命中，请手动选择；不会编造相关性。",
    AMBIGUOUS="多个专业方向命中接近，系统不替你做唯一选择；请逐条查看证据后手动选用。",
    RECOMMENDED="已按确定性规则找到一个明确领先方向；你仍可手动更改。",
)


def target_skill_input_fingerprint(target):
    """Staleness key over exactly the fields the recommendation reads.

    `content_fingerprint` covers raw_text only: editing requirements leaves it
    unchanged, so it cannot tell whether an older recommendation is stale.
    """
    structured = target.structured_json if isinstance(target.structured_json, dict) else {}
    duties = structured.get("responsibilities") or []
    if isinstance(duties, str):
        duties = [duties]
    duty_texts = [d.get("statement", "") if isinstance(d, dict) else d for d in duties] if isinstance(duties, list) else []
    payload = dict(
        version=int(target.version or 0),
        job_title=target.job_title or "",
        requirements=[dict(id=row.get("id"), statement=row.get("statement", ""),
                           evidence=row.get("evidence") or None)
                      for row in (target.requirements_json or []) if isinstance(row, dict)],
        responsibilities=[str(text) for text in duty_texts],
    )
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _private_evidence(target, hit, rows):
    """Attach the confirmed requirement and its verbatim JD quote, or say why not."""
    field, rid, statement, verbatim = hit.get("field"), hit.get("requirement_id"), None, None
    if field == "requirements":
        row = rows.get(rid)
        if row is None:
            status = "REQUIREMENT_NOT_FOUND_IN_CONFIRMED_VERSION"
        else:
            statement, verbatim = row.get("statement"), row.get("evidence") or None
            status = "VERBATIM_FROM_JD" if verbatim else "USER_ADDED_NO_VERBATIM_QUOTE"
    elif field == "responsibilities":
        statement, status = hit.get("excerpt"), "STRUCTURED_DUTY_NOT_VERBATIM_CHECKED"
    elif field == "title":
        statement, status = target.job_title, "USER_SUPPLIED_TITLE_NOT_A_JD_QUOTE"
    else:
        statement, status = hit.get("excerpt"), "FIELD_NOT_VERBATIM_VERIFIABLE"
    return dict(field=field, requirement_id=rid, matched_term=hit.get("term"),
                excerpt=hit.get("excerpt"), requirement_statement=statement,
                verbatim_jd_evidence=verbatim, evidence_status=status)


def project_target_recommendation(target, recommendation=None, *, expected_key=None):
    """Read-only HTTP projection. `recommendation=None` means not yet confirmed.

    `expected_key` is the input_fingerprint a previous one-click hand-off was
    made against. It lets the server say "this recommendation is stale" without
    touching the session-creation path or any frozen snapshot.
    """
    base = dict(
        projection=TARGET_SKILL_PROJECTION, target_id=target.id,
        target_version=int(target.version or 0), parse_status=target.parse_status,
        confirmed_at=target.confirmed_at.isoformat() if target.confirmed_at else None,
        content_fingerprint=target.content_fingerprint,
        input_fingerprint=target_skill_input_fingerprint(target),
        source="DETERMINISTIC_RULE", model_calls=0, skill_registry_read_only=True,
        ranking_semantics=RANKING_SEMANTICS, disclaimer=TARGET_SKILL_DISCLAIMER,
    )
    stale = bool(expected_key) and (expected_key != base["input_fingerprint"]
                                    or target.parse_status != "CONFIRMED")
    base["stale"] = stale
    base["stale_reason"] = "该 JD 的确认版本或要求已变化，请先重新查看推荐。" if stale else None
    if recommendation is None:
        return dict(**base, policy_version=POLICY_VERSION, status="NOT_CONFIRMED",
            auto_select=False, recommended_skill_id=None, recommended_version=None,
            message=STATUS_NOTES["NOT_CONFIRMED"], reason=None,
            needs_manual_confirmation=True, candidates=[], empty_reason="JD_NOT_CONFIRMED",
            confidence_note="无置信度百分比；未确认 JD 不提供任何正式推荐。")
    rows = {row.get("id"): row for row in (target.requirements_json or []) if isinstance(row, dict)}
    candidates = []
    for item in recommendation["ranking"]:
        # The default (frozen/public) envelope keeps only hits[:3], which can
        # drop requirement rows. The private projection asks for all hits and
        # de-duplicates them here so verbatim JD evidence is never lost.
        evidence, seen = [], set()
        for hit in item["evidence"]:
            key = (hit.get("field"), hit.get("requirement_id"), hit.get("term"))
            if key in seen:
                continue
            seen.add(key)
            evidence.append(_private_evidence(target, hit, rows))
            if len(evidence) >= 12:
                break
        has_verbatim = any(e["evidence_status"] == "VERBATIM_FROM_JD" for e in evidence)
        eligible = bool(not item["excluded"] and item["contextual"] and item["ranking_points"] >= MIN_SCORE)
        candidates.append(dict(
            skill_id=item["skill_id"], version=item["version"], label=item["label"],
            rank=item["rank"], ranking_points=item["ranking_points"],
            excluded=item["excluded"], contextual=item["contextual"],
            eligible=eligible, uncertain=bool(item["excluded"] or not item["contextual"] or not has_verbatim),
            matched_reason=item["reason"], evidence=evidence,
            reason_note=("依据：命中你已确认的 JD 要求与本地注册规则；逐条原文证据见 evidence。"
                         if has_verbatim else
                         "依据不足：命中字段没有可核对的 JD 逐字原文，仅作方向参考，请手动确认。")))
    status = recommendation["status"]
    empty_reason = ("NO_REGISTERED_SKILL" if not candidates else
                    "NO_ELIGIBLE_CANDIDATE" if not any(c["eligible"] for c in candidates) else None)
    return dict(**base, policy_version=recommendation["policy_version"], status=status,
        auto_select=bool(recommendation["auto_select"]),
        recommended_skill_id=recommendation["recommended_skill_id"],
        recommended_version=recommendation["recommended_version"],
        message=STATUS_NOTES.get(status, recommendation["reason"]),
        reason=recommendation["reason"],
        needs_manual_confirmation=status in ("AMBIGUOUS", "INSUFFICIENT_DATA", "NO_APPLICABLE_SKILL"),
        candidates=candidates, empty_reason=empty_reason,
        confidence_note="不提供百分比或置信度分数；每个候选的 uncertain 与 evidence_status 标明证据是否可核对。")
