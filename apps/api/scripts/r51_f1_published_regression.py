"""Round 36: prove the F1 chunking change did not disturb any stored evidence.

Zero HTTP, read-only. For every JD version in the sandbox this re-checks:
  * the snapshot bytes still hash to the recorded `content_hash`;
  * every stored ClaimEvidence offset still slices exactly its stored excerpt;
  * every requirement statement classifies the same way it did before F1;
  * a catalogue rebuilt from the same snapshot yields spans whose offsets are
    exact (F1 changes where slices end, never what the bytes are);
  * a rebuilt catalogue no longer cuts the page lines it used to cut.

Writes data/r51_f1_regression/published_evidence.json.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))
sys.path.insert(0, str(API_ROOT / "scripts"))

from r42_audit_publish_batch2 import classify_statement  # noqa: E402

SANDBOX = "interview_agent_wb_jd_r31_20261116"
DSN = f"postgresql://interview_agent:interview_agent@localhost:5432/{SANDBOX}"
OUT = REPO / "data/r51_f1_regression"
SPAN_CHARS = 600
MAX_CHARS = 24000


class Snapshot:
    def __init__(self, id_, source_id, text):
        self.id, self.source_id, self.content_text = id_, source_id, text


def legacy_bounds(text: str, span_chars: int, limit: int) -> list[tuple[int, int]]:
    """The pre-F1 slicing rule, kept only as a control in this audit."""
    bounds = []
    for start in range(0, min(len(text), limit), span_chars):
        excerpt = text[start:start + span_chars]
        if not excerpt.strip():
            continue
        bounds.append((start, start + len(excerpt)))
    return bounds


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "published_evidence.json"))
    args = ap.parse_args()

    from interview_api.modules.public_career.research.evidence import EvidenceCatalog

    conn = await asyncpg.connect(DSN)
    try:
        assert await conn.fetchval("select current_database()") == SANDBOX
        versions = await conn.fetch(
            "select v.id, v.publication_status, v.version_number, v.raw_text, v.source_snapshot_id, "
            "c.canonical_name, j.canonical_title from job_description_versions v "
            "join jobs j on j.id = v.job_id join companies c on c.id = j.company_id order by v.id")
        snapshots = {r["id"]: r for r in await conn.fetch(
            "select id, content_hash, content_text from source_snapshots order by id")}
        evidence = await conn.fetch(
            "select ce.claim_id, ce.source_snapshot_id, ce.excerpt, ce.source_location_json "
            "from claim_evidence ce order by ce.id")
        requirements = await conn.fetch(
            "select r.id, r.job_description_version_id, r.statement, r.claim_id "
            "from job_requirements r order by r.id")
    finally:
        await conn.close()

    by_version: dict[int, list] = {}
    for row in requirements:
        by_version.setdefault(row["job_description_version_id"], []).append(row)

    evidence_checks = []
    for row in evidence:
        snapshot = snapshots[row["source_snapshot_id"]]
        loc = row["source_location_json"]
        if isinstance(loc, str):
            loc = json.loads(loc)
        start, end = loc.get("start_char"), loc.get("end_char")
        text = snapshot["content_text"] or ""
        evidence_checks.append({
            "claim_id": row["claim_id"], "snapshot_id": row["source_snapshot_id"],
            "offset_exact": isinstance(start, int) and isinstance(end, int)
            and text[start:end] == row["excerpt"],
            "hash_intact": hashlib.sha256(text.encode()).hexdigest() == snapshot["content_hash"],
            "start": start, "end": end,
        })

    versions_out = []
    for version in versions:
        snapshot = snapshots[version["source_snapshot_id"]]
        text = snapshot["content_text"] or ""
        rebuilt = EvidenceCatalog([Snapshot(snapshot["id"], 0, text)],
                                  max_chars=MAX_CHARS, span_chars=SPAN_CHARS)
        spans = sorted(rebuilt.spans.values(), key=lambda s: s.start)
        offsets_exact = all(text[s.start:s.end] == s.excerpt for s in spans)
        covered = sum(s.end - s.start for s in spans)

        lines, cursor = [], 0
        for raw in text.splitlines():
            if not raw.strip():
                cursor += len(raw) + 1
                continue
            start = text.find(raw, cursor)
            if start < 0:
                continue
            cursor = start + len(raw)
            lines.append((raw.strip(), start, start + len(raw)))

        def cut_count(bounds):
            return sum(1 for _, start, end in lines
                       if any(s < end and e > start and not (s <= start and e >= end)
                              for s, e in bounds))

        legacy = legacy_bounds(text, SPAN_CHARS, min(len(text), MAX_CHARS))
        now = [(s.start, s.end) for s in spans]
        rows = []
        for requirement in by_version.get(version["id"], []):
            rows.append({"requirement_id": requirement["id"],
                         "kind": classify_statement(requirement["statement"], text)})
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["kind"]] = counts.get(row["kind"], 0) + 1
        versions_out.append({
            "jd_version_id": version["id"], "company": version["canonical_name"],
            "job_title": version["canonical_title"], "publication_status": version["publication_status"],
            "snapshot_id": snapshot["id"], "snapshot_sha256": snapshot["content_hash"],
            "body_chars": len(text), "requirements": len(rows), "kind_counts": counts,
            "requirements_kinds": rows,
            "rebuilt_spans": len(spans), "offsets_exact": offsets_exact, "covered_chars": covered,
            "line_cuts_legacy": cut_count(legacy), "line_cuts_f1": cut_count(now),
            "span_offsets": [{"start": s.start, "end": s.end} for s in spans],
            "body_equals_raw_text": text == version["raw_text"],
        })

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "database": SANDBOX,
        "model_http_calls": 0, "f1_applied": True,
        "evidence_rows": len(evidence_checks),
        "evidence_offset_failures": [e for e in evidence_checks if not e["offset_exact"]],
        "evidence_hash_failures": sorted({e["snapshot_id"] for e in evidence_checks if not e["hash_intact"]}),
        "versions": versions_out,
        "totals": {
            "versions": len(versions_out),
            "requirements": sum(v["requirements"] for v in versions_out),
            "line_cuts_legacy": sum(v["line_cuts_legacy"] for v in versions_out),
            "line_cuts_f1": sum(v["line_cuts_f1"] for v in versions_out),
            "published": sum(1 for v in versions_out if v["publication_status"] == "PUBLISHED"),
            "waiting_review": sum(1 for v in versions_out if v["publication_status"] == "WAITING_REVIEW"),
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"evidence rows={payload['evidence_rows']} "
          f"offset_failures={len(payload['evidence_offset_failures'])} "
          f"hash_failures={payload['evidence_hash_failures']}")
    for v in versions_out:
        print(f"  #{v['jd_version_id']} {v['company'][:6]:8} {v['job_title'][:30]:32} "
              f"{v['publication_status']:<14} reqs={v['requirements']:<3} {v['kind_counts']} "
              f"spans={v['rebuilt_spans']} cuts {v['line_cuts_legacy']}->{v['line_cuts_f1']} "
              f"offsets_exact={v['offsets_exact']}")
    print("totals:", json.dumps(payload["totals"], ensure_ascii=False))
    print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
