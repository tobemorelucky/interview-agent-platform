"""Round 35: offline simulation of the one-line span fix, plus a boundary census.

Zero HTTP, no code change. `EvidenceCatalog` cuts the stored body into fixed
600-character chunks, so a page line that happens to cross a chunk boundary cannot
be quoted with a single evidence id — `supports_literal` returns False for a
perfectly verbatim sentence. The NIO revision failed exactly at that gate.

This script measures the defect on every JD in play and simulates the minimal fix
(snap the chunk end to the next line break) to show it removes the failure without
loosening the literal rule: the quote still has to be a substring of one continuous
span, it just no longer gets sliced by an arbitrary offset.

Output: data/r48_span_fix/span_fix.json
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

from interview_api.modules.public_career.research.evidence import EvidenceCatalog  # noqa: E402

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


class LineAlignedCatalog(EvidenceCatalog):
    """Same spans, same strict substring rule, but chunks end at a line break."""

    def __init__(self, snapshots, *, max_chars=MAX_CHARS, span_chars=SPAN_CHARS):
        super().__init__(snapshots, max_chars=max_chars, span_chars=span_chars)
        self.spans = {}
        used = 0
        for snapshot in sorted(snapshots, key=lambda s: s.id):
            text = snapshot.content_text or ""
            start = 0
            while start < min(len(text), max_chars) and used < max_chars:
                hard_end = min(start + span_chars, len(text), max_chars - used)
                end = hard_end
                if hard_end < len(text):
                    newline = text.find("\n", start + span_chars // 2)
                    if 0 < newline < hard_end + span_chars // 2:
                        end = newline + 1
                excerpt = text[start:end]
                if excerpt.strip():
                    from hashlib import sha256
                    key = "ev_" + sha256(f"{snapshot.id}:{start}:{end}:{excerpt}".encode()).hexdigest()[:24]
                    from interview_api.modules.public_career.research.evidence import EvidenceSpan
                    self.spans[key] = EvidenceSpan(key, snapshot.id, snapshot.source_id, excerpt, start, end)
                    used += len(excerpt)
                start = end


def census(name: str, text: str) -> dict:
    snapshot = Snapshot(1, 1, text)
    fixed = LineAlignedCatalog([snapshot])
    original = EvidenceCatalog([snapshot], max_chars=MAX_CHARS, span_chars=SPAN_CHARS)

    lines, cursor = [], 0
    for raw in text.splitlines():
        if not raw.strip():
            cursor += len(raw) + 1
            continue
        start = text.find(raw, cursor)
        if start < 0:
            continue
        end = start + len(raw)
        cursor = end
        lines.append((raw.strip(), start, end))

    def verdict(catalog) -> dict:
        failing_all, failing_one, cut = [], [], 0
        for needle, start, end in lines:
            overlapping = [k for k, s in catalog.spans.items() if s.start < end and s.end > start]
            containing_start = [k for k, s in catalog.spans.items() if s.start <= start < s.end]
            contained = [k for k, s in catalog.spans.items() if needle in s.excerpt]
            if not contained:
                cut += 1
            if not catalog.supports_literal(overlapping, needle):
                failing_all.append(needle[:60])
            # The realistic lazy citation: quote one id, the chunk the line starts in.
            if not containing_start or not catalog.supports_literal(containing_start, needle):
                failing_one.append(needle[:60])
        return {"lines": len(lines), "lines_not_in_any_single_span": cut,
                "lines_failing_overlapping_citation": len(failing_all),
                "lines_failing_single_start_span": len(failing_one),
                "samples_single_span_fail": failing_one[:3],
                "spans": len(catalog.spans)}

    return {"jd": name, "chars": len(text), "original": verdict(original),
            "line_aligned": verdict(fixed)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "span_fix.json"))
    args = ap.parse_args()
    rows = [census(name, path.read_text(encoding="utf-8")) for name, path in TARGETS.items()]
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "model_http_calls": 0,
               "code_changed": False, "span_chars": SPAN_CHARS,
               "note": "original = current EvidenceCatalog; line_aligned = simulated fix, not applied",
               "rows": rows,
               "totals": {
                   "jds": len(rows),
                   "original_failing_single_span": sum(r["original"]["lines_failing_single_start_span"] for r in rows),
                   "line_aligned_failing_single_span": sum(r["line_aligned"]["lines_failing_single_start_span"] for r in rows),
                   "original_failing_all": sum(r["original"]["lines_failing_overlapping_citation"] for r in rows),
                   "line_aligned_failing_all": sum(r["line_aligned"]["lines_failing_overlapping_citation"] for r in rows),
                   "original_cut_lines": sum(r["original"]["lines_not_in_any_single_span"] for r in rows),
                   "line_aligned_cut_lines": sum(r["line_aligned"]["lines_not_in_any_single_span"] for r in rows),
               }}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for row in rows:
        o, f = row["original"], row["line_aligned"]
        print(f"{row['jd']:34} chars={row['chars']:<5} "
              f"orig: cut={o['lines_not_in_any_single_span']} "
              f"single_fail={o['lines_failing_single_start_span']}/{o['lines']} | "
              f"fixed: cut={f['lines_not_in_any_single_span']} "
              f"single_fail={f['lines_failing_single_start_span']}/{f['lines']}")
    print("totals:", json.dumps(payload["totals"], ensure_ascii=False))
    print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
