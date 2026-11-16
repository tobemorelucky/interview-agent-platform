"""Round 35, task 2: character-level comparison of the two held JDs.

Zero HTTP, read-only. For jd_version 4 (面壁智能) and 6 (蔚来推理) this prints every
requirement with its cited evidence excerpt, classifies it with the same rule the
publish gate uses, and — for the flagged rows — shows the word-level difference
between what the model stored and what the page actually says.

Output: data/r46_held_review/held_review.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))
sys.path.insert(0, str(API_ROOT / "scripts"))

from r42_audit_publish_batch2 import classify_statement, _fold  # noqa: E402

SANDBOX = "interview_agent_wb_jd_r31_20261116"
DSN = f"postgresql://interview_agent:interview_agent@localhost:5432/{SANDBOX}"
OUT = REPO / "data/r46_held_review"
TEXTS = {"4": REPO / "data/r40_batch2/text/MODELBEST_LLM_031.txt",
         "6": REPO / "data/r40_batch2/text/NIO_INFER_021.txt"}


def diff_words(statement: str, source: str) -> dict:
    """Word-level difference using difflib on the folded-but-ordered text."""
    import difflib
    a, b = list(statement), list(source)
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    removed, added = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"delete", "replace"}:
            removed.append(statement[i1:i2])
        if tag in {"insert", "replace"}:
            added.append(source[j1:j2])
    return {"removed_from_model": removed, "present_in_source_only": added,
            "ratio": round(matcher.ratio(), 4)}


def best_source_line(statement: str, body: str) -> tuple[str, float]:
    import difflib
    folded = _fold(statement)
    best, score = "", 0.0
    for line in body.splitlines():
        if len(line.strip()) < 8:
            continue
        value = difflib.SequenceMatcher(a=folded, b=_fold(line), autojunk=False).ratio()
        if value > score:
            best, score = line.strip(), value
    return best, round(score, 4)


async def dump(conn, jd_id: int) -> dict:
    row = await conn.fetchrow(
        "select v.id, v.raw_text, v.publication_status, c.canonical_name, j.canonical_title, "
        "v.content_fingerprint from job_description_versions v join jobs j on j.id = v.job_id "
        "join companies c on c.id = j.company_id where v.id = $1", jd_id)
    reqs = await conn.fetch(
        "select r.id, r.statement, r.importance, r.requirement_type, r.claim_id, "
        "r.normalized_value, cl.category, cl.status "
        "from job_requirements r left join claims cl on cl.id = r.claim_id "
        "where r.job_description_version_id = $1 order by r.id", jd_id)
    ev = await conn.fetch(
        "select ce.claim_id, ce.source_snapshot_id as snapshot_id, ce.excerpt, ce.evidence_key, "
        "ce.source_location_json, ss.content_hash from claim_evidence ce "
        "join source_snapshots ss on ss.id = ce.source_snapshot_id "
        "where ce.claim_id = any($1::bigint[]) order by ce.claim_id, ce.id",
        [r["claim_id"] for r in reqs if r["claim_id"]] or [0])
    return {"jd_version": dict(row), "requirements": [dict(r) for r in reqs],
            "evidence": [dict(e) for e in ev]}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "held_review.json"))
    args = ap.parse_args()

    conn = await asyncpg.connect(DSN)
    try:
        assert await conn.fetchval("select current_database()") == SANDBOX
        data = {str(jd): await dump(conn, jd) for jd in (4, 6)}
    finally:
        await conn.close()

    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "database": SANDBOX,
              "model_http_calls": 0, "jd": {}}

    for jd_key, payload in data.items():
        body = payload["jd_version"]["raw_text"]
        by_claim: dict[int, list[dict]] = {}
        for e in payload["evidence"]:
            by_claim.setdefault(e["claim_id"], []).append(e)
        rows = []
        for req in payload["requirements"]:
            spans = by_claim.get(req["claim_id"], [])
            excerpt = "\n".join(s["excerpt"] for s in spans)
            kind = classify_statement(req["statement"], body)
            item = {
                "requirement_id": req["id"], "claim_id": req["claim_id"],
                "statement": req["statement"], "importance": req["importance"],
                "requirement_type": req["requirement_type"], "kind": kind,
                "evidence_spans": [{"location": s["source_location_json"],
                                    "excerpt": s["excerpt"]} for s in spans],
                "evidence_in_page_body": all(s["excerpt"] in body for s in spans),
            }
            if kind != "VERBATIM":
                line, ratio = best_source_line(req["statement"], body)
                item["closest_source_line"] = line
                item["similarity"] = ratio
                item["diff"] = diff_words(req["statement"], line)
            rows.append(item)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["kind"]] = counts.get(row["kind"], 0) + 1
        report["jd"][jd_key] = {
            "company": payload["jd_version"]["canonical_name"],
            "job_title": payload["jd_version"]["canonical_title"],
            "publication_status": payload["jd_version"]["publication_status"],
            "fingerprint": payload["jd_version"]["content_fingerprint"],
            "body_chars": len(body),
            "requirement_count": len(rows),
            "kind_counts": counts,
            "requirements": rows,
        }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    for jd_key, payload in report["jd"].items():
        print(f"\n=== jd#{jd_key} {payload['company']} {payload['job_title']} "
              f"[{payload['publication_status']}] {payload['body_chars']}字 {payload['kind_counts']}")
        for row in payload["requirements"]:
            flag = "  " if row["kind"] == "VERBATIM" else "!!"
            print(f"{flag} r{row['requirement_id']:<4} {row['kind']:<15} {str(row['importance']):<9} "
                  f"{row['statement'][:76]}")
            if row["kind"] != "VERBATIM":
                print(f"      source: {row['closest_source_line'][:100]}")
                print(f"      removed={row['diff']['removed_from_model']} "
                      f"source_only={row['diff']['present_in_source_only']} sim={row['similarity']}")
    print("\nwritten:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
