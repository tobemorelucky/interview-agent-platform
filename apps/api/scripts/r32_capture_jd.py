"""R32：用本地无头 Chrome 采集官方招聘页的完整正文（0 次模型调用）。

为什么必须用浏览器：飞书招聘（沐瞳/蔚来/小鹏/面壁/小米…）的正文由前端渲染，
静态 GET 只能拿到 200 状态和空壳。这里用 `chrome --headless=new --dump-dom`
把渲染后的 DOM 取回来，再从页面内嵌的 `block-content` 结构里抽正文。

每份采集保存：原始 DOM（gzip 前的 html）、抽取文本、SHA-256、采集时间、页面标题。
抽不到正文就记为失败，**不补写、不用摘要顶替**。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE = REPO_ROOT / "data/incoming_20261116c"
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]
BLOCK_RE = re.compile(r'<div class="block-content">(.*?)</div>', re.S)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)


def chrome_path() -> str:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise SystemExit("未找到本地 Chrome/Edge，无法渲染招聘页")


def strip_html(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"</(p|div|li)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    for source, target in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                           ("&#39;", "'"), ("&quot;", '"')):
        text = text.replace(source, target)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def render(url: str, timeout: int = 75) -> tuple[str | None, str | None]:
    chrome = chrome_path()
    with tempfile.TemporaryDirectory(prefix="r32chrome_") as profile:
        command = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                   "--disable-dev-shm-usage", "--hide-scrollbars",
                   f"--user-data-dir={profile}", "--virtual-time-budget=18000",
                   "--dump-dom", url]
        try:
            completed = subprocess.run(command, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, "TIMEOUT"
    html = completed.stdout.decode("utf-8", errors="ignore")
    if not html.strip():
        return None, f"EMPTY_DOM(rc={completed.returncode})"
    return html, None


def _norm(text: str) -> str:
    return re.sub(r"[\s「」（）()·\-—_/]", "", text or "")


def select_list_item(html: str, expected: str | None) -> tuple[str, str] | None:
    """官方列表页把 20 个岗位放在同一个页面里，必须按岗位标题切片，不能整页重复赋值。"""
    if not expected:
        return None
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("li.content-item")
    if len(items) < 2:
        return None
    wanted = _norm(expected).replace("2027校招", "")
    for item in items:
        heading = item.select_one("h4[title]")
        title = heading.get("title") if heading else ""
        if wanted and (wanted in _norm(title) or _norm(title) in wanted):
            text = "\n".join(line.strip() for line in item.get_text("\n").splitlines() if line.strip())
            return text, "LIST_ITEM"
    return None


def extract(html: str, expected: str | None = None) -> tuple[str, str]:
    """返回 (抽取文本, 抽取方式)。"""
    sliced = select_list_item(html, expected)
    if sliced and len(sliced[0]) >= 150:
        return sliced
    blocks = [strip_html(fragment) for fragment in BLOCK_RE.findall(html)]
    blocks = [block for block in blocks if len(block) >= 30]
    text = "\n".join(blocks)
    if len(text) >= 120:
        return text, "BLOCK_CONTENT"
    # 退路：从整页可见文本里截取「岗位职责/职位描述」到「投递/分享」之间
    plain = strip_html(html)
    match = re.search(r"(岗位职责|工作内容|职位描述|工作职责)[\s\S]{40,}", plain)
    if match:
        tail = match.group(0)
        cut = re.search(r"(投递|分享|收藏|版权所有|隐私)", tail)
        return (tail[:cut.start()] if cut else tail[:4000]), "MARKER_WINDOW"
    return "", "NONE"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(BASE / "01_官方岗位原始来源种子_31条.jsonl"))
    parser.add_argument("--out-dir", default=str(BASE / "captured"))
    parser.add_argument("--report", default=str(BASE / "capture_report.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--delay", type=float, default=1.5)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    (out_dir / "dom").mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in Path(args.input).read_text(encoding="utf-8").splitlines()
            if line.strip()]
    previous = {}
    report_path = Path(args.report)
    if report_path.exists():
        previous = {row["id"]: row for row in
                    json.loads(report_path.read_text(encoding="utf-8")).get("records", [])}

    records = []
    done = 0
    for row in rows:
        ident = row.get("id") or row.get("jd_id")
        if args.skip_existing and previous.get(ident, {}).get("verdict") == "OK":
            records.append(previous[ident])
            continue
        if args.limit and done >= args.limit:
            break
        done += 1
        started = time.time()
        html, error = render(row["source_url"] if "source_url" in row else row["url"])
        record = {"id": ident, "company": row.get("company"), "title": row.get("title"),
                  "url": row.get("source_url") or row.get("url"),
                  "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        if error:
            record.update(verdict="RENDER_FAILED", error=error, seconds=round(time.time() - started, 1))
        else:
            page_title = TITLE_RE.search(html)
            text, method = extract(html, row.get("title"))
            record.update(page_title=(page_title.group(1).strip() if page_title else None),
                          method=method, chars=len(text),
                          text_sha256=hashlib.sha256(text.encode()).hexdigest() if text else None,
                          seconds=round(time.time() - started, 1))
            if len(text) >= 120:
                (out_dir / f"{ident}.txt").write_text(text + "\n", encoding="utf-8")
                (out_dir / "dom" / f"{ident}.html").write_text(html, encoding="utf-8")
                record["verdict"] = "OK"
            else:
                record["verdict"] = "NO_BODY"
        records.append(record)
        print(f"{ident:18} {record['verdict']:14} {record.get('chars', 0):>5}字 "
              f"{record['seconds']:>5}s {record.get('company','')}", flush=True)
        time.sleep(args.delay)

    merged = {row["id"]: row for row in records}
    output = {"captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "http_calls_to_model": 0,
              "ok": sum(1 for row in merged.values() if row["verdict"] == "OK"),
              "failed": sum(1 for row in merged.values() if row["verdict"] != "OK"),
              "records": list(merged.values())}
    report_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": output["ok"], "failed": output["failed"],
                      "report": str(report_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
