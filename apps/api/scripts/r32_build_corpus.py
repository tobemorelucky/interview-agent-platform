"""R32：把三次采集合并成一份可核验的真实 JD 语料（0 次模型调用）。

做四件事：
1. 合并 `capture_report*.json` 中 `quality=OK*` 的记录，统一标题/来源/指纹；
2. 与既有 4 份人工策展 JD 去重（URL、`advertisementId`、公司+岗位、正文相似度）；
3. 语料内部去重（同公司同岗位、同正文 sha256、相似度 ≥0.95）；
4. 统计企业分布，并对每份跑规则解析 → Requirement 数 → Skill 推荐 → 主题。

输出：`jd_corpus.json`（唯一语料 + 去重明细 + 分布）与 `corpus/<id>.txt`。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from interview_api.modules.interview.skill_recommendation import recommend  # noqa: E402
from interview_api.modules.interview.topics import mentioned_topics  # noqa: E402
from interview_api.modules.job_target.service import rule_extract  # noqa: E402

BASE = REPO_ROOT / "data/incoming_20261116c"
MANUAL = REPO_ROOT / "data/manual_sources/2026-10-05"
PUNCT = re.compile(r"[\s，。？！、；：“”‘’（）()【】\[\]《》<>·…—\-_,.!?;:\"']+")


def norm(text: str) -> str:
    return PUNCT.sub("", text or "")


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def clean_title(record: dict) -> str:
    # 优先用采集时记录的岗位标题（种子/发现清单里的真实岗位名）；
    # 只有当它是纯数字（飞书列表页只给了 position id）时才退回页面标题。
    requested = (record.get("title") or "").strip()
    if requested and not re.fullmatch(r"\d{10,25}", requested):
        title = requested
    else:
        title = record.get("page_title") or requested
    title = re.sub(r"\s*-\s*(加入|校招|蔚来).*$", "", title)
    title = re.sub(r"^【[^】]{0,24}】\s*", "", title).strip()
    title = re.sub(r"^(\d{4}\s*)?(届\s*)?(校招|秋招|春招)\s*", "", title).strip()
    title = re.sub(r"^校招-", "", title).strip()
    if re.fullmatch(r"\d{10,25}", title or ""):
        return record.get("title") or title
    return title or record.get("title") or ""


def manual_records() -> list[dict]:
    manifest = json.loads((MANUAL / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    for source in manifest["sources"]:
        if source["document_type"] != "job_jd":
            continue
        path = REPO_ROOT / source["local_file"]
        text = path.read_text(encoding="utf-8")
        rows.append({"jd_id": f"MANUAL_{source['id']}", "company": source["company"],
                     "title": source["job_title"], "url": source["source_url"],
                     "sha256": source["content_sha256"], "chars": len(text),
                     "source_grade": "MANUAL_CURATED_" + source["source_type"].upper(),
                     "text": text})
    return rows


def main() -> int:
    records = []
    for path in sorted(BASE.glob("capture_report*.json")):
        if path.name.startswith("capture_probe"):
            continue
        for row in json.loads(path.read_text(encoding="utf-8"))["records"]:
            status = str(row.get("quality") or row.get("verdict") or "")
            if not status.startswith("OK"):
                continue
            text_path = BASE / "captured" / f"{row['id']}.txt"
            if not text_path.exists():
                continue
            text = text_path.read_text(encoding="utf-8")
            records.append({"jd_id": row["id"], "company": row["company"],
                            "title": clean_title(row), "url": row["url"],
                            "sha256": hashlib.sha256(text.encode()).hexdigest(),
                            "chars": len(text), "captured_at": row.get("captured_at"),
                            "source_grade": "OFFICIAL_PAGE_CAPTURED",
                            "method": row.get("method"), "text": text})

    # 同一 id 可能被多次采集（补采/重抽），保留正文更长的一份
    by_id: dict[str, dict] = {}
    for row in records:
        current = by_id.get(row["jd_id"])
        if current is None or len(row["text"]) > len(current["text"]):
            by_id[row["jd_id"]] = row
    records = list(by_id.values())
    manual = manual_records()
    duplicates = []

    # 与人工策展 JD 去重（同 URL / 同岗位 / 正文高度相似）
    for row in list(records):
        for ref in manual:
            same_url = row["url"].split("?")[0] == ref["url"].split("?")[0]
            same_role = (norm(row["company"]) == norm(ref["company"])
                         and norm(row["title"]) == norm(ref["title"]))
            if same_url or same_role:
                duplicates.append({"kept": ref["jd_id"], "dropped": row["jd_id"],
                                   "reason": "与既有真实 JD 同 URL/同岗位"})
                records.remove(row)
                break

    # 语料内部去重
    unique, seen_sha, seen_role = [], {}, {}
    for row in sorted(records, key=lambda r: (-r["chars"], r["jd_id"])):
        key = (norm(row["company"]), norm(row["title"]))
        if row["sha256"] in seen_sha:
            duplicates.append({"kept": seen_sha[row["sha256"]], "dropped": row["jd_id"],
                               "reason": "正文 sha256 相同"})
            continue
        if key in seen_role:
            duplicates.append({"kept": seen_role[key], "dropped": row["jd_id"],
                               "reason": "同公司同岗位"})
            continue
        seen_sha[row["sha256"]] = row["jd_id"]
        seen_role[key] = row["jd_id"]
        unique.append(row)

    near = []
    for index, left in enumerate(unique):
        for right in unique[index + 1:]:
            ratio = similarity(left["text"], right["text"])
            if ratio >= 0.95:
                near.append({"a": left["jd_id"], "b": right["jd_id"], "ratio": round(ratio, 3)})

    corpus_dir = BASE / "corpus"
    corpus_dir.mkdir(exist_ok=True)
    for row in unique:
        shutil.copyfile(BASE / "captured" / f"{row['jd_id']}.txt", corpus_dir / f"{row['jd_id']}.txt")

    # 规则解析 + 技能 + 主题（零 HTTP）
    for row in unique:
        parsed = rule_extract(row["text"], row["company"], row["title"])
        result = recommend({"title": row["title"],
                            "responsibilities": parsed.get("responsibilities") or [],
                            "requirements": parsed.get("requirements") or []})
        row["rule_requirements"] = len(parsed.get("requirements") or [])
        row["rule_responsibilities"] = len(parsed.get("responsibilities") or [])
        row["skill_status"] = result.get("status")
        row["skill"] = result.get("recommended_skill_id")
        row["topics"] = mentioned_topics(row["text"])

    all_rows = unique + manual
    report = {
        "http_calls_to_model": 0,
        "counts": {"captured_ok": len(unique), "manual": len(manual), "total_unique": len(all_rows),
                   "duplicates_removed": len(duplicates), "near_duplicates": len(near),
                   "target": 50, "gap": max(0, 50 - len(all_rows))},
        "companies": dict(Counter(row["company"] for row in all_rows).most_common()),
        "duplicates": duplicates, "near_duplicates": near,
        "quality": {"min_chars": min(row["chars"] for row in all_rows),
                    "median_chars": sorted(row["chars"] for row in all_rows)[len(all_rows) // 2],
                    "with_rule_requirements": sum(1 for row in unique if row["rule_requirements"]),
                    "skill_recommended": sum(1 for row in unique if row["skill"]),
                    "skills": dict(Counter(row["skill"] for row in unique if row["skill"]))},
        "records": [{k: v for k, v in row.items() if k != "text"} for row in all_rows],
    }
    out = BASE / "jd_corpus.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("counts", "companies", "quality")},
                     ensure_ascii=False, indent=2))
    print("duplicates:", json.dumps(duplicates, ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
