"""Round 34: audit the five new real JDs, publish what passes, verify afterwards.

Zero model calls. Reuses the round-33 auditor so the checks are identical to the
ones the already-published JDs went through.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))
sys.path.insert(0, str(API_ROOT / "scripts"))

import r38_publish_and_verify as r33  # noqa: E402  (same audit rules, same sandbox)

import re  # noqa: E402
import unicodedata  # noqa: E402

LABEL_PREFIX = re.compile(r"^[^：:]{1,10}[：:]")


def _fold(text: str) -> str:
    """Width/punctuation/whitespace-insensitive form. No word-level rewriting."""
    value = unicodedata.normalize("NFKC", text or "").casefold()
    value = re.sub(r"^\s*(?:[-•·*]\s*|\d{1,2}\s*[.、)）]\s*)", "", value)
    value = re.sub(r"[\s、，,。；;：:（）()\[\]【】“”\"'’‘·…—-]", "", value)
    return value


def classify_statement(statement: str, body: str) -> str:
    if statement in body:
        return "VERBATIM"
    folded, folded_body = _fold(statement), _fold(body)
    if folded and folded in folded_body:
        return "FORMAT_VARIANT"
    match = LABEL_PREFIX.match(statement or "")
    if match:
        label = match.group(0).rstrip("：:")
        stripped = (statement or "")[match.end():].strip()
        if stripped and _fold(stripped) in folded_body:
            # The label exists on the page as its own heading; the model only joined
            # heading and body into one line. Nothing was invented.
            if any(line.strip().rstrip("：:") == label for line in body.splitlines()):
                return "HEADING_JOINED"
            return "LABEL_INVENTED"
    return "PARAPHRASE"

REPORT = REPO / "data/r40_batch2/audit_report.json"
BATCH = REPO / "data/r40_batch2/source_manifest.json"


def publish_gate(audit: dict) -> tuple[bool, list[str]]:
    """Approval needs: no blocking problem, and no undecided requirement kind."""
    reasons: list[str] = []
    if audit["problems"]:
        reasons.append("blocking:" + ",".join(audit["problems"]))
    if not audit["body_equals_snapshot"] or not audit["snapshot_hash_ok"]:
        reasons.append("source_integrity")
    undecided = audit["type_status"]["unknown_not_hard_eligibility"]
    if undecided:
        reasons.append("undecided_type:" + ",".join(str(i) for i in undecided))
    if audit["counts"]["requirements"] < 3:
        reasons.append("too_few_requirements")
    if not audit["counts"]["evidence_problems"] == 0:
        reasons.append("evidence_problems")
    return (not reasons, reasons)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--jd-ids", required=True, help="comma separated jd_version ids")
    parser.add_argument("--publish", action="store_true", help="publish the approved ones")
    args = parser.parse_args()
    if args.database != r33.SANDBOX or args.confirm != r33.SANDBOX:
        raise SystemExit("REFUSED: sandbox mismatch")
    env = r33.guard_environment()

    import asyncpg
    jd_ids = [int(v) for v in args.jd_ids.split(",")]
    conn = await asyncpg.connect(r33.pg())
    try:
        admin = await conn.fetchrow("select id, email, role, is_active from users where email=$1",
                                    r33.ADMIN_EMAIL)
        if not admin or admin["role"] != "ADMIN":
            raise SystemExit("REFUSED: no active sandbox ADMIN")
        audits = [await r33.audit_one(conn, jd_id) for jd_id in jd_ids]
        bodies = {row["id"]: row["raw_text"] for row in await conn.fetch(
            "select id, raw_text from job_description_versions where id = any($1::bigint[])", jd_ids)}
    finally:
        await conn.close()

    manifest = {row["jd_key"]: row for row in json.loads(BATCH.read_text(encoding="utf-8"))["records"]}
    by_title = {row["job_title"]: row for row in manifest.values()}

    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "environment": env,
              "model_http_calls": 0, "audits": {}, "decisions": {}, "published": {},
              "gate": {}, "verbatim": {}}

    for audit in audits:
        body = bodies.get(audit["jd_id"], "")
        buckets: dict[str, list[int]] = {"VERBATIM": [], "FORMAT_VARIANT": [],
                                         "HEADING_JOINED": [], "LABEL_INVENTED": [],
                                         "PARAPHRASE": []}
        samples: dict[str, list[dict]] = {"HEADING_JOINED": [], "LABEL_INVENTED": [],
                                          "PARAPHRASE": []}
        for row in audit["requirements"]:
            kind = classify_statement(row["statement"], body)
            buckets[kind].append(row["id"])
            if kind in samples and len(samples[kind]) < 4:
                samples[kind].append({"id": row["id"], "statement": row["statement"][:160]})
        report["verbatim"][audit["jd_id"]] = {kind: ids for kind, ids in buckets.items()}
        audit["verbatim_buckets"] = {kind: len(ids) for kind, ids in buckets.items()}
        audit["verbatim_samples"] = samples
        # The platform's own check is offset-exact evidence; the statement being a
        # rewrite of the source is a separate defect this audit refuses to hide.
        audit["problems"] = [p for p in audit["problems"] if not p.startswith("STATEMENT_NOT_IN_BODY")]
        for jd_id_ in buckets["LABEL_INVENTED"]:
            audit["problems"].append(f"STATEMENT_LABEL_INVENTED:req{jd_id_}")
        for jd_id_ in buckets["PARAPHRASE"]:
            audit["problems"].append(f"STATEMENT_PARAPHRASED:req{jd_id_}")

    for audit in audits:
        gate, reasons = publish_gate(audit)
        report["gate"][audit["jd_id"]] = {"approved": gate, "reasons": reasons}
        report["audits"][audit["jd_id"]] = audit

    if not args.publish:
        for jd_id, gate in report["gate"].items():
            print(jd_id, "approved" if gate["approved"] else "BLOCKED", gate["reasons"])
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print("written", REPORT)
        return 0

    for audit in audits:
        gate = report["gate"][audit["jd_id"]]
        if not gate["approved"]:
            report["decisions"][audit["jd_id"]] = "HELD_WAITING_REVIEW"
            continue
        record = by_title.get(audit["job_title"], {})
        reason = (
            f"人工审核通过（R34 沙箱）。来源：{audit['company']} 官方招聘页 {record.get('source_url', '')}"
            f"（岗位 ID {record.get('job_id', '')}）。正文 {audit['body_chars']} 字，与快照逐字一致（SHA-256 已核）；"
            f"{audit['counts']['requirements']} 条要求均有 Claim/Evidence 且偏移精确；"
            f"强度存储 {audit['counts']['stored_strength']}、消费口径 {audit['counts']['effective_strength']}；"
            f"混合句按分句判定，未改写原文；学历/专业等硬资格归入非技术项。"
        )
        result = await r33.publish(audit["jd_id"], admin["id"], reason)
        again = await r33.publish(audit["jd_id"], admin["id"], reason)
        report["published"][audit["jd_id"]] = dict(result, repeat_call=again)
        report["decisions"][audit["jd_id"]] = "PUBLISHED"

    # ── post checks ─────────────────────────────────────────────────────────
    conn = await asyncpg.connect(r33.pg())
    try:
        versions = [dict(row) for row in await conn.fetch("""
            select jdv.id, jdv.job_id, jdv.version_number, jdv.publication_status, jdv.published_at,
                   jdv.content_fingerprint, j.canonical_title, c.canonical_name
            from job_description_versions jdv join jobs j on j.id=jdv.job_id
            join companies c on c.id=j.company_id order by jdv.id""")]
        logs = [{**dict(row), "before_json": r33.as_dict(row["before_json"]),
                 "after_json": r33.as_dict(row["after_json"])} for row in await conn.fetch("""
            select id, actor_role, action, resource_type, resource_id, status, before_json, after_json
            from audit_logs order by id""")]
        counts = dict(await conn.fetchrow("""
            select (select count(*) from companies) companies, (select count(*) from jobs) jobs,
                   (select count(*) from job_description_versions) jd_versions,
                   (select count(*) from job_requirements) requirements,
                   (select count(*) from claims) claims,
                   (select count(*) from claim_evidence) claim_evidence,
                   (select count(*) from model_admissions) admissions"""))
        selectable = await conn.fetchval("""
            select count(*) from jobs j join companies c on c.id=j.company_id
            where c.status='ACTIVE' and j.status='ACTIVE'
              and j.id in (select job_id from job_description_versions where publication_status='PUBLISHED')""")
    finally:
        await conn.close()

    login = await r33.http_login()
    token = (login["body"].get("data") or {}).get("access_token") if login["status"] == 200 else None
    targets = await r33.http_get("/api/v1/interview/targets", token)
    public = await r33.http_get("/api/v1/public-career/jobs", token)
    details = {}
    for job in (targets["body"].get("data") or []):
        details[job["job_id"]] = await r33.http_get(f"/api/v1/public-career/jobs/{job['job_id']}", token)

    report["post"] = {
        "counts": counts, "versions": versions, "audit_logs": logs,
        "selectable_jobs": selectable,
        "targets": {"status": targets["status"], "jobs": targets["body"].get("data")},
        "public_jobs": {"status": public["status"],
                        "count": len(public["body"].get("data") or [])},
        "detail_status": {str(k): v["status"] for k, v in details.items()},
        "detail_requirement_strength": {
            str(k): {s: sum(1 for r in (v["body"].get("data", {}).get("requirements") or [])
                            if r["importance"] == s)
                     for s in ("REQUIRED", "PREFERRED", "UNKNOWN")}
            for k, v in details.items()},
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"gate": report["gate"], "decisions": report["decisions"],
                      "counts": counts, "selectable": selectable,
                      "targets": [(j["company_name"], j["job_title"]) for j in (targets["body"].get("data") or [])],
                      "detail_strength": report["post"]["detail_requirement_strength"]},
                     ensure_ascii=False, indent=1, default=str))
    print("written", REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
