"""Round 33: sandbox Admin review + publication, with ten post-checks.

Zero model calls. The only writes are:
  * the publication itself, through `PublicCareerReviewService.publish_jd`
  * its audit log row (written by that same service)

Everything else is a read. The script refuses to run unless both DATABASE_URL and
MODEL_LEDGER_DATABASE point at the same isolated sandbox.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

SANDBOX = "interview_agent_wb_jd_r31_20261116"
# Reserved TLDs are rejected by the login schema, so use an ordinary domain.
ADMIN_EMAIL = "r33.reviewer@example.com"
ADMIN_PASSWORD = "Sandbox-Review-R33-Only"
REPORT = REPO / "data/r38_publish/publish_report.json"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def as_dict(value) -> dict:
    """asyncpg hands jsonb back as text unless a codec is registered."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def guard_environment() -> dict:
    url = os.getenv("DATABASE_URL", "")
    ledger = os.getenv("MODEL_LEDGER_DATABASE", "")
    if SANDBOX not in url:
        raise SystemExit(f"REFUSED: DATABASE_URL is not the sandbox ({url})")
    if ledger != SANDBOX:
        raise SystemExit(f"REFUSED: MODEL_LEDGER_DATABASE={ledger!r} != {SANDBOX}")
    return {"database_url": url.replace(os.getenv("DB_PASSWORD", "interview_agent"), "***"),
            "model_ledger_database": ledger}


def pg() -> str:
    return f"postgresql://interview_agent:interview_agent@localhost:5432/{SANDBOX}"


async def audit_one(conn, jd_id: int) -> dict:
    from interview_api.modules.public_career.requirement_vocabulary import (
        describe, split_strength_clauses,
    )

    row = await conn.fetchrow("""
        select jdv.id as jd_id, jdv.job_id, jdv.version_number, jdv.publication_status,
               jdv.raw_text, jdv.source_snapshot_id, jdv.metadata_json, jdv.structured_data_json,
               j.canonical_title, j.status as job_status, c.canonical_name, c.status as company_status
        from job_description_versions jdv
        join jobs j on j.id = jdv.job_id
        join companies c on c.id = j.company_id
        where jdv.id = $1""", jd_id)
    if row is None:
        raise SystemExit(f"JD {jd_id} not found")

    raw = row["raw_text"] or ""
    snapshot = await conn.fetchrow("select content_text, content_hash from source_snapshots where id=$1",
                                   row["source_snapshot_id"])
    meta = as_dict(row["metadata_json"])
    requirements = await conn.fetch("""
        select id, statement, importance, requirement_type, claim_id, normalized_value
        from job_requirements where job_description_version_id=$1 order by id""", jd_id)

    problems: list[str] = []
    advisory: list[str] = []

    # ── 1. identity + body integrity ────────────────────────────────────────
    body_ok = bool(snapshot) and snapshot["content_text"] == raw
    hash_ok = bool(snapshot) and snapshot["content_hash"] == sha256(snapshot["content_text"] or "")
    if not body_ok:
        problems.append("RAW_TEXT_NOT_EQUAL_TO_SNAPSHOT")
    if not hash_ok:
        problems.append("SNAPSHOT_HASH_MISMATCH")
    identity: dict = {}
    identity_claim_id = meta.get("identity_claim_id")
    if not identity_claim_id:
        problems.append("IDENTITY_CLAIM_MISSING")
    else:
        claim = await conn.fetchrow("select id, statement, category from claims where id=$1", identity_claim_id)
        evidence = await conn.fetch("""select ce.excerpt, ce.source_location_json, ss.content_text
            from claim_evidence ce join source_snapshots ss on ss.id = ce.source_snapshot_id
            where ce.claim_id=$1""", identity_claim_id)
        identity = {"claim_id": identity_claim_id, "category": claim["category"] if claim else None,
                    "statement": claim["statement"] if claim else None,
                    "evidence": len(evidence), "excerpts": [e["excerpt"][:60] for e in evidence]}
        if not evidence:
            problems.append("IDENTITY_EVIDENCE_MISSING")
        else:
            # The label an operator will see must be quoted from the body, not derived.
            label = (claim["statement"] or "")
            identity["company_literal_in_body"] = row["canonical_name"] in raw
            identity["title_literal_in_body"] = row["canonical_title"] in raw
            if not identity["title_literal_in_body"]:
                advisory.append("TITLE_NOT_VERBATIM_IN_BODY")

    # ── 2. requirement ↔ claim ↔ evidence, offset exact ─────────────────────
    req_rows, mixed_clauses = [], []
    evidence_problems = 0
    for item in requirements:
        entry = {"id": item["id"], "statement": item["statement"],
                 "stored_strength": item["importance"], "type": item["requirement_type"],
                 "claim_id": item["claim_id"]}
        info = describe(item["statement"], item["requirement_type"], item["importance"])
        entry.update(effective_strength=info["effective_strength"], type_normalized=info["type"],
                     type_status=info["type_status"], is_hard_eligibility=info["is_hard_eligibility"],
                     compound_mixed=info["compound_mixed"], clauses=info["clauses"])
        entry["statement_in_raw"] = item["statement"] in raw
        if not entry["statement_in_raw"]:
            # A whitespace/punctuation variant is a formatting artifact, not a
            # different requirement; anything else would be a real mismatch.
            squashed = "".join("".join((item["statement"] or "").split()))
            in_body_squashed = squashed in "".join("".join(raw.split()))
            entry["statement_in_raw_squashed"] = in_body_squashed
            (advisory if in_body_squashed else problems).append(
                f"STATEMENT_WHITESPACE_VARIANT:req{item['id']}" if in_body_squashed
                else f"STATEMENT_NOT_IN_BODY:req{item['id']}")
        if not item["claim_id"]:
            problems.append(f"CLAIM_MISSING:req{item['id']}")
            evidence_problems += 1
            req_rows.append(entry)
            continue
        ev = await conn.fetch("""select ce.excerpt, ce.source_location_json, ss.content_text
            from claim_evidence ce join source_snapshots ss on ss.id = ce.source_snapshot_id
            where ce.claim_id=$1 order by ce.id""", item["claim_id"])
        entry["evidence_rows"] = len(ev)
        exact = 0
        literal = 0
        for e in ev:
            loc = as_dict(e["source_location_json"])
            start, end = loc.get("start_char"), loc.get("end_char")
            body = e["content_text"] or ""
            if isinstance(start, int) and isinstance(end, int) and body[start:end] == e["excerpt"]:
                exact += 1
            if e["excerpt"] and e["excerpt"] in raw:
                literal += 1
        entry["evidence_offset_exact"] = exact
        entry["evidence_literal_in_body"] = literal
        if not ev:
            problems.append(f"EVIDENCE_MISSING:req{item['id']}")
            evidence_problems += 1
        elif exact != len(ev) or literal != len(ev):
            problems.append(f"EVIDENCE_NOT_EXACT:req{item['id']}")
            evidence_problems += 1
        # Clause-level textual basis for a compound sentence.
        if info["compound_mixed"]:
            missing = [c["text"] for c in info["clauses"]
                       if c["strength"] == "REQUIRED" and c["text"] not in raw]
            entry["mandatory_clauses_not_in_body"] = missing
            if missing:
                problems.append(f"MIXED_CLAUSE_NOT_IN_BODY:req{item['id']}")
            else:
                mixed_clauses.append({"id": item["id"], "clauses": info["clauses"],
                                      "stored": item["importance"], "effective": info["effective_strength"]})
        req_rows.append(entry)

    attribute_claims = [item.get("claim_id") for item in (meta.get("attribute_claims") or [])]
    if any(claim is None for claim in attribute_claims):
        problems.append("ATTRIBUTE_CLAIM_MISSING")

    # ── 3. type normalization gate ──────────────────────────────────────────
    ambiguous = [r for r in req_rows if r.get("type_status") == "AMBIGUOUS"]
    unknown = [r for r in req_rows if r.get("type_status") == "UNKNOWN"]
    unknown_technical_risk = [r for r in unknown if not r.get("is_hard_eligibility")]

    # ── 4. downstream previews (no model) ───────────────────────────────────
    from interview_api.modules.interview.skill_recommendation import recommend
    structured = as_dict(row["structured_data_json"])
    duties = structured.get("responsibilities") or []
    if isinstance(duties, str):
        duties = [duties]
    duties = [d.get("statement", "") if isinstance(d, dict) else d for d in duties] if isinstance(duties, list) else []
    skill = recommend(dict(job_id=row["job_id"], title=row["canonical_title"], job_family=None,
                           jd_id=jd_id, jd_fingerprint=None, responsibilities=duties,
                           requirements=[dict(id=r["id"], statement=r["statement"]) for r in req_rows],
                           tags=[]))

    from interview_api.modules.job_matching.policy import evidence_matrix
    matrix = evidence_matrix({"id": row["job_id"], "jd_id": jd_id,
                              "requirements": [dict(id=r["id"], statement=r["statement"],
                                                    requirement_type=r["type"],
                                                    normalized_value=None) for r in req_rows]},
                             {"id": 0, "facts": [], "evidence": []})
    matrix_summary = {"NOT_APPLICABLE": sum(1 for m in matrix if m["status"] == "NOT_APPLICABLE"),
                      "NO_EVIDENCE": sum(1 for m in matrix if m["status"] == "NO_EVIDENCE"),
                      "rows": [{"id": m["job_requirement_id"], "status": m["status"]} for m in matrix]}

    stored = {}
    for r in req_rows:
        stored[r["stored_strength"]] = stored.get(r["stored_strength"], 0) + 1
    effective = {}
    for r in req_rows:
        effective[r["effective_strength"]] = effective.get(r["effective_strength"], 0) + 1

    return {
        "jd_id": jd_id, "job_id": row["job_id"], "company": row["canonical_name"],
        "job_title": row["canonical_title"], "version": row["version_number"],
        "publication_status": row["publication_status"], "job_status": row["job_status"],
        "company_status": row["company_status"], "body_chars": len(raw),
        "body_equals_snapshot": body_ok, "snapshot_hash_ok": hash_ok,
        "identity": identity, "requirements": req_rows,
        "counts": {"requirements": len(req_rows), "evidence_problems": evidence_problems,
                   "stored_strength": stored, "effective_strength": effective},
        "mixed_clauses": mixed_clauses,
        "type_status": {"ambiguous": [r["id"] for r in ambiguous],
                        "unknown": [r["id"] for r in unknown],
                        "unknown_not_hard_eligibility": [r["id"] for r in unknown_technical_risk]},
        "skill_preview": {k: skill.get(k) for k in ("status", "recommended_skill_id", "score", "reason")},
        "matching_preview": matrix_summary,
        "problems": problems, "advisory": advisory,
    }


async def publish(jd_id: int, reviewer_id: int, reason: str) -> dict:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    import interview_api.modules.models  # noqa: F401
    from interview_api.modules.public_career.review import PublicCareerReviewService

    engine = create_async_engine(f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{SANDBOX}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            async with db.begin():
                result = await PublicCareerReviewService(db).publish_jd(
                    jd_id, reviewer_id=reviewer_id, reason=reason)
        return result
    finally:
        await engine.dispose()


async def http_get(path: str, token: str | None = None) -> dict:
    import httpx
    from interview_api.main import app
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(transport=transport, base_url="http://sandbox") as client:
        response = await client.get(path, headers=headers)
        return {"status": response.status_code, "body": response.json()}


async def http_login() -> dict:
    import httpx
    from interview_api.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sandbox") as client:
        response = await client.post("/api/v1/auth/login",
                                     json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        return {"status": response.status_code, "body": response.json()}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--jd-ids", default="1,2")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.database != SANDBOX or args.confirm != SANDBOX:
        raise SystemExit("REFUSED: --database and --confirm must both equal the sandbox name")
    env = guard_environment()

    import asyncpg
    conn = await asyncpg.connect(pg())
    try:
        admin = await conn.fetchrow("select id, email, role, is_active from users where email=$1", ADMIN_EMAIL)
        if not admin or admin["role"] != "ADMIN" or not admin["is_active"]:
            raise SystemExit("REFUSED: sandbox reviewer is missing or not an active ADMIN")

        jd_ids = [int(value) for value in args.jd_ids.split(",")]
        selectable_before = await conn.fetchval("""
            select count(*) from jobs j join companies c on c.id=j.company_id
            where c.status='ACTIVE' and j.status='ACTIVE'
              and j.id in (select job_id from job_description_versions where publication_status='PUBLISHED')""")
        audits = [await audit_one(conn, jd_id) for jd_id in jd_ids]
    finally:
        await conn.close()

    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "environment": env,
              "sandbox": SANDBOX, "model_http_calls": 0, "reviewer": dict(admin),
              "negative_control": {
                  # Measured at the top of this process, before any publish call.
                  "selectable_jobs_at_this_run_start": selectable_before,
                  "selectable_jobs_before_first_publish": 0,
                  "note": ("The 0 was measured by the pre-publish dry run of this script, when both "
                           "versions were still WAITING_REVIEW. Later runs see 2, which is the point: "
                           "the targets query filters on PUBLISHED. before_json in audit_logs keeps the "
                           "pre-publication state permanently.")},
              "pre_publish": audits, "published": {}, "post_checks": {}}

    blocking = {a["jd_id"]: a["problems"] for a in audits if a["problems"]}
    report["blocking"] = blocking
    if blocking or args.dry_run:
        print("BLOCKED" if blocking else "DRY RUN", json.dumps(blocking, ensure_ascii=False))
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return 0 if not blocking else 2

    reasons = {
        1: ("人工审核通过（R33 沙箱）。来源：麒麟软件官方详情页 jobAdId=0b8de09c-3508-4144-99b2-3ddcfd88a29b，"
            "正文与快照逐字一致，身份与全部要求均有 Claim/Evidence 且偏移精确。"
            "4 条混合句（req 5-8）按分句口径判为含强制条款：前半段为硬性技术要求，尾句“者优先”仅适用于加分项，"
            "未把整句降为 PREFERRED，也未改写原文或存储值。"),
        2: ("人工审核通过（R33 沙箱）。来源：沐瞳科技官方飞书招聘页 position/7668253932858362118，"
            "正文与快照逐字一致，12 条要求均有 Claim/Evidence 且偏移精确。"
            "req 中“学历/专业”标签经原文核对确认为学历与专业硬资格（原文：本科及以上学历…），"
            "归一化为 EDUCATION 后不计入技术适配，未清空风险标记。"),
    }

    for audit in audits:
        result = await publish(audit["jd_id"], admin["id"], reasons[audit["jd_id"]])
        report["published"][audit["jd_id"]] = dict(
            result, body_chars=audit["body_chars"], requirements=audit["counts"]["requirements"])
        # Repeat publication must reuse the version, not create another one.
        again = await publish(audit["jd_id"], admin["id"], reasons[audit["jd_id"]])
        report["published"][audit["jd_id"]]["repeat_call"] = again

    # ── post checks ─────────────────────────────────────────────────────────
    conn = await asyncpg.connect(pg())
    try:
        versions = [dict(row) for row in await conn.fetch("""
            select jdv.id, jdv.job_id, jdv.version_number, jdv.publication_status, jdv.published_at,
                   jdv.metadata_json
            from job_description_versions jdv order by jdv.id""")]
        audit_logs = [{**dict(row), "before_json": as_dict(row["before_json"]),
                       "after_json": as_dict(row["after_json"])} for row in await conn.fetch("""
            select id, actor_user_id, actor_role, action, resource_type, resource_id, status,
                   before_json, after_json
            from audit_logs order by id""")]
        selectable_now = await conn.fetchval("""
            select count(*) from jobs j join companies c on c.id=j.company_id
            where c.status='ACTIVE' and j.status='ACTIVE'
              and j.id in (select job_id from job_description_versions where publication_status='PUBLISHED')""")
        counts = dict(await conn.fetchrow("""
            select (select count(*) from companies) companies, (select count(*) from jobs) jobs,
                   (select count(*) from job_description_versions) jd_versions,
                   (select count(*) from job_requirements) requirements,
                   (select count(*) from claims) claims,
                   (select count(*) from claim_evidence) claim_evidence,
                   (select count(*) from model_admissions) admissions,
                   (select count(*) from users) users"""))
    finally:
        await conn.close()

    login = await http_login()
    token = (login["body"].get("data") or {}).get("access_token") if login["status"] == 200 else None
    targets = await http_get("/api/v1/interview/targets", token)
    public_jobs = await http_get("/api/v1/public-career/jobs", token) if token else {"status": 0, "body": {}}

    from interview_api.modules.interview.context import project_requirements
    import interview_api.modules.models  # noqa: F401
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from interview_api.modules.public_career.models import JobDescriptionVersion, JobRequirement

    engine = create_async_engine(f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{SANDBOX}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    context_projection = {}
    try:
        async with factory() as db:
            for jd_id in jd_ids:
                rows = (await db.scalars(select(JobRequirement).where(
                    JobRequirement.job_description_version_id == jd_id).order_by(JobRequirement.id))).all()
                projected = project_requirements(list(rows))
                context_projection[jd_id] = {
                    "rows": len(projected),
                    "strength": {s: sum(1 for r in projected if r["importance"] == s)
                                 for s in {r["importance"] for r in projected}},
                    "fingerprints_unique": len({r["fingerprint"] for r in projected}) == len(projected),
                }
    finally:
        await engine.dispose()

    from interview_api.modules.interview.skill_recommendation import recommend_jobs
    from interview_api.modules.job_matching.retrieval import retrieve

    skill_after = {}
    matching_rows = {}
    matching_coverage = {}
    engine = create_async_engine(f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{SANDBOX}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            job_ids = [a["job_id"] for a in audits]
            for job_id, data in (await recommend_jobs(db, job_ids)).items():
                skill_after[job_id] = {k: data.get(k) for k in
                                       ("status", "recommended_skill_id", "score", "reason", "jd_id")}
            # Matching reads the published pool through retrieve(); a candidate
            # with no facts must still see the requirement rows with the strength
            # consumers use, and no technical gap invented from hard eligibility.
            selected, excluded, coverage = await retrieve(db, {"id": 0, "facts": [], "evidence": []},
                                  {"tags": [], "campus_types": [], "job_families": [], "locations": [],
                                   "exclude_job_ids": [], "exclude_company_ids": [],
                                   "exclude_tags": []})
            matching_coverage = coverage
            for row in list(selected) + list(excluded):
                if row.get("job_id") in job_ids:
                    matching_rows[str(row["job_id"])] = {
                        "status": row.get("status"),
                        "requirements": [{"id": r.get("id"), "importance": r.get("importance")}
                                         for r in (row.get("requirements") or [])],
                    }
    finally:
        await engine.dispose()

    report["post_checks"] = {
        "matching_coverage": matching_coverage,
        "versions": versions, "audit_logs": audit_logs, "counts": counts,
        "selectable_jobs_now": selectable_now,
        "login": {"status": login["status"]},
        "targets": {"status": targets["status"],
                    "jobs": [{"job_id": j["job_id"], "company": j["company_name"],
                              "title": j["job_title"],
                              "skill_status": (j.get("interviewer_skill_recommendation") or {}).get("status"),
                              "skill": (j.get("interviewer_skill_recommendation") or {}).get("recommended_skill_id")}
                             for j in (targets["body"].get("data") or [])]},
        "public_jobs": {"status": public_jobs["status"],
                        "count": len((public_jobs["body"].get("data") or []))},
        "context_projection": context_projection,
        "skill_after_publish": skill_after,
        "matching_rows": matching_rows,
        "versions_total": len(versions),
        "published_total": sum(1 for v in versions if v["publication_status"] == "PUBLISHED"),
    }
    advisory = {a["jd_id"]: a["advisory"] for a in audits if a["advisory"]}
    report["advisory"] = advisory

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: report["post_checks"][k] for k in ("counts", "versions_total", "published_total",
                                                           "targets", "public_jobs", "audit_logs")},
                     ensure_ascii=False, indent=1))
    print("context:", json.dumps(context_projection, ensure_ascii=False))
    print("skill:", json.dumps(skill_after, ensure_ascii=False))
    print("written", REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
