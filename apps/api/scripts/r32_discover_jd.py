"""R32：从官方职位列表页发现更多岗位详情链接（0 次模型调用）。

只做「发现 + 去重」，不判断正文质量；正文由 `r32_capture_jd.py` 逐条渲染采集。
输出 JSONL：`id, company, title, source_url, discovered_from`。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE = REPO_ROOT / "data/incoming_20261116c"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def render(url: str, budget: int = 20000) -> str:
    with tempfile.TemporaryDirectory(prefix="r32disc_") as profile:
        completed = subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--user-data-dir={profile}", f"--virtual-time-budget={budget}",
             "--dump-dom", url], capture_output=True, timeout=90)
    return completed.stdout.decode("utf-8", errors="ignore")


def feishu_positions(html: str, base_url: str, company: str) -> list[dict]:
    """飞书招聘：详情链接形如 /campus/position/<id>/detail，标题在相邻的 title 属性里。"""
    found = {}
    for match in re.finditer(r'href="(/campus/position/(\d+)/detail)"[^>]*>(.{0,200}?)</a>', html):
        path, position_id = match.group(1), match.group(2)
        text = re.sub(r"<[^>]+>", "", match.group(3)).strip()
        found.setdefault(position_id, {"id": f"{company}_POS_{position_id}", "company": company,
                                       "title": text or position_id,
                                       "source_url": urljoin(base_url, path),
                                       "discovered_from": base_url})
    if not found:  # 有的模板把链接放在 JSON 里
        for match in re.finditer(r'position/(\d{15,25})/detail', html):
            position_id = match.group(1)
            found.setdefault(position_id, {"id": f"{company}_POS_{position_id}", "company": company,
                                           "title": position_id,
                                           "source_url": f"{base_url.split('/campus')[0]}/campus/position/{position_id}/detail",
                                           "discovered_from": base_url})
    return list(found.values())


def kylinos_positions(html: str, base_url: str) -> list[dict]:
    """麒麟软件：一个列表页含 20 个岗位，标题在 h4[title]，投递链接带 jobAdId。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for item in soup.select("li.content-item"):
        heading = item.select_one("h4[title]")
        title = (heading.get("title") if heading else "") or ""
        title = title.replace("「2027校招」", "").strip()
        link = item.find("a", href=re.compile(r"jobAdId="))
        if not title or not link:
            continue
        items.append({"id": f"KYLINOS_{re.sub(r'[^A-Za-z0-9]+', '_', title)}",
                      "company": "麒麟软件", "title": title,
                      "source_url": link["href"], "discovered_from": base_url})
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(BASE / "discovered_candidates.jsonl"))
    args = parser.parse_args()
    sources = [("沐瞳科技", "https://moonton.jobs.feishu.cn/campus/position/list",
                lambda html, url, company="沐瞳科技": feishu_positions(html, url, company)),
               ("蔚来", "https://nio.jobs.feishu.cn/campus/position/list",
                lambda html, url, company="蔚来": feishu_positions(html, url, company)),
               ("麒麟软件", "https://www.kylinos.cn/about/job/campusRecruitment/",
                lambda html, url, company="麒麟软件": kylinos_positions(html, url))]
    rows = []
    for company, url, handler in sources:
        html = render(url)
        found = handler(html, url)
        print(f"{company}: {len(found)} 个岗位  (dom {len(html)} bytes)")
        rows.extend(found)
    seen = set()
    unique = []
    for row in rows:
        key = row["source_url"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    out = Path(args.out)
    out.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in unique) + "\n",
                   encoding="utf-8")
    print(json.dumps({"candidates": len(unique), "output": str(out),
                      "discovered_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
