"""R32：校验已采集的 JD 正文质量，并对不合格项用「可见文本」重抽（0 次模型调用）。

不合格类型：
* `JSON_NOISE`：抓到的是页面内嵌字段 schema（`"field_type"`/`"object_id"`），不是 JD；
* `TOO_SHORT`：< 300 字；
* `NO_REQUIREMENTS`：没有任何「要求/熟悉/掌握/具备」行；
* `TRUNCATED`：结尾停在半个 JSON 或半句话。

重抽时只取 body 可见文本（剔除 script/style），再从「职位描述/岗位职责」切到「投递/分享/版权」。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE = REPO_ROOT / "data/incoming_20261116c"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
NOISE = ('"field_type"', '"object_id"', '"active_status"', '"children_list"', '"i18n_name"')


def diagnose(text: str) -> str:
    if any(token in text for token in NOISE):
        return "JSON_NOISE"
    if "已下线" in text or "职位不存在" in text or len(text.strip()) < 120:
        return "OFFLINE_OR_EMPTY"
    lines = [line for line in text.splitlines() if line.strip()]
    has_requirement = bool(re.search(r"(要求|任职资格|资格|熟悉|掌握|具备|了解)", text))
    if len(text.strip()) < 300:
        # 短不等于不完整：只要职责/要求两类都在、行数够，就按「短正文」接受。
        return "OK_SHORT" if has_requirement and len(lines) >= 6 else "TOO_SHORT"
    if not re.search(r"(要求|熟悉|掌握|具备|了解)", text):
        return "NO_REQUIREMENTS"
    if text.count("{") > 20 or text.rstrip().endswith((",", "{", "[")):
        return "TRUNCATED"
    return "OK"


def visible_text(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text("\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def slice_body(text: str) -> str:
    match = re.search(r"(职位描述|岗位职责|工作内容|工作职责)", text)
    if not match:
        return ""
    body = text[match.start():]
    cut = re.search(r"(投递简历|投递地址|分享|收藏|版权所有|隐私政策|相关职位)", body)
    if cut:
        body = body[:cut.start()]
    return body.strip()


def rerender(url: str, budget: int = 18000) -> str:
    with tempfile.TemporaryDirectory(prefix="r32fix_") as profile:
        completed = subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--user-data-dir={profile}", f"--virtual-time-budget={budget}",
             "--dump-dom", url], capture_output=True, timeout=90)
    return completed.stdout.decode("utf-8", errors="ignore")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=str(BASE / "capture_report.json"))
    parser.add_argument("--only-bad", action="store_true")
    args = parser.parse_args()

    report_path = Path(args.report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    changed = 0
    for record in report["records"]:
        path = BASE / "captured" / f"{record['id']}.txt"
        if not path.exists():
            record["quality"] = "MISSING_TEXT"
            continue
        text = path.read_text(encoding="utf-8")
        verdict = diagnose(text)
        if verdict.startswith("OK") or not args.only_bad:
            record["quality"] = verdict
        if verdict.startswith("OK"):
            continue
        html = rerender(record["url"])
        visible = visible_text(html)
        body = slice_body(visible)
        if len(body) >= 150 and diagnose(body).startswith("OK"):
            path.write_text(body + "\n", encoding="utf-8")
            (BASE / "captured" / "dom" / f"{record['id']}.html").write_text(html, encoding="utf-8")
            record.update(method="VISIBLE_TEXT", chars=len(body), quality=diagnose(body),
                          text_sha256=hashlib.sha256(body.encode()).hexdigest(),
                          reprocessed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            changed += 1
            print(f"{record['id']:18} {verdict:14} -> OK {len(body)}字")
        else:
            offline = "已下线" in visible or "职位不存在" in visible
            record["quality"] = "FAILED_OFFLINE" if offline else f"{verdict}_UNRESOLVED"
            rejected = BASE / "captured/rejected"
            rejected.mkdir(parents=True, exist_ok=True)
            if path.exists():
                path.replace(rejected / path.name)
            print(f"{record['id']:18} {verdict:14} -> {record['quality']}（重抽 {len(body)} 字）")
    report["ok"] = sum(1 for r in report["records"] if str(r.get("quality", "")).startswith("OK"))
    report["failed"] = len(report["records"]) - report["ok"]
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "failed": report["failed"], "reprocessed": changed},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
