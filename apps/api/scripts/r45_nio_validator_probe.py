"""Round 35, task 1b: prove what the attribute gate *could* have rejected.

Zero HTTP. Two independent checks against the exact snapshot the failed revision
used (the sha256 is asserted against the frozen manifest, so this is the same
bytes the model saw):

1. verbatim self-test — every page metadata line, cited by the span that contains
   it. If these pass, the gate is not rejecting literal text for a whitespace or
   chunk-boundary reason, and the failure must be a rewrite.
2. candidate probes — the normalisations a model plausibly produces for the four
   allowed attribute fields. These are labelled HYPOTHESIS: they show which
   rewrites the gate would reject, not which one the model emitted.

Output: data/r44_nio_diagnosis/validator_probe.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import asyncpg

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from interview_api.modules.public_career.fingerprints import normalize_text  # noqa: E402
from interview_api.modules.public_career.research.evidence import EvidenceCatalog  # noqa: E402

SANDBOX = "interview_agent_wb_jd_r31_20261116"
DSN = f"postgresql://interview_agent:interview_agent@localhost:5432/{SANDBOX}"
TXT = REPO / "data/r34_target_jd/text/NIO_AIA_025.txt"
OUT = REPO / "data/r44_nio_diagnosis"
SPAN_CHARS = 600
MAX_CHARS = 24000

# Page lines that can legitimately become an attribute (field is constrained to
# RESPONSIBILITY / LOCATION / PUBLISHED_AT / JOB_NUMBER).
VERBATIM_LINES = {
    3: "LOCATION", 4: "PUBLISHED_AT?", 5: "PUBLISHED_AT?", 6: "PUBLISHED_AT?",
    7: "PUBLISHED_AT?", 8: "PUBLISHED_AT?", 9: "JOB_NUMBER",
    15: "RESPONSIBILITY", 16: "RESPONSIBILITY", 17: "RESPONSIBILITY",
    18: "RESPONSIBILITY", 19: "RESPONSIBILITY", 20: "RESPONSIBILITY",
}

# Rewrites a model could plausibly emit. Explicitly hypotheses, not findings.
HYPOTHESES = [
    ("LOCATION", "上海", "page line 3 is exactly 上海"),
    ("LOCATION", "上海市", "expanding city to municipality"),
    ("PUBLISHED_AT", "2027届校园招聘-正式批", "page line 8 verbatim"),
    ("PUBLISHED_AT", "2027届校园招聘", "dropping the batch suffix"),
    ("PUBLISHED_AT", "校园招聘", "normalising 校招 to 校园招聘"),
    ("PUBLISHED_AT", "2027届", "keeping only the cohort year"),
    ("PUBLISHED_AT", "2026-11-16", "filling today as the publish date"),
    ("JOB_NUMBER", "职位 ID：A25338A", "page line 9 verbatim"),
    ("JOB_NUMBER", "A25338A", "job id alone (still a substring)"),
    ("JOB_NUMBER", "A25338A ", "trailing space"),
]


class Snapshot:
    """Minimal stand-in so EvidenceCatalog can be reused without a DB session."""

    def __init__(self, id_, source_id, text):
        self.id = id_
        self.source_id = source_id
        self.content_text = text


def span_containing(catalog, text, needle):
    hits = []
    for key, span in catalog.spans.items():
        if needle in span.excerpt:
            hits.append(key)
        elif needle in span.excerpt.rstrip() and normalize_text(needle) in normalize_text(span.excerpt):
            hits.append(key)
    return hits


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "validator_probe.json"))
    args = ap.parse_args()

    raw = TXT.read_bytes()
    text = raw.decode("utf-8")
    digest = sha256(raw).hexdigest()

    conn = await asyncpg.connect(DSN)
    try:
        task = await conn.fetchrow(
            "select id, target_ref, reason from public_data_refresh_tasks where id = 4")
        assert task["target_ref"] == "MANUAL_SOURCE_IMPORT:2", task["target_ref"]
        manifest = json.loads(task["reason"])
        snapshot_id = manifest["source_snapshot_id"]
        row = await conn.fetchrow(
            "select id, source_id, content_hash, length(content_text) as chars "
            "from source_snapshots where id = $1", snapshot_id)
        stored_hash = row["content_hash"]
    finally:
        await conn.close()

    assert stored_hash == digest == manifest["content_sha256"], (
        stored_hash, digest, manifest["content_sha256"])

    snapshot = Snapshot(row["id"], row["source_id"], text)
    catalog = EvidenceCatalog([snapshot], max_chars=MAX_CHARS, span_chars=SPAN_CHARS)
    lines = text.splitlines()

    boundaries = sorted({s.start for s in catalog.spans.values()} | {s.end for s in catalog.spans.values()})
    verbatim = []
    cursor = 0
    for number, line in enumerate(lines, start=1):
        start = text.find(line, cursor)
        end = start + len(line) if start >= 0 else -1
        if start >= 0:
            cursor = end
        if number not in VERBATIM_LINES:
            continue
        needle = line.strip()
        hits = span_containing(catalog, text, needle)
        ok = catalog.supports_literal(hits, needle) if hits else False
        # The realistic recovery: cite every span that overlaps the line.
        overlapping = [k for k, sp in catalog.spans.items()
                       if start >= 0 and sp.start < end and sp.end > start]
        ok_all = catalog.supports_literal(overlapping, needle) if overlapping else False
        splits = [b for b in boundaries if start < b < end]
        verbatim.append({
            "line": number, "field": VERBATIM_LINES[number], "text": needle,
            "start": start, "end": end,
            "spans_containing": hits, "span_count": len(hits),
            "spans_overlapping": overlapping,
            "passes_single_span": ok,
            "passes_with_all_overlapping_spans": ok_all,
            "split_at_char": splits,
        })

    hypotheses = []
    for field, statement, note in HYPOTHESES:
        hits = span_containing(catalog, text, statement)
        # A model may also cite the neighbouring span for a straddling phrase,
        # so report both the single-span and the best-effort adjacent-span result.
        passes = catalog.supports_literal(hits, statement) if hits else False
        hypotheses.append({
            "field": field, "statement": statement, "note": note,
            "spans_containing": hits, "passes": passes,
        })

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": SANDBOX,
        "snapshot_id": row["id"],
        "snapshot_chars": row["chars"],
        "snapshot_sha256": digest,
        "manifest_sha256": manifest["content_sha256"],
        "hashes_match": True,
        "span_chars": SPAN_CHARS,
        "span_count": len(catalog.spans),
        "span_lengths": sorted({len(s.excerpt) for s in catalog.spans.values()}),
        "span_boundaries": boundaries,
        "verbatim_lines": verbatim,
        "hypotheses": hypotheses,
        "verdict": {
            "verbatim_all_pass": all(v["passes_single_span"] for v in verbatim),
            "any_verbatim_straddles": any(bool(v["split_at_char"]) for v in verbatim),
            "rejected_hypotheses": [h["statement"] for h in hypotheses if not h["passes"]],
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"snapshot#{row['id']} chars={row['chars']} spans={len(catalog.spans)} "
          f"span_lengths={result['span_lengths']}")
    print(f"sha256 matches manifest: {digest == manifest['content_sha256']}")
    print("\nverbatim page lines through the gate:")
    for v in verbatim:
        print(f"  L{v['line']:>2} {v['field']:<15} single={v['passes_single_span']!s:<5} "
              f"multi={v['passes_with_all_overlapping_spans']!s:<5} "
              f"chars={v['start']}-{v['end']} split={v['split_at_char']} {v['text'][:50]}")
    print("\nhypothesis probes (which rewrites the gate rejects):")
    for h in hypotheses:
        print(f"  {h['field']:<15} pass={h['passes']!s:<5} {h['statement']!r}  ({h['note']})")
    print("\nverdict:", json.dumps(result["verdict"], ensure_ascii=False))
    print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
