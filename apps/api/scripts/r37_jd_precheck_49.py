"""Round 33: zero-HTTP identity/structure precheck for the remaining real JDs.

Reads only local artefacts (captured text + rendered DOM + curated originals).
No network, no database writes, no model calls.

Calibration notes (learned from the round 31 failure):

* The identity guard compares the manifest label with the *page literal* using
  `normalize_text`, which collapses whitespace but does not delete it. Comparing
  with all whitespace stripped would have called the NIO label "equal" and hidden
  exactly the defect that failed. This script therefore compares with
  `normalize_text`, and treats a channel prefix (`校招-` / `【2027届秋招】`) as a
  difference, not as noise.
* A captured body that holds only the JD blocks cannot prove its own identity;
  that is a missing deterministic projection step, not a bad posting.
* A tail without a final full stop is normal in bullet lists. Only a dangling
  connector or an unbalanced bracket counts as truncation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from interview_api.modules.public_career.fingerprints import normalize_text  # noqa: E402
from interview_api.modules.public_career.requirement_vocabulary import (  # noqa: E402
    is_compound_mixed,
)

BASE = REPO / "data/incoming_20261116c"
CAPTURED = BASE / "captured"
DOM = CAPTURED / "dom"
MANUAL = REPO / "data/manual_sources/2026-10-05"
OUT = REPO / "data/r37_precheck_49"

TARGETS = {
    "OFFICIAL_CAND_025": "NIO_AIA_025",
    "OFFICIAL_CAND_005": "MOON_AGENT_005",
    "OFFICIAL_CAND_002": "KYLINOS_AI_002",
}

UI_TAIL = {"立即投递", "立即", "投递", "投递简历", "分享", "收藏", "返回顶部", "职位详情", "查看详情"}
NAV_MARKERS = ("首页", "登录", "注册", "关于我们", "隐私政策", "用户协议", "版权所有")
REQUIREMENT_CUE = re.compile(r"(熟悉|掌握|具备|了解|至少|优先|学历|专业|届别|经验|能力|熟练|精通)")
# A trailing 。/； is a finished clause, not a cut. Only a connector or an
# opening label means the sentence was severed mid-way.
DANGLING_END = re.compile(r"(和|及|与|或|的|、|，|,|：|:)$")
OPEN_CLOSE = (("（", "）"), ("(", ")"), ("「", "」"), ("【", "】"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def squash(value: str) -> str:
    return "".join(normalize_text(value or "").split())


def strip_channel(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"^[「【\[]([^」】\]]{0,24})[」】\]]\s*", "", text)
    text = re.sub(r"^(校招|秋招|春招)\s*[-－—]?\s*", "", text)
    text = re.sub(r"^(19|20)\d{2}\s*[届年]?\s*[-－—]?\s*", "", text)
    return re.sub(r"^-\s*", "", text).strip()


def dom_literal(html: str, expected: str) -> dict | None:
    """The posting's own title element, not the browser tab / site name.

    Some official detail pages (kylinos.zhiye.com) render the title as a plain
    text line carrying a channel prefix (`「2027校招」…`) with no dedicated
    element. There we accept a line whose channel-stripped form equals the
    recorded title — but we report the finding under a distinct selector name so
    the provenance is visible, and the returned literal still carries the
    prefix, never the cleaned-up display form.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    # A list page holds many postings. The first matching element is the wrong
    # one unless it names this record's job, so prefer an exact match.
    for selector, attr in (("h4[title]", "title"), ('[data-test="jobTitle"]', None),
                           (".job-title", None), ("h1", None)):
        nodes = soup.select(selector)
        for node in nodes:
            value = node.get(attr) if attr else node.get_text(strip=True)
            if value and normalize_text(strip_channel(value)) == normalize_text(expected):
                return {"selector": selector, "text": value.strip(), "matches_expected": True}
    for selector, attr in (('[data-test="jobTitle"]', None), ("h4[title]", "title"),
                           (".job-title", None), ("h1", None)):
        node = soup.select_one(selector)
        if not node:
            continue
        value = node.get(attr) if attr else node.get_text(strip=True)
        if value and value.strip():
            return {"selector": selector, "text": value.strip(), "matches_expected": False}
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    for line in (item.strip() for item in soup.get_text("\n").splitlines()):
        if "://" in line or len(line) > 80:
            continue
        if line and normalize_text(strip_channel(line)) == normalize_text(expected) and line != expected:
            return {"selector": "channel_prefixed_text_line", "text": line, "matches_expected": True}
    return None


def visible_text(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())


def job_id_of(url: str) -> tuple[str | None, str]:
    for pattern, kind in ((r"position/(\d{10,25})", "position_path"),
                          (r"jobAdId=([0-9a-fA-F-]{8,})", "jobAdId"),
                          (r"advertisementId=(\d+)", "advertisementId"),
                          (r"/id/(\d{4,})", "page_id"),
                          (r"zpinfo\d*/(\d{4,})", "page_id")):
        match = re.search(pattern, url or "")
        if match:
            return match.group(1), kind
    return None, "none"


def unbalanced(line: str) -> bool:
    for left, right in OPEN_CLOSE:
        if line.count(left) != line.count(right):
            return True
    return False


def load_detail_urls() -> dict[tuple[str, str], str]:
    path = BASE / "discovered_candidates.jsonl"
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    out = {}
    for row in rows:
        out[(row["company"], squash(strip_channel(row["title"])))] = row["source_url"]
    return out


def load_inputs() -> tuple[list[dict], dict]:
    corpus = json.loads((BASE / "jd_corpus.json").read_text(encoding="utf-8"))
    manifest = json.loads((MANUAL / "manifest.json").read_text(encoding="utf-8"))
    curated = {f"MANUAL_{src['id']}": src for src in manifest["sources"]
               if src["document_type"] == "job_jd"}
    rows = []
    for record in corpus["records"]:
        if record["jd_id"] in TARGETS:
            continue
        item = dict(record)
        if record["jd_id"] in curated:
            source = curated[record["jd_id"]]
            item.update(text_path=REPO / source["local_file"], dom_path=None,
                        source_type=source["source_type"],
                        acquisition_mode=source.get("acquisition_mode"),
                        recorded_sha256=source["content_sha256"])
        else:
            item.update(text_path=CAPTURED / f"{record['jd_id']}.txt",
                        dom_path=DOM / f"{record['jd_id']}.html", source_type="official",
                        acquisition_mode="LOCAL_HEADLESS_RENDER")
        rows.append(item)
    return rows, corpus


def check(record: dict, corpus_records: list[dict], details: dict) -> dict:
    blocking: list[str] = []
    review: list[str] = []
    prep: list[str] = []
    notes: dict = {}
    path = Path(record["text_path"])
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text.strip():
        blocking.append("BODY_MISSING")

    body_sha = sha256_text(text) if text.strip() else None
    recorded_sha = record.get("sha256") or record.get("recorded_sha256")
    notes.update(chars=len(text), body_sha256=body_sha, recorded_sha256=recorded_sha,
                 hash_matches_record=body_sha == recorded_sha)
    if body_sha and body_sha != recorded_sha:
        blocking.append("BODY_HASH_MISMATCH")

    # ── identity ────────────────────────────────────────────────────────────
    dom_path = record.get("dom_path")
    literal = None
    dom_visible = ""
    if dom_path and Path(dom_path).exists():
        html = Path(dom_path).read_text(encoding="utf-8", errors="ignore")
        found = dom_literal(html, record["title"])
        literal = found["text"] if found else None
        if found:
            notes["literal_selector"] = found["selector"]
            notes["literal_matches_record"] = found["matches_expected"]
        dom_visible = visible_text(html)
        notes["company_brand_in_page"] = record["company"][:2] in dom_visible
        if not notes["company_brand_in_page"]:
            review.append("COMPANY_BRAND_NOT_IN_PAGE")
    else:
        review.append("NO_DOM_SNAPSHOT")
        literal = None

    notes["recorded_title"] = record["title"]
    notes["page_literal"] = literal
    if literal:
        # Same comparison the real guard uses: whitespace collapsed, not deleted.
        notes["label_matches_literal"] = normalize_text(record["title"]) == normalize_text(literal)
        notes["channel_prefix_in_literal"] = normalize_text(strip_channel(literal)) == normalize_text(record["title"]) \
            and not notes["label_matches_literal"]
        notes["literal_provable_in_page"] = normalize_text(literal) in normalize_text(dom_visible)
        notes["literal_in_stored_body"] = normalize_text(literal) in normalize_text(text)
        if not notes["label_matches_literal"]:
            # Three buckets, because they need different actions:
            #   pure channel prefix  -> deterministic: adopt the literal
            #   whitespace/punctuation only -> deterministic: adopt the literal
            #   different wording    -> a human must confirm it is the same posting
            trimmed = strip_channel(literal)
            if normalize_text(trimmed) == normalize_text(record["title"]):
                prep.append("IDENTITY_LABEL_HAS_CHANNEL_PREFIX")
            elif squash(trimmed) == squash(record["title"]):
                prep.append("IDENTITY_LABEL_SPACING_ONLY")
            else:
                review.append("IDENTITY_LABEL_WORDING_DIFFERS")
        if not notes["literal_provable_in_page"]:
            blocking.append("IDENTITY_UNPROVABLE")
    else:
        notes["label_matches_literal"] = None
        # Curated originals: the title must at least appear in the stored text.
        notes["literal_in_stored_body"] = normalize_text(record["title"]) in normalize_text(text)
        if not notes["literal_in_stored_body"]:
            review.append("TITLE_NOT_IN_BODY")

    notes["projection_needed"] = not notes.get("literal_in_stored_body", False)
    if notes["projection_needed"]:
        prep.append("NEEDS_DETERMINISTIC_PROJECTION")

    if record["title"].lstrip().startswith("-"):
        review.append("TITLE_LEADING_DASH")

    job_id, kind = job_id_of(record["url"])
    detail_url = details.get((record["company"], squash(strip_channel(record["title"]))))
    notes.update(job_id=job_id, job_id_kind=kind, detail_url_candidate=detail_url)
    if not job_id:
        if detail_url:
            review.append("NEEDS_DETAIL_URL")
        else:
            blocking.append("NO_STABLE_JOB_ID")
    elif kind == "advertisementId":
        review.append("AD_ID_ONLY")
    elif kind == "page_id":
        # Article id on a repost page: stable, but not the employer's posting id.
        review.append("PAGE_ID_NOT_POSTING_ID")

    # ── body shape ──────────────────────────────────────────────────────────
    lines = [line for line in text.splitlines() if line.strip()]
    notes["lines"] = len(lines)
    notes["has_responsibility_header"] = any(m in text for m in
                                            ("岗位职责", "工作职责", "职位描述", "工作内容", "职责描述"))
    notes["has_requirement_header"] = any(m in text for m in
                                         ("任职要求", "任职资格", "岗位要求", "职位要求", "资格要求", "加分项"))
    requirement_lines = [line for line in lines if REQUIREMENT_CUE.search(line)]
    notes.update(rule_requirements=record.get("rule_requirements", 0),
                 requirement_like_lines=len(requirement_lines))
    if not record.get("rule_requirements", 0) and len(requirement_lines) < 3:
        blocking.append("NO_REQUIREMENT_CONTENT")
    elif len(requirement_lines) < 3:
        review.append("THIN_REQUIREMENTS")
    if not notes["has_responsibility_header"]:
        review.append("NO_SECTION_HEADERS")

    tail = lines[-1] if lines else ""
    notes["tail"] = tail[:60]
    if tail.strip() in UI_TAIL or tail.strip().startswith("http"):
        review.append("UI_TAIL_PRESENT")
    notes["truncated"] = bool(DANGLING_END.search(tail)) or unbalanced(tail)
    if notes["truncated"]:
        blocking.append("TRUE_TRUNCATION")

    nav_hits = [marker for marker in NAV_MARKERS if marker in text]
    notes["nav_markers"] = nav_hits[:4]
    if len(nav_hits) >= 3:
        blocking.append("NAV_POLLUTION")

    others = [row["title"] for row in corpus_records
              if row["company"] != record["company"] and row.get("title")
              and squash(row["title"]) != squash(strip_channel(record["title"]))
              and len(squash(row["title"])) >= 6 and row["title"] in text]
    notes["other_company_titles_in_body"] = others[:3]
    if others:
        blocking.append("OTHER_JOB_BODY_POLLUTION")

    if len(text) < 300:
        review.append("SHORT_BODY")

    # ── requirement strength shape ──────────────────────────────────────────
    mixed = [line for line in lines if len(line) >= 8 and is_compound_mixed(line)]
    notes["compound_mixed_lines"] = len(mixed)
    notes["compound_mixed_samples"] = [line[:60] for line in mixed[:2]]
    notes["overlong_lines"] = sum(1 for line in lines if len(line) > 400)

    # ── source grade ────────────────────────────────────────────────────────
    grade = record.get("source_grade", "")
    notes["source_grade"] = grade
    if grade.startswith("MANUAL_CURATED_COMMUNITY"):
        review.append("COMMUNITY_SOURCE_NOT_OFFICIAL")

    # ── duplicates ──────────────────────────────────────────────────────────
    notes["duplicate_body"] = [row["jd_id"] for row in corpus_records
                               if row["jd_id"] != record["jd_id"] and row.get("sha256") == body_sha]
    notes["duplicate_company_title"] = [row["jd_id"] for row in corpus_records
                                        if row["jd_id"] != record["jd_id"]
                                        and row["company"] == record["company"]
                                        and squash(row["title"]) == squash(record["title"])]
    notes["duplicate_job_id"] = [row["jd_id"] for row in corpus_records
                                 if row["jd_id"] != record["jd_id"] and job_id
                                 and job_id in (row.get("url") or "")]
    for key in ("duplicate_body", "duplicate_company_title", "duplicate_job_id"):
        if notes[key]:
            blocking.append(key.upper())

    verdict = "REJECT" if blocking else ("NEEDS_REVIEW" if review else "PASS")

    title = literal or record["title"]
    direction = "OTHER"
    for token, label in (("Agent", "AI Agent"), ("RAG", "RAG"), ("大模型", "LLM/算法"),
                         ("算法", "LLM/算法"), ("后端", "后端"), ("Java", "后端"),
                         ("Python", "Python"), ("云原生", "云原生/基础软件"),
                         ("内核", "云原生/基础软件"), ("虚拟化", "云原生/基础软件"),
                         ("嵌入式", "嵌入式"), ("C/C++", "C/C++"), ("测试", "测试"),
                         ("数据", "数据"), ("安全", "安全"), ("Unity", "客户端"),
                         ("客户端", "客户端"), ("全栈", "全栈")):
        if token.lower() in title.lower():
            direction = label
            break

    return {"jd_id": record["jd_id"], "company": record["company"], "title": record["title"],
            "page_literal": literal, "url": record["url"], "verdict": verdict,
            "blocking": sorted(set(blocking)), "review": sorted(set(review)),
            "prep": sorted(set(prep)), "direction": direction, "notes": notes}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(OUT / "precheck_49.json"))
    args = parser.parse_args()

    rows, corpus = load_inputs()
    corpus_records = corpus["records"]
    details = load_detail_urls()
    results = [check(row, corpus_records, details) for row in rows]

    groups: dict[str, list[str]] = {"PASS": [], "NEEDS_REVIEW": [], "REJECT": []}
    for item in results:
        groups[item["verdict"]].append(item["jd_id"])

    reason_counts = Counter(reason for item in results
                            for reason in item["blocking"] + item["review"] + item["prep"])
    prep_counts = Counter(reason for item in results for reason in item["prep"])
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_http_calls": 0,
        "database_writes": 0,
        "scope": {"corpus_records": len(corpus_records), "excluded_targets": sorted(TARGETS.values()),
                  "checked": len(results)},
        "verdicts": {key: len(value) for key, value in groups.items()},
        "groups": groups,
        "reason_counts": dict(reason_counts.most_common()),
        "prep_counts": dict(prep_counts.most_common()),
        "label_differs_beyond_prefix": [
            {"jd_id": item["jd_id"], "recorded": item["title"], "page_literal": item["page_literal"]}
            for item in results if "IDENTITY_LABEL_WORDING_DIFFERS" in item["review"]],
        "companies": dict(Counter(item["company"] for item in results).most_common()),
        "companies_pass": dict(Counter(item["company"] for item in results
                                       if item["verdict"] == "PASS").most_common()),
        "directions": dict(Counter(item["direction"] for item in results).most_common()),
        "directions_pass": dict(Counter(item["direction"] for item in results
                                        if item["verdict"] == "PASS").most_common()),
        "identity": {
            "label_already_literal": sum(1 for item in results if item["notes"].get("label_matches_literal")),
            "label_has_channel_prefix": sum(1 for item in results
                                            if "IDENTITY_LABEL_HAS_CHANNEL_PREFIX" in item["prep"]),
            "label_spacing_only": sum(1 for item in results
                                      if "IDENTITY_LABEL_SPACING_ONLY" in item["prep"]),
            "label_wording_differs": sum(1 for item in results
                                         if "IDENTITY_LABEL_WORDING_DIFFERS" in item["review"]),
            "channel_prefix_only": sum(1 for item in results if item["notes"].get("channel_prefix_in_literal")),
            "projection_needed": sum(1 for item in results if item["notes"].get("projection_needed")),
            "no_dom_snapshot": sum(1 for item in results if "NO_DOM_SNAPSHOT" in item["review"]),
        },
        "mixed_strength_records": [{"jd_id": item["jd_id"], "lines": item["notes"]["compound_mixed_lines"]}
                                   for item in results if item["notes"]["compound_mixed_lines"]],
        "records": results,
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"checked {len(results)} / corpus {len(corpus_records)} (excluded {len(TARGETS)} targets)")
    for key in ("PASS", "NEEDS_REVIEW", "REJECT"):
        print(f"  {key:13} {len(groups[key])}")
    print("identity:", json.dumps(payload["identity"], ensure_ascii=False))
    print("prep:", dict(prep_counts.most_common()))
    print("reasons:", dict(reason_counts.most_common(14)))
    print("written", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
