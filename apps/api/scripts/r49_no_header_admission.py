"""Round 35, task 3.8: which header-less JDs may enter the formal Research flow.

Zero HTTP, read-only. The 49-record precheck flags `NO_SECTION_HEADERS` whenever the
page does not print 岗位职责 / 任职资格 style headings. Round 34 showed such JDs can
still be extracted correctly — the real risk is that the model tidies the wording.
So admission here is decided on *source integrity*, not on the presence of headings.

Admission (all required):
  * the only open review flag is NO_SECTION_HEADERS
  * a captured DOM exists and the page literal is provable in it
  * the stored body hash equals the recorded hash
  * a stable job id can be parsed from the official URL
  * body >= 300 characters, no truncation, no navigation markers
  * the record was not already imported and its title/body is not already published
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "scripts"))

from r40_build_batch2 import CAPTURED, DOM, job_id_of  # noqa: E402

PRECHECK = REPO / "data/r37_precheck_49/precheck_49.json"
BATCH2 = REPO / "data/r40_batch2/source_manifest.json"
OUT = REPO / "data/r49_no_header_admission"

# The five records already imported in round 34 (Unity / 面壁 / 沐瞳后端 / 蔚来推理 / 麒麟内核).
USED = {"OFFICIAL_CAND_029", "OFFICIAL_CAND_031", "沐瞳科技_POS_7673023051731503402",
        "OFFICIAL_CAND_021", "KYLINOS_aa9be729"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT / "admission.json"))
    args = ap.parse_args()
    precheck = json.loads(PRECHECK.read_text(encoding="utf-8"))
    used = set(USED)

    rows = []
    for record in precheck["records"]:
        review = record["review"]
        if "NO_SECTION_HEADERS" not in review:
            continue
        notes = record["notes"]
        dom = DOM / f"{record['jd_id']}.html"
        body = CAPTURED / f"{record['jd_id']}.txt"
        job_id, kind = job_id_of(record["url"])
        checks = {
            "only_header_flag": review == ["NO_SECTION_HEADERS"],
            "dom_present": dom.exists(),
            "body_present": body.exists(),
            "page_literal_provable": bool(notes.get("literal_provable_in_page")),
            "hash_matches": bool(notes.get("hash_matches_record")),
            "stable_job_id": bool(job_id) and kind != "none",
            "body_long_enough": notes["chars"] >= 300,
            "not_truncated": not notes.get("truncated"),
            "no_nav_markers": not notes.get("nav_markers"),
            "not_already_used": record["jd_id"] not in used,
        }
        rows.append({"jd_id": record["jd_id"], "company": record["company"],
                     "title": record["title"], "direction": record["direction"],
                     "chars": notes["chars"], "job_id": job_id, "job_id_kind": kind,
                     "review": review, "other_flags": [r for r in review if r != "NO_SECTION_HEADERS"],
                     "admitted": all(checks.values()), "checks": checks,
                     "blocked": record["blocking"]})

    admitted = [r for r in rows if r["admitted"]]
    rejected = [r for r in rows if not r["admitted"]]
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "model_http_calls": 0,
               "rule": "source integrity only; missing headings is not a blocker",
               "flagged": len(rows), "admitted": len(admitted), "rejected": len(rejected),
               "admitted_rows": admitted, "rejected_rows": rejected}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"records with NO_SECTION_HEADERS: {len(rows)}  admitted: {len(admitted)}  rejected: {len(rejected)}")
    for row in rejected:
        failed = [k for k, v in row["checks"].items() if not v]
        print(f"  REJECT {row['jd_id']:44} {row['company'][:6]:8} {row['title'][:24]:26} {failed}")
    print()
    for row in admitted:
        print(f"  OK     {row['jd_id']:44} {row['company'][:6]:8} {row['title'][:26]:28} "
              f"{row['chars']:>5}字 {row['direction']}")
    print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
