"""R31: build the verified source package for the three target real JDs.

Zero model calls. One fresh browser render per JD only to prove the posting is
still live; the raw DOM, the projected text, its SHA-256 and the dedupe result
are written as audit artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "data" / "r34_target_jd"
CHROME = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")

FOOTER_MARKERS = ("立即投递", "投递", "收藏", "职位详情", "分享")
FOOTER_PATTERNS = (r"©", r"京ICP", r"京公网安备", r"隐私政策", r"Powered by", r"^合$", r"^\|$")
OFFLINE_MARKERS = ("该职位已下线", "职位不存在", "页面不存在", "404")

TARGETS = [
    {
        "jd_key": "NIO_AIA_025",
        "company": "蔚来",
        "job_title": "车端AI Agent与应用生态系统架构工程师",
        "url": "https://nio.jobs.feishu.cn/campus/position/7673028675729656105/detail",
        "job_id_kind": "FEISHU_POSITION_ID",
        "job_id": "7673028675729656105",
        "round33_id": "OFFICIAL_CAND_025",
        "heading_responsibility": ("你将负责",),
        "heading_requirement": ("我们希望你具备",),
        "heading_bonus": ("加分项",),
    },
    {
        "jd_key": "MOONTON_AGENT_005",
        "company": "沐瞳科技",
        "job_title": "Agent开发工程师（平台）",
        "url": "https://moonton.jobs.feishu.cn/campus/position/7668253932858362118/detail",
        "job_id_kind": "FEISHU_POSITION_ID",
        "job_id": "7668253932858362118",
        "round33_id": "OFFICIAL_CAND_005",
        "heading_responsibility": (),
        "heading_requirement": (),
        "heading_bonus": ("加分项",),
    },
    {
        "jd_key": "KYLINOS_AI_002",
        "company": "麒麟软件",
        "job_title": "AI研发工程师",
        "url": "https://kylinos.zhiye.com/campus/detail?jobAdId=0b8de09c-3508-4144-99b2-3ddcfd88a29b",
        "job_id_kind": "BEISEN_JOB_AD_ID",
        "job_id": "0b8de09c-3508-4144-99b2-3ddcfd88a29b",
        "round33_id": "OFFICIAL_CAND_002",
        "heading_responsibility": ("工作职责", "岗位职责"),
        "heading_requirement": ("任职资格", "任职要求"),
        "heading_bonus": ("加分项",),
    },
]


def render(url: str, timeout_ms: int = 20000) -> tuple[str, bool]:
    with tempfile.TemporaryDirectory() as tmp:
        user_dir = Path(tmp) / "profile"
        proc = subprocess.run(
            [str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--user-data-dir={user_dir}", f"--virtual-time-budget={timeout_ms}",
             "--dump-dom", url],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=120,
        )
    return proc.stdout or "", bool(proc.stdout)


def visible_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]


def norm(text: str) -> str:
    value = re.sub(r"[\s\u3000]", "", text or "")
    value = re.sub(r"^【[^】]{0,24}】", "", value)
    value = re.sub(r"^「[^」]{0,24}」", "", value)
    value = re.sub(r"^(校招|社招|实习)[-—]", "", value)
    return value


LABEL_PREFIXES = ("工作地点", "发布时间", "岗位职责", "任职要求", "投递地址", "职位描述", "职位要求")


def loose(text: str) -> str:
    """Punctuation/label-insensitive skeleton, used only to classify a diff."""
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text or "")
    for label in LABEL_PREFIXES:
        if value.startswith(label):
            value = value[len(label):]
    return value


def is_footer(line: str) -> bool:
    if line in FOOTER_MARKERS:
        return True
    return any(re.search(pattern, line) for pattern in FOOTER_PATTERNS)


def project(lines: list[str], job_title: str) -> tuple[list[str], dict]:
    """DOC_TITLE + [JOB_TITLE_LINE .. last body line]; global nav between them is dropped."""
    title_index = None
    for index, line in enumerate(lines[1:], start=1):
        if norm(line) == norm(job_title):
            title_index = index
            break
    if title_index is None:
        raise SystemExit(f"job title not found in visible text: {job_title}")
    end = len(lines)
    for index in range(title_index + 1, len(lines)):
        if is_footer(lines[index]):
            end = index
            break
    dropped_head = lines[1:title_index]
    dropped_tail = lines[end:]
    kept = [lines[0]] + lines[title_index:end]
    return kept, {"dropped_nav_lines": dropped_head, "dropped_footer_lines": dropped_tail}


def numbered_block(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def section(lines: list[str], start_heading: tuple[str, ...], stop: tuple[str, ...]) -> list[str]:
    if not start_heading:
        return []
    start = None
    for index, line in enumerate(lines):
        if any(head in line for head in start_heading):
            start = index + 1
            break
    if start is None:
        return []
    out = []
    for line in lines[start:]:
        if any(head in line for head in stop):
            break
        if is_footer(line):
            break
        out.append(line)
    return out


def split_blocks(body: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Return (responsibilities, requirements, bonuses) as literal line lists."""
    resp, req, bonus = [], [], []
    mode = None
    seen_second_start = False
    for line in body:
        marker = re.match(r"^\s*(\d+)[\.、]", line)
        if marker and marker.group(1) == "1":
            if mode is None:
                mode = "RESP"
            elif mode == "RESP" and not seen_second_start:
                mode = "REQ"
                seen_second_start = True
        if "加分项" in line:
            mode = "BONUS"
            continue
        if mode == "RESP":
            resp.append(line)
        elif mode == "REQ":
            req.append(line)
        elif mode == "BONUS":
            bonus.append(line)
    return resp, req, bonus


def build(config: dict) -> dict:
    html, ok = render(config["url"])
    if not ok:
        raise SystemExit(f"render failed: {config['url']}")
    lines = visible_lines(html)
    joined = "\n".join(lines)
    offline = [marker for marker in OFFLINE_MARKERS if marker in joined]
    kept, dropped = project(lines, config["job_title"])
    text = "\n".join(kept)

    body = kept[1:]
    resp = section(kept, config["heading_responsibility"], ("任职资格", "任职要求", "我们希望你具备", "加分项"))
    req = section(kept, config["heading_requirement"], ("加分项",))
    bonus = section(kept, config["heading_bonus"], ())
    if not resp and not req:
        resp, req, bonus = split_blocks(body)

    dom_path = OUT / "dom" / f"{config['jd_key']}.html"
    text_path = OUT / "text" / f"{config['jd_key']}.txt"
    dom_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    dom_path.write_text(html, encoding="utf-8")
    text_path.write_text(text + "\n", encoding="utf-8")

    prior_dom = REPO / "data/incoming_20261116c/captured/dom" / f"{config['round33_id']}.html"
    prior_sha = sha256(prior_dom.read_bytes()).hexdigest() if prior_dom.exists() else None
    now_sha = sha256(html.encode("utf-8")).hexdigest()
    # DOM bytes are not stable across renders (nonces/timestamps); the claim we
    # need is that the *body text* still contains every line captured in R33.
    prior_text_path = REPO / "data/incoming_20261116c/captured" / f"{config['round33_id']}.txt"
    prior_lines = ([line for line in prior_text_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                   if prior_text_path.exists() else [])
    current_norm = norm(text)
    # Compare line by line: norm() removes whitespace, so it must not be applied
    # to the whole document before splitting.
    missing = [line for line in prior_lines if norm(line) and norm(line) not in current_norm]
    current_loose = loose(text)
    missing_classified = [{"line": line,
                           "kind": "FORMAT_VARIANT" if loose(line) in current_loose else "CONTENT_MISSING"}
                          for line in missing]

    return {
        "jd_key": config["jd_key"],
        "company": config["company"],
        "job_title": config["job_title"],
        "job_id_kind": config["job_id_kind"],
        "job_id": config["job_id"],
        "source_url": config["url"],
        "source_grade": "OFFICIAL_PAGE_CAPTURED",
        "render": {"tool": "headless_chrome_dump_dom", "virtual_time_budget_ms": 20000,
                   "rendered_at": datetime.now(timezone.utc).isoformat()},
        "offline_markers": offline,
        "still_live": not offline and len(text) > 400,
        "dom_sha256": now_sha,
        "dom_sha256_round33": prior_sha,
        "dom_bytes_stable_vs_round33": bool(prior_sha) and prior_sha == now_sha,
        "body_text_superset_of_round33": bool(prior_lines) and not missing,
        "body_lines_missing_vs_round33": missing_classified[:10],
        "content_missing_count": sum(1 for item in missing_classified if item["kind"] == "CONTENT_MISSING"),
        "format_variant_count": sum(1 for item in missing_classified if item["kind"] == "FORMAT_VARIANT"),
        "dom_path": str(dom_path.relative_to(REPO)),
        "text_sha256": sha256((text + "\n").encode("utf-8")).hexdigest(),
        "text_path": str(text_path.relative_to(REPO)),
        "text_chars": len(text),
        "visible_lines": len(lines),
        "projection": {"rule": "DOC_TITLE + [JOB_TITLE_LINE..BODY_END]", **dropped},
        "sections": {
            "responsibilities": resp,
            "requirements": req,
            "bonuses": bonus,
            "counts": {"responsibilities": len(resp), "requirements": len(req), "bonuses": len(bonus)},
        },
        "identity_in_text": {
            "company": config["company"] in text,
            "job_title": norm(config["job_title"]) in norm(text),
        },
    }


def corpus_dedupe(records: list[dict]) -> dict:
    corpus_path = REPO / "data/incoming_20261116c/jd_corpus.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))["records"]
    out = []
    for record in records:
        same_company_title = [row["jd_id"] for row in corpus
                              if row["company"] == record["company"] and norm(row["title"]) == norm(record["job_title"])]
        same_job_id = [row["jd_id"] for row in corpus if record["job_id"] in (row.get("url") or "")]
        list_link = []
        if not same_job_id:
            # The R33 Kylinos record came from the official list page; the
            # posting identity lives in that page's own <a href>.
            for row in corpus:
                dom = REPO / "data/incoming_20261116c/captured/dom" / f"{row['jd_id']}.html"
                if dom.exists() and record["job_id"] in dom.read_text(encoding="utf-8", errors="ignore"):
                    list_link.append(row["jd_id"])
        verdict = ("SAME_POSTING_IN_ROUND33_CORPUS" if same_job_id else
                   "SAME_POSTING_VIA_LIST_PAGE_LINK" if list_link else "NEW_VS_CORPUS")
        out.append({"jd_key": record["jd_key"], "same_company_and_title": same_company_title,
                    "same_job_id_in_url": same_job_id, "same_job_id_in_list_page_dom": list_link,
                    "verdict": verdict})
    return {"against": str(corpus_path.relative_to(REPO)), "records": out}


async def formal_db_dedupe(records: list[dict]) -> dict:
    """Read-only check against the production JD set. Never writes."""
    import asyncpg
    conn = await asyncpg.connect("postgresql://interview_agent:interview_agent@localhost:5432/interview_agent")
    try:
        rows = await conn.fetch("""
            select c.canonical_name, j.canonical_title, jdv.version_number,
                   jdv.publication_status, jdv.content_fingerprint, length(jdv.raw_text) as chars
            from job_description_versions jdv
            join jobs j on j.id = jdv.job_id
            join companies c on c.id = j.company_id
            order by jdv.id
        """)
    finally:
        await conn.close()
    published = [dict(row) for row in rows]
    out = []
    for record in records:
        same_company = [row for row in published if row["canonical_name"] == record["company"]]
        same_title = [row for row in published if norm(row["canonical_title"]) == norm(record["job_title"])]
        out.append({
            "jd_key": record["jd_key"],
            "same_company_in_formal_db": [f"{r['canonical_name']}/{r['canonical_title']}" for r in same_company],
            "same_job_title_in_formal_db": [f"{r['canonical_name']}/{r['canonical_title']}" for r in same_title],
            "duplicate": bool(same_company and same_title),
        })
    return {"against_database": "interview_agent", "formal_jd_versions": len(published),
            "formal_jds": [f"{r['canonical_name']}/{r['canonical_title']} v{r['version_number']} {r['publication_status']}"
                           for r in published],
            "records": out}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(OUT / "source_manifest.json"))
    args = parser.parse_args()
    records = [build(config) for config in TARGETS]
    import asyncio
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_http_calls": 0,
        "browser_http_requests": len(TARGETS),
        "records": records,
        "dedupe_vs_round33_corpus": corpus_dedupe(records),
        "dedupe_vs_formal_db": asyncio.run(formal_db_dedupe(records)),
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for record in records:
        print(f"{record['jd_key']:20} live={record['still_live']} chars={record['text_chars']:>5} "
              f"resp={record['sections']['counts']['responsibilities']:>2} "
              f"req={record['sections']['counts']['requirements']:>2} "
              f"bonus={record['sections']['counts']['bonuses']:>2} "
              f"id={record['identity_in_text']}")


if __name__ == "__main__":
    main()
