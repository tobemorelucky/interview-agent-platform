"""SHOWCASE-R1 / part 2 step 1: a single, evidence-backed inventory of the 52 JDs.

Read-only. For every corpus record it re-derives the artefacts from disk (body
text, DOM, hashes), classifies the technical direction from the page title, and
joins the current sandbox state (JD version, publication status, refresh task,
published skill recommendation). Nothing is inferred from earlier manifests: the
hashes are recomputed here.

Also emits the deep, per-field re-check for the six records of the next batch and
runs the real EvidenceCatalog chunker over their projected text, so the "line cut"
risk is measured against the code that is actually deployed rather than against a
report.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from hashlib import sha256
from pathlib import Path

import asyncpg

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from interview_api.modules.public_career.research.evidence import EvidenceCatalog  # noqa: E402

CORPUS = REPO / "data/incoming_20261116c/jd_corpus.json"
CAPTURED = REPO / "data/incoming_20261116c/captured"
DOM = CAPTURED / "dom"
BATCH4 = REPO / "data/r55_batch4/source_manifest.json"
OUT = REPO / "data/r56_showcase"
DSN = "postgresql://interview_agent:interview_agent@localhost:5432/interview_agent_wb_jd_r31_20261116"

# Titles that are not software engineering; kept separate so nobody reports the
# whole corpus as "developer roles".
NON_TECH = ("产品经理", "运营", "设计", "美术", "画师", "美术师", "策划", "市场", "销售",
            "人力", "财务", "法务", "供应链", "采购", "品牌", "编辑", "翻译", "行政")
TECH_HINTS = ("工程师", "开发", "研发", "算法", "架构", "测试", "技术", "SRE", "运维", "数据")


class Snapshot:
    def __init__(self, ident: int, text: str):
        self.id = ident
        self.source_id = 1
        self.content_text = text


def classify(title: str) -> str:
    if any(word in title for word in NON_TECH):
        return "OTHER"
    if any(word in title for word in TECH_HINTS):
        return "TECH"
    return "UNCLASSIFIED"


def dom_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


async def load_sandbox() -> dict:
    conn = await asyncpg.connect(DSN)
    try:
        versions = await conn.fetch(
            """select v.id, v.version_number, v.publication_status, length(v.raw_text) chars,
                      v.content_fingerprint, j.canonical_title, c.canonical_name,
                      s.canonical_url
                 from job_description_versions v
                 join jobs j on j.id = v.job_id
                 join companies c on c.id = j.company_id
                 left join source_snapshots ss on ss.id = v.source_snapshot_id
                 left join sources s on s.id = ss.source_id
                order by v.id""")
        tasks = await conn.fetch(
            """select id, status, target_ref, left(coalesce(error_message, ''), 160) error
                 from public_data_refresh_tasks order by id""")
        skills = await conn.fetch(
            """select jd_version_id, status, recommended_skill, reason
                 from public_career_skill_recommendations order by jd_version_id""") \
            if await conn.fetchval(
                "select count(*) from information_schema.tables where table_name='public_career_skill_recommendations'") \
            else []
    finally:
        await conn.close()
    return {"versions": [dict(r) for r in versions],
            "tasks": [dict(r) for r in tasks],
            "skills": [dict(r) for r in skills]}


async def main() -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    batch4 = json.loads(BATCH4.read_text(encoding="utf-8"))
    sandbox = await load_sandbox()
    by_url = {v["canonical_url"]: v for v in sandbox["versions"] if v.get("canonical_url")}
    # A listing-page capture and the later detail-page capture of the same role are
    # different URLs for one job; fall back to (company, title) with a checked map.
    by_title = {}
    for version in sandbox["versions"]:
        by_title.setdefault((version["canonical_name"], version["canonical_title"]), version)

    rows, problems = [], []
    for record in corpus["records"]:
        jd_id = record["jd_id"]
        body = CAPTURED / f"{jd_id}.txt"
        dom = DOM / f"{jd_id}.html"
        text = body.read_text(encoding="utf-8") if body.exists() else ""
        body_hash = sha256(text.encode()).hexdigest() if text else ""
        # Same reading convention the capture scripts used: universal newlines,
        # invalid bytes dropped. Anything else produces a false hash mismatch.
        dom_text = dom.read_text(encoding="utf-8", errors="ignore") if dom.exists() else ""
        dom_hash = sha256(dom_text.encode()).hexdigest() if dom_text else ""
        curated = sorted((REPO / "data/manual_sources").rglob(f"{jd_id.lower()}*")) \
            + sorted((REPO / "data/manual_sources").rglob(f"{jd_id}*"))
        version = by_url.get(record["url"]) or by_title.get((record["company"], record["title"]))
        task = next((t for t in sandbox["tasks"]
                     if t["target_ref"] and record["url"] in str(t["target_ref"])), None)
        skill = next((s for s in sandbox["skills"]
                      if version and s["jd_version_id"] == version["id"]), None)

        if body.exists() and body_hash != record["sha256"]:
            problems.append({"jd_id": jd_id, "issue": "BODY_HASH_MISMATCH"})
        if not body.exists() and record["source_grade"].startswith("MANUAL"):
            pass  # curated records live under data/manual_sources, checked below
        elif not body.exists():
            problems.append({"jd_id": jd_id, "issue": "BODY_MISSING"})
        if not dom.exists() and not record["source_grade"].startswith("MANUAL"):
            problems.append({"jd_id": jd_id, "issue": "DOM_MISSING"})

        rows.append({
            "jd_id": jd_id,
            "company": record["company"],
            "title": record["title"],
            "url": record["url"],
            "sha256": record["sha256"],
            "chars": record["chars"],
            "source_grade": record["source_grade"],
            "method": record.get("method"),
            "has_dom": dom.exists(),
            "dom_sha256": dom_hash[:16] if dom_hash else "",
            "dom_title": dom_title(dom_text)[:60],
            "title_literal_in_dom": record["title"] in dom_text if dom_text else False,
            "body_present": body.exists(),
            "body_hash_matches": body_hash == record["sha256"],
            "curated_copy": str(curated[0].relative_to(REPO)) if curated else None,
            "kind": classify(record["title"]),
            "rule_requirements": record.get("rule_requirements"),
            "rule_responsibilities": record.get("rule_responsibilities"),
            "topics": record.get("topics") or [],
            "curated_skill": record.get("skill_status"),
            "skill": record.get("skill"),
            "sandbox_version_id": version["id"] if version else None,
            "sandbox_version_number": version["version_number"] if version else None,
            "publication_status": version["publication_status"] if version else None,
            "sandbox_chars": version["chars"] if version else None,
            "task_id": task["id"] if task else None,
            "task_status": task["status"] if task else None,
            "task_error": task["error"] if task else None,
            "published_skill": ({"status": skill["status"], "skill": skill["recommended_skill"],
                                 "reason": skill["reason"]} if skill else None),
        })

    # Deep re-check of the six records queued for this batch.
    batch_rows = []
    corpus_by_id = {r["jd_id"]: r for r in corpus["records"]}
    for record in batch4["records"]:
        text = (REPO / record["text_path"]).read_text(encoding="utf-8")
        dom_file = REPO / record["dom_path"]
        dom_text = dom_file.read_text(encoding="utf-8", errors="ignore")
        body_hash = sha256(text.encode()).hexdigest()
        source_id = dom_file.stem
        source_record = corpus_by_id.get(source_id)
        body_only = text.split("\n", 2)[2] if text.count("\n") >= 2 else ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        catalog = EvidenceCatalog([Snapshot(1, text)], max_chars=24000, span_chars=600)
        spans = sorted(catalog.spans.values(), key=lambda s: s.start)
        cursor, cut = 0, []
        for line in lines:
            start = text.find(line, cursor)
            end = start + len(line)
            cursor = end
            if any(span.start < end and span.end > start and not
                   (span.start <= start and end <= span.end) for span in spans):
                cut.append({"line": line[:70], "start": start, "end": end})
        batch_rows.append({
            "jd_key": record["jd_key"],
            "company": record["company"],
            "job_title": record["job_title"],
            "direction": record["direction"],
            "source_url": record["source_url"],
            "job_id": record["job_id"],
            "job_id_in_url": record["job_id"] in record["source_url"],
            "text_sha256": body_hash,
            "manifest_hash_matches": body_hash == record["text_sha256"],
            "source_record": source_id,
            "projected_body_equals_corpus": bool(
                source_record and sha256(body_only.encode()).hexdigest() == source_record["sha256"]),
            "chars": len(text),
            "dom_sha256_matches": sha256(dom_text.encode()).hexdigest() == record["dom_sha256"],
            "title_literal_in_dom": record["job_title"] in dom_text,
            "company_in_dom": record["company"] in dom_text,
            "first_two_lines": text.splitlines()[:2],
            "section_headers": record["section_headers"],
            "chunk_spans": [(s.start, s.end) for s in spans],
            "lines_still_cut": cut,
        })

    OUT.mkdir(parents=True, exist_ok=True)
    payload = {"http_calls_to_model": 0,
               "corpus_counts": corpus["counts"],
               "companies": corpus["companies"],
               "records": rows,
               "problems": problems,
               "batch4_recheck": batch_rows,
               "sandbox": {"versions": sandbox["versions"], "tasks": sandbox["tasks"],
                           "skills": sandbox["skills"]}}
    (OUT / "inventory_52.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    tech = [r for r in rows if r["kind"] == "TECH"]
    other = [r for r in rows if r["kind"] == "OTHER"]
    unclassified = [r for r in rows if r["kind"] == "UNCLASSIFIED"]
    published = [r for r in rows if r["publication_status"] == "PUBLISHED"]
    waiting = [r for r in rows if r["publication_status"] == "WAITING_REVIEW"]
    failed = [r for r in rows if r["task_status"] == "FAILED"]
    with_dom = [r for r in rows if r["has_dom"]]

    print(f"records={len(rows)} TECH={len(tech)} OTHER={len(other)} UNCLASSIFIED={len(unclassified)}")
    print(f"published={len(published)} waiting={len(waiting)} failed_tasks={len(failed)} with_dom={len(with_dom)}")
    print("problems:", problems)
    print("\n-- unclassified titles")
    for row in unclassified:
        print("  ", row["jd_id"], row["company"], row["title"])
    print("\n-- batch4 recheck")
    for row in batch_rows:
        print(f"  {row['jd_key']:<22} hash_ok={row['manifest_hash_matches']!s:<5} "
              f"body_ok={row['projected_body_equals_corpus']!s:<5} dom_ok={row['dom_sha256_matches']!s:<5} "
              f"title_ok={row['title_literal_in_dom']!s:<5} id_in_url={row['job_id_in_url']!s:<5} "
              f"chars={row['chars']:<5} cut={len(row['lines_still_cut'])} "
              f"span={row['chunk_spans']}")
        if row["lines_still_cut"]:
            for item in row["lines_still_cut"]:
                print("     cut:", item)
    print("\nwritten", OUT / "inventory_52.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
