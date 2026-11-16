"""Round 36: F1 before/after measurement, using the shipped chunker.

Zero HTTP. `legacy_bounds` is a local copy of the pre-F1 rule (also used by the
regression audit as a control); the "after" side is the real `EvidenceCatalog`, so
these numbers describe the code that will actually run — not a simulation.

Output: data/r48_span_fix/span_fix.json (overwrites the round-35 simulation, which
is reproducible from this file plus git history of `evidence.py`).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))
sys.path.insert(0, str(API_ROOT / "scripts"))

from interview_api.modules.public_career.research.evidence import EvidenceCatalog  # noqa: E402
from r51_f1_published_regression import legacy_bounds  # noqa: E402

SPAN_CHARS = 600
MAX_CHARS = 24000
OUT = REPO / "data/r48_span_fix"

TARGETS = {
    "NIO_AIA_025": REPO / "data/r34_target_jd/text/NIO_AIA_025.txt",
    **{row["jd_key"]: REPO / row["text_path"] for row in
       json.loads((REPO / "data/r47_batch3/source_manifest.json").read_text(encoding="utf-8"))["records"]},
}


class Snapshot:
    def __init__(self, id_, source_id, text):
        self.id, self.source_id, self.content_text = id_, source_id, text


def line_table(text: str) -> list[tuple[str, int, int]]:
    rows, cursor = [], 0
    for raw in text.splitlines():
        if not raw.strip():
            cursor += len(raw) + 1
            continue
        start = text.find(raw, cursor)
        if start < 0:
            continue
        cursor = start + len(raw)
        rows.append((raw.strip(), start, start + len(raw)))
    return rows


def measure(text: str, bounds: list[tuple[int, int]]) -> dict:
    lines = line_table(text)
    inside = 0
    cut_lines = []
    for needle, start, end in lines:
        holders = [(s, e) for s, e in bounds if s <= start and e >= end]
        if holders:
            inside += 1
        else:
            cut_lines.append(needle[:60])
    return {"lines": len(lines), "lines_held_by_one_chunk": inside,
            "lines_cut": len(cut_lines), "cut_samples": cut_lines[:3],
            "chunks": len(bounds)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "span_fix.json"))
    args = ap.parse_args()
    rows = []
    for name, path in TARGETS.items():
        text = path.read_text(encoding="utf-8")
        legacy = legacy_bounds(text, SPAN_CHARS, min(len(text), MAX_CHARS))
        catalog = EvidenceCatalog([Snapshot(1, 1, text)], max_chars=MAX_CHARS, span_chars=SPAN_CHARS)
        now = [(s.start, s.end) for s in sorted(catalog.spans.values(), key=lambda s: s.start)]
        before, after = measure(text, legacy), measure(text, now)
        rows.append({"jd": name, "chars": len(text), "before": before, "after": after,
                     "chunk_lengths_before": sorted({e - s for s, e in legacy}),
                     "chunk_lengths_after": sorted({e - s for s, e in now}),
                     "offsets_exact_after": all(text[s:e] == catalog.spans[k].excerpt
                                                for k, (s, e) in zip(
                                                    [k for k, v in sorted(
                                                        catalog.spans.items(),
                                                        key=lambda kv: kv[1].start)],
                                                    now))})
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "model_http_calls": 0,
               "f1_applied": True, "span_chars": SPAN_CHARS,
               "note": "before = pre-F1 rule (control copy); after = shipped EvidenceCatalog",
               "rows": rows,
               "totals": {
                   "jds": len(rows),
                   "lines_before": sum(r["before"]["lines"] for r in rows),
                   "cut_before": sum(r["before"]["lines_cut"] for r in rows),
                   "cut_after": sum(r["after"]["lines_cut"] for r in rows),
               }}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for row in rows:
        print(f"{row['jd']:34} chars={row['chars']:<5} "
              f"cuts {row['before']['lines_cut']}->{row['after']['lines_cut']} "
              f"lines={row['after']['lines']} chunks {row['before']['chunks']}->{row['after']['chunks']} "
              f"len_after={row['chunk_lengths_after']}")
    print("totals:", json.dumps(payload["totals"], ensure_ascii=False))
    print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
