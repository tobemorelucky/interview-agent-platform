"""R123 stage 2: build an isolated showcase DB with the 50 curated JD records.

NEW database only (interview_agent_showcase_r123_20261117). No production / JD
sandbox / historical Live writes. No model calls. No fake Evidence: these are
"recruitment-information curated versions" from the supplied package, imported
as display/practice jobs with explicit provenance and NO Claim/Evidence rows.

Each of the 50 logical jobs becomes Company + Job + one PUBLISHED
JobDescriptionVersion + rule-parsed JobRequirements (claim_id=NULL). The public
read path (published_jobs / /interview/targets / /career) then sees 50 jobs.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import asyncpg

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

TARGET = "interview_agent_showcase_r123_20261117"
ROOT_DSN = "postgresql://interview_agent:interview_agent@localhost:5432/postgres"
PG = "postgresql://interview_agent:interview_agent@localhost:5432/"
PKG = REPO / "data/r123_package"
OUT = REPO / "data/r123_import"
OUT.mkdir(parents=True, exist_ok=True)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold()
    return re.sub(r"[\s（）()\-_·、,，/.]", "", s)


def classify(statement: str) -> tuple[str, str]:
    """Rule-only, conservative: do not claim technical skill for unknown text."""
    st = statement or ""
    importance = "PREFERRED" if re.search(r"优先|加分|bonus", st) else "REQUIRED"
    if re.search(r"学历|本科|硕士|博士|毕业|届|专业|学位", st):
        rtype = "EDUCATION"
    elif re.search(r"经验|经历|年以上|实习", st):
        rtype = "EXPERIENCE"
    elif re.search(r"沟通|协作|团队|责任|主动|热情|学习", st):
        rtype = "SOFT_SKILL"
    elif re.search(r"熟悉|掌握|精通|熟练|了解|实现|开发|设计|理解|语言|框架|算法", st):
        rtype = "OTHER"  # do NOT assert SKILL without a reviewed model
    else:
        rtype = "UNKNOWN"
    return rtype, importance


async def main() -> int:
    jds = [json.loads(l) for l in (PKG / "jd_50.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(jds) == 50

    root = await asyncpg.connect(ROOT_DSN)
    try:
        exists = await root.fetchval("select count(*) from pg_database where datname=$1", TARGET)
        if not exists:
            await root.execute(f'CREATE DATABASE "{TARGET}"')
            created = True
        else:
            created = False
    finally:
        await root.close()

    env = dict(os.environ)
    env["DATABASE_URL"] = f"postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/{TARGET}"
    env["MODEL_LEDGER_DATABASE"] = TARGET
    env["PYTHONPATH"] = str(API_ROOT / "src")
    head = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                          cwd=str(API_ROOT), env=env, capture_output=True, text=True)
    if head.returncode != 0:
        print(head.stdout[-1500:], head.stderr[-1500:])
        raise SystemExit("alembic upgrade failed")
    version = subprocess.run([sys.executable, "-m", "alembic", "current"],
                             cwd=str(API_ROOT), env=env, capture_output=True, text=True)
    print("created" if created else "reused", TARGET, "|", version.stdout.strip()[-40:])

    conn = await asyncpg.connect(PG + TARGET)
    report = {"target": TARGET, "created": created, "imported": [], "companies": {},
              "notes": "CURATED_PARAPHRASE; no Claim/Evidence; rule-parsed requirements"}
    now = datetime.now(timezone.utc)
    try:
        # idempotent: refuse to double-import
        existing = await conn.fetchval("select count(*) from job_description_versions")
        if existing:
            print("ALREADY IMPORTED jd_versions=", existing)
            return 0
        for j in jds:
            company = j["company"]
            nname = norm(company)
            cid = report["companies"].get(nname)
            if cid is None:
                cid = await conn.fetchval(
                    "insert into companies(canonical_name,normalized_name,status,created_at,updated_at) "
                    "values($1,$2,'ACTIVE',$3,$3) returning id", company, nname, now)
                report["companies"][nname] = cid
            title = j["position"]
            ntitle = norm(title)
            jid = await conn.fetchval(
                "insert into jobs(company_id,canonical_title,normalized_title,status,created_at,updated_at) "
                "values($1,$2,$3,'ACTIVE',$4,$4) returning id", cid, title, ntitle, now)
            content = j["jd_text"]
            fp = sha256(content.encode("utf-8")).hexdigest()
            url = j["source_url"]
            url_hash = sha256(url.encode("utf-8")).hexdigest()
            sid = await conn.fetchval(
                "select id from sources where url_hash=$1", url_hash)
            if sid is None:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc or "unknown"
                sid = await conn.fetchval(
                    "insert into sources(canonical_url,url_hash,domain,source_type,metadata_json,created_at,updated_at) "
                    "values($1,$2,$3,'CURATED_PARAPHRASE',$4,$5,$5) returning id",
                    url, url_hash, domain,
                    json.dumps({"label": "招聘信息整理版", "source_kind": j.get("source_kind")}, ensure_ascii=False), now)
            snap_id = await conn.fetchval(
                "select id from source_snapshots where source_id=$1 and content_hash=$2", sid, fp)
            if snap_id is None:
                snap_id = await conn.fetchval(
                    "insert into source_snapshots(source_id,content_hash,fetched_at,title,content_text,"
                    "metadata_json,created_at,updated_at) values($1,$2,$3,$4,$5,$6,$3,$3) returning id",
                    sid, fp, now, title, content,
                    json.dumps({"provenance": "CURATED_PARAPHRASE", "not_verbatim_official": True}, ensure_ascii=False))
            metadata = {
                "display_label": "招聘信息整理版",
                "provenance": "CURATED_PARAPHRASE_FROM_REAL_JOB_POSTING",
                "manual_source": {
                    "source_type": "CURATED_PARAPHRASE",
                    "source_url": j["source_url"],
                    "source_platform": j.get("source_platform"),
                    "source_kind": j.get("source_kind"),
                    "jd_id": j["id"],
                },
                "source_verification": j.get("source_verification"),
                "live_opening_unverified": bool(j.get("live_opening_unverified")),
                "requirement_parse": "RULE_ONLY_NO_MODEL",
            }
            vid = await conn.fetchval(
                "insert into job_description_versions(job_id,version_number,raw_text,structured_data_json,"
                "metadata_json,source_snapshot_id,content_fingerprint,observed_at,created_at,updated_at,"
                "publication_status,published_at) values($1,1,$2,$3,$4,$5,$6,$7,$7,$7,'PUBLISHED',$7) returning id",
                jid, content, json.dumps({"responsibilities": j["responsibilities"],
                                          "requirements": j["requirements"]}, ensure_ascii=False),
                json.dumps(metadata, ensure_ascii=False), snap_id, fp, now)
            req_count = 0
            for stmt in j["requirements"]:
                rtype, importance = classify(stmt)
                await conn.execute(
                    "insert into job_requirements(job_description_version_id,requirement_type,normalized_value,"
                    "statement,importance,claim_id) values($1,$2,NULL,$3,$4,NULL)",
                    vid, rtype, stmt, importance)
                req_count += 1
            report["imported"].append({"jd_id": j["id"], "company": company, "title": title,
                                       "job_id": jid, "jd_version_id": vid, "requirements": req_count,
                                       "source_url": j["source_url"]})
        report["counts"] = {
            "companies": await conn.fetchval("select count(*) from companies"),
            "jobs": await conn.fetchval("select count(*) from jobs"),
            "jd_versions": await conn.fetchval("select count(*) from job_description_versions"),
            "published": await conn.fetchval("select count(*) from job_description_versions where publication_status='PUBLISHED'"),
            "requirements": await conn.fetchval("select count(*) from job_requirements"),
            "sources": await conn.fetchval("select count(*) from sources"),
            "source_snapshots": await conn.fetchval("select count(*) from source_snapshots"),
            "claims": await conn.fetchval("select count(*) from claims"),
            "claim_evidence": await conn.fetchval("select count(*) from claim_evidence"),
        }
    finally:
        await conn.close()
    (OUT / "import_50.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"counts": report["counts"], "companies": len(report["companies"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
