"""R124 browser acceptance: fixed UI issues, zero model calls.

Drives a real Chrome over CDP. Uses the showcase account, never creates a match
run or an interview (both would call the model).
"""
import asyncio
import base64
import json
from pathlib import Path

import httpx
import websockets

BASE = "http://127.0.0.1:5173"
CDP = "http://127.0.0.1:9222"
OUT = Path(__file__).resolve().parents[3] / "data/r124_ui"
OUT.mkdir(parents=True, exist_ok=True)
EMAIL = "showcase-r123-user@example.com"
PASSWORD = "Showcase123!"


class Page:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def call(self, method, params=None):
        self.n += 1
        ident = self.n
        await self.ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=60))
            if msg.get("id") == ident:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    async def goto(self, path, wait=1.6):
        await self.call("Page.navigate", {"url": BASE + path})
        await asyncio.sleep(wait)

    async def eval(self, expr):
        result = await self.call("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True})
        return result.get("result", {}).get("value")

    async def text(self):
        return await self.eval("document.body.innerText") or ""

    async def wait_for(self, expr, seconds=12, label=""):
        deadline = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < deadline:
            if await self.eval(expr):
                return True
            await asyncio.sleep(0.3)
        raise AssertionError(f"timeout waiting for {label or expr}")

    async def shot(self, name):
        data = await self.call("Page.captureScreenshot", {"format": "png"})
        (OUT / name).write_bytes(base64.b64decode(data["data"]))

    async def viewport(self, width, height):
        await self.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 600})
        await asyncio.sleep(0.6)


async def login(page):
    await page.goto("/login")
    await page.eval("""(() => {
        const set = (el, v) => { const s = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value').set;
            s.call(el, v); el.dispatchEvent(new Event('input', {bubbles:true})); };
        set(document.querySelector('input[type=email]'), %s);
        set(document.querySelector('input[type=password]'), %s);
        document.querySelector('form').requestSubmit();
        return true; })()""" % (json.dumps(EMAIL), json.dumps(PASSWORD)))
    await asyncio.sleep(2.2)


async def main():
    targets = json.loads((await httpx.AsyncClient().get(f"{CDP}/json/list")).text)
    target = next(t for t in targets if t["type"] == "page")
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=40_000_000) as ws:
        page = Page(ws)
        await page.call("Page.enable")
        await page.call("Runtime.enable")
        report = {"steps": []}

        def rec(name, ok, detail=None):
            report["steps"].append({"name": name, "ok": bool(ok), "detail": detail})

        await login(page)
        rec("登录", "登录" not in await page.text() and await page.eval("!!document.querySelector('a[href=\"/dashboard\"], nav')"))

        # 1. Job library search
        await page.viewport(1440, 1000)
        await page.goto("/career", 2.0)
        body = await page.text()
        rec("岗位库打开", "共 50 个岗位" in body, body[:120])
        await page.eval("""(() => { const el = document.querySelector('input[type=search]');
            const s = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value').set;
            s.call(el, 'Agent'); el.dispatchEvent(new Event('input', {bubbles:true})); return true; })()""")
        await asyncio.sleep(1.6)
        body = await page.text()
        count = await page.eval("""(() => { const m = document.body.innerText.match(/共 (\\d+) 个岗位/); return m ? Number(m[1]) : -1; })()""")
        rec("搜索 Agent 覆盖全库", count == 27, f"total={count}")
        await page.shot("01_career_search_1440.png")

        # 2. Detail drawer with duty/qualification split
        await page.eval("document.querySelector('.job-card').click()")
        await asyncio.sleep(1.2)
        body = await page.text()
        rec("详情区分职责与要求", "岗位职责" in body and "任职要求" in body)
        rec("详情标注整理版", "招聘信息整理版" in body)
        await page.shot("02_career_detail_drawer.png")
        await page.eval("document.querySelector('.drawer .ghost').click()")

        # 3. Profile: grouped sections, evidence behind one drawer
        await page.goto("/profile", 2.2)
        body = await page.text()
        buttons = await page.eval("document.querySelectorAll('.profile-page button').length")
        rec("画像分组展示", "技术栈" in body and "教育背景" in body and "项目经历" in body, f"buttons={buttons}")
        rec("画像不再逐条铺开依据", "查看依据" not in body or body.count("查看依据") < 3)
        opened = await page.eval("""(() => { const b=[...document.querySelectorAll('.profile-page .link')][0];
            if(!b) return false; b.click(); return true; })()""")
        await asyncio.sleep(0.9)
        rec("来源抽屉可打开", opened and "简历中的原文依据" in (await page.text()))
        await page.shot("03_profile_grouped.png")

        # 4. Match page (existing run; no model call)
        await page.goto("/jobs/matches/2", 2.4)
        body = await page.text()
        rec("推荐页可打开", "本次推荐" in body)
        rec("推荐页解释排序依据", "排序依据" in body)
        rec("推荐页区分匹配与待确认", "为什么推荐" in body and "还需要确认" in body)
        await page.shot("04_match_cards.png")

        # 5. Training
        await page.goto("/training", 2.4)
        body = await page.text()
        rec("训练页定位为专项", "专项训练" in body and "完整模拟面试" in body)
        await page.shot("05_training.png")

        # 6. Interview: sidebar only history + wizard
        await page.goto("/interview", 2.4)
        body = await page.text()
        selects_in_sidebar = await page.eval("document.querySelectorAll('.session-sidebar select').length")
        rec("面试页左侧不再堆放配置", selects_in_sidebar == 0, f"selects={selects_in_sidebar}")
        await page.eval("""(() => { const b=[...document.querySelectorAll('button')].find(x=>x.innerText.includes('新建面试')); b && b.click(); return !!b; })()""")
        await asyncio.sleep(1.0)
        body = await page.text()
        rec("新建面试向导可打开", "新建模拟面试" in body and "考察侧重" in body and "快速体验 3 题" in body)
        await page.shot("06_interview_wizard.png")
        await page.eval("document.querySelector('.wizard .btn-ghost').click()")

        # 7. Responsive
        for width, height, name in ((1024, 900, "07_career_1024.png"), (390, 844, "08_career_390.png")):
            await page.viewport(width, height)
            await page.goto("/career", 1.8)
            overflow = await page.eval("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            rec(f"响应式 {width}px 无横向溢出", overflow <= 2, f"overflow={overflow}")
            await page.shot(name)
        await page.viewport(390, 844)
        await page.goto("/interview", 2.0)
        await page.shot("09_interview_390.png")

        report["all_ok"] = all(step["ok"] for step in report["steps"])
        (OUT / "ui_acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        for step in report["steps"]:
            print(("OK   " if step["ok"] else "FAIL ") + step["name"] + ("" if step["ok"] else f"  <- {step['detail']}"))
        print("ALL_OK", report["all_ok"])
        return 0 if report["all_ok"] else 2


raise SystemExit(asyncio.run(main()))
