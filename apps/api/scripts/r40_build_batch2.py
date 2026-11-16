"""Round 34: deterministic projections for the five new real JDs.

No network, no model, no database. Every line written into a projection is copied
verbatim from either the stored body blocks or the rendered DOM; the only edit is
that the page's own company and job-title strings are placed at the top, because
the block extractor captured the JD body without the posting header.

Nothing that the site does not contain is added: no section headings, no
normalised wording, no summary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from interview_api.modules.public_career.fingerprints import normalize_text  # noqa: E402

BASE = REPO / "data/incoming_20261116c"
CAPTURED = BASE / "captured"
DOM = CAPTURED / "dom"
OUT = REPO / "data/r40_batch2"

UI_TAIL = {"立即投递", "立即", "投递", "投递简历", "分享", "收藏", "返回顶部", "职位详情", "查看详情"}

PICKS = [
    {"jd_key": "UNITY_AGENT_029", "record": "OFFICIAL_CAND_029", "company": "Unity中国",
     "direction": "AI Agent", "why": "Unity 官方招聘页；AI Agent 算法方向，与既有 AI 研发岗差异明显"},
    {"jd_key": "MODELBEST_LLM_031", "record": "OFFICIAL_CAND_031", "company": "面壁智能",
     "direction": "LLM/RAG", "why": "面壁官方飞书招聘页；RAG/Agent 应用方向，正文含检索与框架要求"},
    {"jd_key": "MOONTON_BACKEND_7673023051731503402", "record": "沐瞳科技_POS_7673023051731503402",
     "company": "沐瞳科技", "direction": "后端", "why": "沐瞳官方页；Java/Python 后端方向，补后端样本"},
    {"jd_key": "NIO_INFER_021", "record": "OFFICIAL_CAND_021", "company": "蔚来",
     "direction": "推理引擎", "why": "蔚来官方页；推理引擎/性能优化方向，与 AI Agent 岗不重叠"},
    {"jd_key": "KYLINOS_KERNEL_aa9be729", "record": "KYLINOS_aa9be729", "company": "麒麟软件",
     "direction": "基础软件/内核", "why": "华为无 DOM 快照，按规则替换；麒麟官方详情页内核岗，独立 jobAdId"},
]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def visible_text(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())


def dom_literal(html: str, expected: str) -> str | None:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for selector, attr in (('[data-test="jobTitle"]', None), ("h4[title]", "title"), (".job-title", None)):
        for node in soup.select(selector):
            value = (node.get(attr) if attr else node.get_text(strip=True)) or ""
            value = value.strip()
            if value and _compact(strip_channel(value)) == _compact(strip_channel(expected)):
                return value
    for line in visible_text(html).splitlines():
        if "://" in line or len(line) > 80:
            continue
        if line and _compact(strip_channel(line)) == _compact(strip_channel(expected)) and line != expected:
            return line
    return None


def _compact(value: str) -> str:
    return "".join(normalize_text(value or "").split())


def strip_channel(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"^[「【\[]([^」】\]]{0,24})[」】\]]\s*", "", text)
    text = re.sub(r"^(校招|秋招|春招)\s*[-－—]?\s*", "", text)
    text = re.sub(r"^(19|20)\d{2}\s*[届年]?\s*[-－—]?\s*", "", text)
    return re.sub(r"^-\s*", "", text).strip()


def job_id_of(url: str) -> tuple[str | None, str]:
    for pattern, kind in ((r"position/(\d{10,25})", "position_path"),
                          (r"jobAdId=([0-9a-fA-F-]{8,})", "jobAdId"),
                          (r"advertisementId=(\d+)", "advertisementId")):
        match = re.search(pattern, url or "")
        if match:
            return match.group(1), kind
    return None, "none"


def build_one(pick: dict, corpus: dict) -> dict:
    record = corpus[pick["record"]]
    body_path = CAPTURED / f"{pick['record']}.txt"
    dom_path = DOM / f"{pick['record']}.html"
    if not body_path.exists() or not dom_path.exists():
        raise SystemExit(f"missing capture for {pick['record']}")
    body_lines = [line.rstrip() for line in body_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(body_lines) < 4:
        raise SystemExit(f"body too short for {pick['record']}")
    if any(line.strip() in UI_TAIL for line in body_lines):
        raise SystemExit(f"UI tail inside body for {pick['record']}")

    html = dom_path.read_text(encoding="utf-8", errors="ignore")
    literal = dom_literal(html, record["title"])
    if not literal:
        raise SystemExit(f"page literal not found in DOM for {pick['record']}")
    visible = visible_text(html)
    if _compact(literal) not in _compact(visible):
        raise SystemExit(f"literal not in visible DOM text for {pick['record']}")
    if pick["company"][:2] not in visible:
        raise SystemExit(f"company brand not in page for {pick['record']}")

    # Page-own strings, placed above the body the block extractor captured.
    header = [pick["company"], literal]
    text = "\n".join(header + body_lines) + "\n"
    if literal not in text or pick["company"] not in text:
        raise SystemExit(f"identity line missing from projection for {pick['record']}")

    OUT.joinpath("text").mkdir(parents=True, exist_ok=True)
    target = OUT / "text" / f"{pick['jd_key']}.txt"
    target.write_text(text, encoding="utf-8")

    job_id, kind = job_id_of(record["url"])
    if not job_id:
        raise SystemExit(f"no stable job id for {pick['record']}")
    return {
        "jd_key": pick["jd_key"], "company": pick["company"], "job_title": literal,
        "display_title": record["title"], "direction": pick["direction"], "selection_reason": pick["why"],
        "source_url": record["url"], "job_id": job_id, "job_id_kind": kind,
        "source_grade": record["source_grade"], "captured_at": record.get("captured_at"),
        "dom_path": str(dom_path.relative_to(REPO)), "dom_sha256": sha256(html),
        "body_blocks_sha256": sha256(body_path.read_text(encoding="utf-8")),
        "text_path": str(target.relative_to(REPO)), "text_sha256": sha256(text),
        "text_chars": len(text),
        "projection": {"mode": "PAGE_IDENTITY_HEADER + BODY_BLOCKS",
                       "header_lines": header, "body_lines": len(body_lines),
                       "added_section_headings": 0,
                       "note": "两行身份取自页面自身（DOM 标题元素 + 站点企业名），正文块逐字未改"},
        "identity": {"label_is_page_literal": True, "literal_in_dom": True,
                     "literal_in_visible_text": True, "company_in_page": True},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(OUT / "source_manifest.json"))
    args = parser.parse_args()
    corpus = {row["jd_id"]: row for row in
              json.loads((BASE / "jd_corpus.json").read_text(encoding="utf-8"))["records"]}
    records = [build_one(pick, corpus) for pick in PICKS]

    # Duplicate checks against the corpus and against each other.
    seen_urls = {row["url"] for row in corpus.values() if row.get("url")}
    for record in records:
        if record["source_url"] not in seen_urls:
            raise SystemExit(f"url not in corpus: {record['source_url']}")
    if len({record["job_id"] for record in records}) != len(records):
        raise SystemExit("duplicate job id inside the batch")

    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "model_http_calls": 0,
               "records": records}
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for record in records:
        print(f"{record['jd_key']:44} {record['company'][:8]:10} {record['job_title'][:30]:32} "
              f"{record['text_chars']:>5}字 id={record['job_id'][:12]}")
    print("written", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
