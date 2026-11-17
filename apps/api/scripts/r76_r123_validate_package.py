"""R123 stage 1: read-only validation of the supplied data package.

No model calls, no DB writes. Reports completeness, JSON validity, counts and
cross-file consistency to data/r123_validation/validation.json.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PKG = REPO / "data/r123_package"
OUT = REPO / "data/r123_validation"
OUT.mkdir(parents=True, exist_ok=True)


def read_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    rows, errors = [], []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            errors.append(f"line {i}: {e}")
    return rows, errors


def main() -> int:
    report: dict = {"package": str(PKG.relative_to(REPO))}

    # --- JD 50 ---
    jds, jd_err = read_jsonl(PKG / "jd_50.jsonl")
    manual, man_err = read_jsonl(PKG / "workbuddy_manual_import_50.jsonl")
    report["jd"] = {
        "jd_50_count": len(jds), "json_errors": jd_err,
        "manual_count": len(manual), "manual_json_errors": man_err,
        "companies": len({j["company"] for j in jds}),
        "unique_url": len({j["source_url"] for j in jds}),
        "unique_id": len({j["id"] for j in jds}),
    }
    # field completeness per test_contract §1
    incomplete = []
    for j in jds:
        problems = []
        if not j.get("company") or not j.get("position"):
            problems.append("missing company/position")
        if len(j.get("responsibilities") or []) < 2:
            problems.append("<2 responsibilities")
        if len(j.get("requirements") or []) < 3:
            problems.append("<3 requirements")
        if not re.match(r"https?://", j.get("source_url") or ""):
            problems.append("bad source_url")
        if problems:
            incomplete.append({"id": j.get("id"), "problems": problems})
    report["jd"]["incomplete"] = incomplete

    # hash check of manual import content
    hash_bad = []
    for m in manual:
        digest = hashlib.sha256(m["content"].encode("utf-8")).hexdigest()
        if digest != m.get("content_sha256"):
            hash_bad.append({"jd_id": m.get("jd_id"), "expected": m.get("content_sha256"), "got": digest})
    report["jd"]["manual_hash_mismatch"] = hash_bad

    # cross-file consistency: same ids, same company/title, url
    jd_by_id = {j["id"]: j for j in jds}
    cross_bad = []
    for m in manual:
        j = jd_by_id.get(m.get("jd_id"))
        if not j:
            cross_bad.append({"jd_id": m.get("jd_id"), "issue": "id not in jd_50"})
            continue
        if j["company"] != m["company"] or j["position"] != m["job_title"]:
            cross_bad.append({"jd_id": m["jd_id"], "issue": "company/title mismatch"})
        if j["source_url"] != m["source_url"]:
            cross_bad.append({"jd_id": m["jd_id"], "issue": "url mismatch"})
    report["jd"]["cross_file_mismatch"] = cross_bad

    # TXT files
    txts = sorted((PKG / "JD_逐条TXT").glob("*.txt"))
    report["jd"]["txt_count"] = len(txts)
    txt_ids = {t.name.split("_")[0] for t in txts}
    report["jd"]["txt_ids_missing"] = sorted(set(jd_by_id) - txt_ids)
    report["jd"]["txt_ids_extra"] = sorted(txt_ids - set(jd_by_id))

    # --- interviews ---
    interviews = json.loads((PKG / "interviews_3.json").read_text(encoding="utf-8"))
    questions, q_err = read_jsonl(PKG / "interview_questions_3.jsonl")
    answers, a_err = read_jsonl(PKG / "synthetic_answer_testcases.jsonl")
    report["interviews"] = {
        "raw_type": type(interviews).__name__,
        "question_count": len(questions), "question_json_errors": q_err,
        "answer_count": len(answers), "answer_json_errors": a_err,
        "question_ids_unique": len({(q.get("case_id"), q.get("question_id")) for q in questions}),
    }
    # interviews_3.json structure
    if isinstance(interviews, dict):
        report["interviews"]["top_keys"] = list(interviews)
        cases = interviews.get("interviews") or interviews.get("cases") or []
    else:
        cases = interviews
    report["interviews"]["case_count"] = len(cases) if isinstance(cases, list) else "n/a"
    per_case = {}
    for c in (cases if isinstance(cases, list) else []):
        key = c.get("case_id") or c.get("id") or c.get("name", "?")
        qs = c.get("questions") or []
        per_case[str(key)] = {"questions": len(qs),
                              "has_key_points": sum(1 for q in qs if q.get("key_points")),
                              "has_red_flags": sum(1 for q in qs if q.get("red_flag") or q.get("red_flags"))}
    report["interviews"]["per_case"] = per_case
    # question count from jsonl per case
    report["interviews"]["questions_per_case_jsonl"] = dict(Counter(q.get("case_id") for q in questions))
    report["interviews"]["answers_per_variant"] = dict(Counter(a.get("variant") for a in answers))
    report["interviews"]["answers_per_case"] = dict(Counter(a.get("case_id") for a in answers))
    # alignment: each (case_id, question_id) has 3 variants
    qkeys = {(q.get("case_id"), q.get("question_id")) for q in questions}
    a_by_key: dict = {}
    for a in answers:
        a_by_key.setdefault((a.get("case_id"), a.get("question_id")), set()).add(a.get("variant"))
    missing_variants = []
    for k in qkeys:
        variants = a_by_key.get(k, set())
        if not {"strong", "blank", "incorrect"}.issubset(variants):
            missing_variants.append({"key": list(k), "variants": sorted(variants)})
    report["interviews"]["missing_variants"] = missing_variants[:20]
    report["interviews"]["missing_variants_count"] = len(missing_variants)
    report["interviews"]["answer_keys_not_in_questions"] = [list(k) for k in a_by_key if k not in qkeys][:20]

    ok = (not jd_err and not man_err and not q_err and not a_err
          and len(jds) == 50 and len(manual) == 50 and len(txts) == 50
          and not incomplete and not hash_bad and not cross_bad
          and not report["jd"]["txt_ids_missing"] and not report["jd"]["txt_ids_extra"]
          and len(questions) == 61 and len(answers) == 183
          and not missing_variants)
    report["verdict"] = "PASS" if ok else "REVIEW"
    (OUT / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "jd": report["jd"]["jd_50_count"],
                      "manual": report["jd"]["manual_count"], "txt": report["jd"]["txt_count"],
                      "questions": len(questions), "answers": len(answers),
                      "incomplete": len(incomplete), "hash_bad": len(hash_bad),
                      "cross_bad": len(cross_bad), "missing_variants": len(missing_variants),
                      "per_case": report["interviews"]["per_case"]}, ensure_ascii=False, indent=1))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
