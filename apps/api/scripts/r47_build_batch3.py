"""Round 35, task 3: deterministic manifest for the next real-JD batch.

No network, no model, no writes to the database. Same projection rule as round 34:
the page's own company and job-title strings are placed on top of the body blocks
the extractor captured; nothing else is added, no section heading is invented.

Refusals are hard: missing DOM, hash mismatch against the corpus, page literal not
found, duplicate job id / title / body against what the sandbox already holds.
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

from r40_build_batch2 import (  # noqa: E402
    DOM, CAPTURED, BASE, UI_TAIL, _compact, dom_literal, job_id_of, visible_text,
)

SANDBOX = "interview_agent_wb_jd_r31_20261116"
DSN = f"postgresql://interview_agent:interview_agent@localhost:5432/{SANDBOX}"
OUT = REPO / "data/r47_batch3"
SPAN_CHARS = 600
MAX_CHARS = 24000

PICKS = [
    {"jd_key": "KYLINOS_SEC_5480b909", "record": "KYLINOS_5480b909", "company": "麒麟软件",
     "direction": "安全研发", "why": "官方详情页；安全研发方向，与已发布的内核/AI研发不重叠"},
    {"jd_key": "KYLINOS_EMB_cd3ac686", "record": "KYLINOS_cd3ac686", "company": "麒麟软件",
     "direction": "嵌入式", "why": "官方详情页；嵌入式/板级方向，补操作系统生态侧的第二种岗位"},
    {"jd_key": "MOONTON_QA_008", "record": "OFFICIAL_CAND_008", "company": "沐瞳科技",
     "direction": "测试/QA", "why": "官方飞书页；AI 测试平台方向，首批 5 份没有测试岗"},
    {"jd_key": "MOONTON_PM_013", "record": "OFFICIAL_CAND_013", "company": "沐瞳科技",
     "direction": "AI 产品", "why": "官方飞书页；AI Agent 产品经理，验证平台能否处理非研发岗"},
    {"jd_key": "NIO_CV_023", "record": "OFFICIAL_CAND_023", "company": "蔚来",
     "direction": "视觉算法", "why": "官方飞书页；CV 算法方向，与被扣下的推理引擎岗不同"},
    {"jd_key": "NIO_SUPPLY_024", "record": "OFFICIAL_CAND_024", "company": "蔚来",
     "direction": "供应链算法", "why": "官方飞书页；运筹/供应链算法，非大模型方向，检验通用性"},
]

ALTERNATES = [
    {"jd_key": "MOONTON_TA_7683867480011622697", "record": "沐瞳科技_POS_7683867480011622697",
     "company": "沐瞳科技", "direction": "技术美术", "why": "正文最长（2183 字），TA/AI 美术平台方向"},
    {"jd_key": "KYLINOS_ANDROID_dd5441a9", "record": "KYLINOS_dd5441a9", "company": "麒麟软件",
     "direction": "Android 系统", "why": "417 字，篇幅偏短，仅作替补"},
]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def span_split_lines(text: str) -> dict:
    """How many page lines a fixed 600-char evidence chunk would cut in half."""
    boundaries = list(range(SPAN_CHARS, min(len(text), MAX_CHARS), SPAN_CHARS))
    split, cursor = [], 0
    for line in text.splitlines():
        if not line.strip():
            cursor += len(line) + 2
            continue
        start = text.find(line, cursor)
        if start < 0:
            continue
        end = start + len(line)
        cursor = end
        cut = [b for b in boundaries if start < b < end]
        if cut:
            split.append({"text": line.strip()[:70], "start": start, "end": end, "cut_at": cut})
    return {"lines": len([l for l in text.splitlines() if l.strip()]), "split": split}


def build_one(pick: dict, corpus: dict) -> dict:
    record = corpus[pick["record"]]
    body_path = CAPTURED / f"{pick['record']}.txt"
    dom_path = DOM / f"{pick['record']}.html"
    if not body_path.exists() or not dom_path.exists():
        raise SystemExit(f"missing capture for {pick['record']}")
    # Universal-newline read: the corpus hash is defined over LF-normalised text.
    body_text = body_path.read_text(encoding="utf-8")
    if sha256(body_text) != record["sha256"]:
        raise SystemExit(f"body hash mismatch for {pick['record']}: "
                         f"{sha256(body_text)} != {record['sha256']}")
    body_lines = [line.rstrip() for line in body_text.splitlines() if line.strip()]
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

    header = [pick["company"], literal]
    text = "\n".join(header + body_lines) + "\n"
    if literal not in text or pick["company"] not in text:
        raise SystemExit(f"identity line missing from projection for {pick['record']}")

    job_id, kind = job_id_of(record["url"])
    if not job_id:
        raise SystemExit(f"no stable job id for {pick['record']}")

    OUT.joinpath("text").mkdir(parents=True, exist_ok=True)
    target = OUT / "text" / f"{pick['jd_key']}.txt"
    # LF only: CRLF would inject \r into every evidence offset the model sees.
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)

    duty_heads = ("工作职责", "岗位职责", "职位描述", "岗位描述", "职责描述", "工作内容")
    skill_heads = ("任职资格", "任职要求", "职位要求", "岗位要求", "我们希望你", "技能要求", "任职条件")
    sections = {"duty_heading": next((l.strip() for l in body_lines
                                      if l.strip().startswith(duty_heads)), None),
                "skill_heading": next((l.strip() for l in body_lines
                                       if l.strip().startswith(skill_heads)), None)}
    risk = span_split_lines(text)
    return {
        "jd_key": pick["jd_key"], "company": pick["company"], "job_title": literal,
        "display_title": record["title"], "direction": pick["direction"],
        "selection_reason": pick["why"], "source_url": record["url"],
        "job_id": job_id, "job_id_kind": kind, "source_grade": record["source_grade"],
        "captured_at": record.get("captured_at"),
        "dom_path": str(dom_path.relative_to(REPO)), "dom_sha256": sha256(html),
        "body_blocks_sha256": sha256(body_text),
        "text_path": str(target.relative_to(REPO)), "text_sha256": sha256(text),
        "text_chars": len(text), "body_chars": record["chars"],
        "projection": {"mode": "PAGE_IDENTITY_HEADER + BODY_BLOCKS", "header_lines": header,
                       "body_lines": len(body_lines), "added_section_headings": 0,
                       "note": "两行身份取自页面自身（DOM 标题元素 + 站点企业名），正文块逐字未改，未补造小节标题"},
        "section_headers": sections,
        "identity": {"label_is_page_literal": True, "literal_in_dom": True,
                     "literal_in_visible_text": True, "company_in_page": True},
        "evidence_chunk_risk": {"span_chars": SPAN_CHARS, "lines": risk["lines"],
                                "split_lines": len(risk["split"]), "samples": risk["split"][:3]},
    }


async def existing() -> dict:
    """What the sandbox already holds, for duplicate refusal."""
    conn = await asyncpg.connect(DSN)
    try:
        assert await conn.fetchval("select current_database()") == SANDBOX
        rows = await conn.fetch(
            "select v.id, v.publication_status, v.version_number, v.content_fingerprint, "
            "j.canonical_title, j.normalized_title, c.canonical_name "
            "from job_description_versions v join jobs j on j.id = v.job_id "
            "join companies c on c.id = j.company_id order by v.id")
    finally:
        await conn.close()
    return {"rows": [dict(r) for r in rows],
            "titles": {r["normalized_title"] for r in rows},
            "fingerprints": {r["content_fingerprint"] for r in rows}}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=str(OUT / "source_manifest.json"))
    ap.add_argument("--include-alternates", action="store_true")
    args = ap.parse_args()
    corpus = {row["jd_id"]: row for row in
              json.loads((BASE / "jd_corpus.json").read_text(encoding="utf-8"))["records"]}

    picks = PICKS + (ALTERNATES if args.include_alternates else [])
    records = [build_one(pick, corpus) for pick in picks]

    held = await existing()
    prior = json.loads((REPO / "data/r40_batch2/source_manifest.json").read_text(encoding="utf-8"))
    prior_ids = {row["job_id"] for row in prior["records"]}
    for record in records:
        if record["job_id"] in prior_ids:
            raise SystemExit(f"job id already used in batch 2: {record['job_id']}")
        if record["job_id"] in {r["job_id"] for r in records} - {record["job_id"]}:
            raise SystemExit(f"duplicate job id inside batch 3: {record['job_id']}")
        if record["text_sha256"] in held["fingerprints"]:
            raise SystemExit(f"identical body already stored: {record['jd_key']}")
        compact = "".join(record["job_title"].split())
        for row in held["rows"]:
            if "".join((row["canonical_title"] or "").split()) == compact:
                raise SystemExit(f"same title already stored: {row['canonical_title']}")

    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "model_http_calls": 0,
               "database": SANDBOX, "batch": 3, "records": records,
               "existing_sandbox": held["rows"]}
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for record in records:
        print(f"{record['jd_key']:36} {record['company'][:6]:8} {record['job_title'][:30]:32} "
              f"{record['text_chars']:>5}字 id={record['job_id'][:14]} "
              f"split={record['evidence_chunk_risk']['split_lines']}")
    print("written", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
