"""R29 第二批：13 条公开 JD 候选的只读来源核查（0 次模型调用）。

只做三件事：
1. 逐条 GET 公开页面（浏览器 UA、单次、超时 25s），保存原始 HTML 与抽取文本；
2. 判定正文是否真的在页面里（`BODY_PRESENT` / `JS_SHELL` / `LIST_PAGE` / `HTTP_ERROR`）；
3. 对拿到的正文算 sha256、抽岗位标题，供去重与后续「合法 Research 导入」使用。

**不写任何业务表、不发布 JD、不调用模型。** 判定为 `BODY_PRESENT` 的正文仍然只是
「候选原件」，必须经 Research/Owner/Evidence 流程才能进沙箱业务表。
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK = REPO_ROOT / "data/incoming_20261116/真实面经与JD扩充包_20261116"
OUT = REPO_ROOT / "data/incoming_20261116/fetched"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
BODY_MARKERS = ("岗位职责", "任职要求", "职位描述", "工作职责", "任职资格", "岗位要求")
LIST_MARKERS = ("投递简历", "职位列表", "热招岗位")


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def fetch(url: str) -> dict:
    request = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9", "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read()
            status = response.status
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "bytes": 0, "text": "", "error": f"HTTP_{exc.code}"}
    except Exception as exc:  # noqa: BLE001 - 网络异常要如实记录，不能当成正文缺失
        return {"status": None, "bytes": 0, "text": "", "error": type(exc).__name__}
    html = raw.decode(charset, errors="ignore")
    return {"status": status, "bytes": len(raw), "text": visible_text(html), "error": None}


def classify(text: str, status: int | None) -> str:
    if status is None:
        return "HTTP_ERROR"
    marker_hits = sum(1 for marker in BODY_MARKERS if marker in text)
    if marker_hits >= 1 and len(text) >= 600:
        # 列表页也可能含「岗位职责」，靠标题数量区分单岗位与多岗位页。
        headings = len(re.findall(r"「?2027校招」?\S{2,20}(?:工程师|专员|经理|助理)", text))
        return "LIST_PAGE" if headings >= 5 else "BODY_PRESENT"
    if len(text) < 400 or sum(1 for m in LIST_MARKERS if m in text) >= 2:
        return "JS_SHELL"
    return "NO_MARKER"


def main() -> int:
    rows = list(csv.DictReader((PACK / "03_2027校招JD待采集官方来源.csv").open(encoding="utf-8-sig")))
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"http_calls_to_model": 0, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "candidates": []}
    for row in rows:
        result = fetch(row["url"])
        verdict = classify(result["text"], result["status"])
        digest = hashlib.sha256(result["text"].encode("utf-8")).hexdigest() if result["text"] else None
        slug = f"{row['id']}_{re.sub(r'[^a-z0-9]+', '', row['company'].lower()) or 'co'}"
        saved = None
        if result["text"]:
            target = OUT / f"{slug}.txt"
            target.write_text(result["text"], encoding="utf-8")
            saved = str(target.relative_to(REPO_ROOT))
        report["candidates"].append({
            "id": row["id"], "company": row["company"], "role": row["role"],
            "track": row["track"], "city": row["city"], "url": row["url"],
            "source_grade": row["source_grade"], "overlap_note": row["overlap_note"],
            "http_status": result["status"], "bytes": result["bytes"],
            "text_chars": len(result["text"]), "error": result["error"],
            "verdict": verdict, "text_sha256": digest, "saved_text": saved,
        })
        print(f"{row['id']:12} {verdict:12} {result['status']} bytes={result['bytes']:>7} chars={len(result['text']):>6} {row['company']}")
        time.sleep(1.2)
    out = REPO_ROOT / "data/incoming_20261116/jd_source_check.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
