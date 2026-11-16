"""R29 第二批：把已获取到的公开 JD 正文按岗位切分、落盘并做去重（0 次模型调用）。

输入：`r29_jd_source_check.py` 保存的页面文本。
输出：`data/incoming_20261116/acquired/*.txt`（逐岗位正文）+ `jd_acquired.json`（来源、指纹、去重结论）。

规则：
* 只处理正文确实在页面里的来源（`BODY_PRESENT` / `LIST_PAGE`）；
* 每个岗位都要有独立标识（`jobAdId` / `advertisementId`）或独立正文指纹，否则不算独立样本；
* 同一企业只取与平台方向相关的少数岗位，其余记为「可用但未纳入」，避免单一企业刷数量；
* 不写业务库、不发布 JD、不调用模型。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE = REPO_ROOT / "data/incoming_20261116"
FETCHED = BASE / "fetched"
ACQUIRED = BASE / "acquired"
MANUAL = REPO_ROOT / "data/manual_sources/2026-10-05"

# 平台方向：AI Agent / RAG / 后端 / 云原生。同一企业最多取 4 个，避免单一来源刷数。
KYLINOS_TAKE = {
    "AI研发工程师": "AI_AGENT_AND_RAG",
    "Python研发工程师": "RAG_AI_APPLICATION_BACKEND",
    "后端研发工程师（Go/Java）": "BACKEND",
    "云原生研发工程师": "CLOUD_NATIVE",
}
KYLINOS_SKIP_REASON = "同企业方向重复或与平台方向无关，未纳入（可用但未计入）"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_kylinos(text: str) -> list[dict]:
    """按岗位标题切分官方列表页；每段以「2027校招」开头，末尾带 jobAdId 投递链接。"""
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines)
              if re.match(r"^「2027校招」", line.strip())]
    if not starts:
        return []
    jobs = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = lines[start:end]
        title = re.sub(r"^「2027校招」", "", block[0].strip()).strip()
        body = "\n".join(block).strip()
        job_ad = re.search(r"jobAdId=([0-9a-fA-F-]{36})", body)
        location, published = "", ""
        for line in block[1:4]:
            if re.match(r"^\d{4}-\d{2}-\d{2}$", line.strip()):
                published = line.strip()
        if len(block) > 1:
            location = block[1].strip()
        jobs.append({"title": title, "location": location, "published": published,
                     "job_ad_id": job_ad.group(1) if job_ad else None,
                     "body": body, "sha256": digest(body)})
    return jobs


def pdd_record() -> dict | None:
    path = FETCHED / "JD_CAND_12_co.txt"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    start = text.find("AI Agent研发工程师")
    body = text[start:].strip() if start >= 0 else text.strip()
    return {"title": "AI Agent研发工程师", "location": "上海市长宁区",
            "published": "2026-07-17", "job_ad_id": None,
            "body": body, "sha256": digest(body)}


def manual_records() -> list[dict]:
    manifest = json.loads((MANUAL / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    for source in manifest["sources"]:
        if source["document_type"] != "job_jd":
            continue
        rows.append({"id": source["id"], "company": source["company"],
                     "title": source["job_title"], "url": source["source_url"],
                     "source_type": source["source_type"],
                     "content_sha256": source["content_sha256"]})
    return rows


def main() -> int:
    source_check = json.loads((BASE / "jd_source_check.json").read_text(encoding="utf-8"))
    verdicts = {row["id"]: row for row in source_check["candidates"]}
    ACQUIRED.mkdir(parents=True, exist_ok=True)
    manual = manual_records()
    manual_urls = {row["url"] for row in manual}
    manual_ads = {re.search(r"(?:advertisementId|jobAdId)=([^&#]+)", row["url"]).group(1)
                  for row in manual if re.search(r"(?:advertisementId|jobAdId)=([^&#]+)", row["url"])}

    report = {"http_calls_to_model": 0, "acquired": [], "skipped": [], "duplicates": []}

    # 1) 麒麟软件官方列表页：20 个岗位，取 4 个与平台方向相关的
    kylinos_text = (FETCHED / "JD_CAND_11_co.txt").read_text(encoding="utf-8")
    jobs = split_kylinos(kylinos_text)
    for job in jobs:
        taken = job["title"] in KYLINOS_TAKE
        record = {"company": "麒麟软件", "track": KYLINOS_TAKE.get(job["title"]),
                  "title": job["title"], "location": job["location"],
                  "published": job["published"],
                  "url": f"https://kylinos.zhiye.com/campus/detail?jobAdId={job['job_ad_id']}",
                  "job_ad_id": job["job_ad_id"], "sha256": job["sha256"],
                  "chars": len(job["body"]),
                  "source_page": verdicts["JD_CAND_11"]["url"],
                  "source_grade": "OFFICIAL_EMPLOYER_LIST_PAGE",
                  "identity_verified": bool(job["job_ad_id"]),
                  "body_verbatim_from_page": True}
        if not taken:
            report["skipped"].append({**record, "reason": KYLINOS_SKIP_REASON})
            continue
        target = ACQUIRED / f"KYL_{re.sub(r'[\\\\/:*?\"<>|]', '_', job['title'])}.txt"
        target.write_text(job["body"] + "\n", encoding="utf-8")
        record["saved_text"] = str(target.relative_to(REPO_ROOT))
        report["acquired"].append(record)

    # 2) 拼多多：正文在高校转载页里，身份只能算「转载」，待官方页复核
    pdd = pdd_record()
    if pdd:
        target = ACQUIRED / "PDD_AI_AGENT_DEV.txt"
        target.write_text(pdd["body"] + "\n", encoding="utf-8")
        report["acquired"].append({
            "company": "拼多多", "track": "AI_AGENT", "title": pdd["title"],
            "location": pdd["location"], "published": pdd["published"],
            "url": verdicts["JD_CAND_12"]["url"], "job_ad_id": None,
            "sha256": pdd["sha256"], "chars": len(pdd["body"]),
            "source_page": verdicts["JD_CAND_12"]["url"],
            "source_grade": "UNIVERSITY_REPOST", "identity_verified": False,
            "body_verbatim_from_page": True,
            "saved_text": str(target.relative_to(REPO_ROOT)),
            "note": "正文来自高校就业网转载的拼多多 2027 校招岗位页；雇主官方页未复核，"
                    "因此标记为待复核，不计入「已核验独立真实 JD」",
        })

    # 3) 去重：与 4 份既有真实 JD 比 URL / jobAdId / 公司+岗位 / 正文指纹
    for row in source_check["candidates"]:
        ad = re.search(r"(?:advertisementId|jobAdId)=([^&#]+)", row["url"])
        same_url = row["url"] in manual_urls
        same_ad = bool(ad and ad.group(1) in manual_ads)
        if same_url or same_ad:
            report["duplicates"].append({
                "candidate_id": row["id"], "company": row["company"], "role": row["role"],
                "url": row["url"], "duplicate_of": "既有 JD 原件",
                "reason": "同一 advertisementId / jobAdId 且同一岗位",
                "action": "去重，不重复计数",
            })

    # 4) 采集内部去重（同企业同岗位、同正文指纹）
    seen_sha, seen_title = {}, {}
    for row in report["acquired"]:
        key = (row["company"], row["title"])
        assert row["sha256"] not in seen_sha, f"正文重复: {row['title']}"
        assert key not in seen_title, f"岗位重复: {key}"
        seen_sha[row["sha256"]] = row["title"]
        seen_title[key] = row["title"]

    report["summary"] = {
        "candidates_total": len(source_check["candidates"]),
        "body_acquired": len(report["acquired"]),
        "skipped_same_employer": len(report["skipped"]),
        "duplicates_of_existing": len(report["duplicates"]),
        "js_shell_unacquired": sum(1 for row in source_check["candidates"]
                                   if row["verdict"] == "JS_SHELL"),
        "existing_verified_jd": len(manual),
        "cumulative_verified_independent": len(manual) + sum(
            1 for row in report["acquired"] if row["identity_verified"]),
    }
    out = BASE / "jd_acquired.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    for row in report["acquired"]:
        print(f"  {row['company']:8} {row['title']:26} {row['chars']:>5}字 {row['track']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
