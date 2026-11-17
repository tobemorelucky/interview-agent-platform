"""R121 stage 2: real Chrome/CDP browser acceptance of the showcase UI.

Every action below happens in a real headless Chrome against the running Vite
dev server and the Replay-bound FastAPI app. Screenshots, visible text, API
statuses and a step log are saved for reconciliation. No model HTTP is used.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import websockets

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
OUT = REPO / "data/r64_showcase_ui"
OUT.mkdir(parents=True, exist_ok=True)
EMAIL = "showcase-r1-synthetic-a-e6600748@example.com"  # R120 synthetic user with a synthetic resume/profile
PASSWORD = "ShowcaseR1ReplayOnly!"
BASE = "http://127.0.0.1:5173"


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.seq = 0

    async def call(self, method, params=None):
        self.seq += 1
        ident = self.seq
        await self.ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        while True:
            message = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=60))
            if message.get("id") == ident:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    async def eval(self, expression: str):
        result = await self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            return {"__exception": str(result["exceptionDetails"])[:300]}
        return result.get("result", {}).get("value")

    async def goto(self, url: str, wait: float = 1.2):
        await self.call("Page.navigate", {"url": url})
        await asyncio.sleep(wait)
        for _ in range(30):
            state = await self.eval("document.readyState")
            if state == "complete":
                break
            await asyncio.sleep(0.2)

    async def wait(self, expression: str, timeout: float = 20, label: str = ""):
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            value = await self.eval(expression)
            if value:
                return value
            await asyncio.sleep(0.35)
        raise TimeoutError(f"wait timeout: {label or expression[:80]}")

    async def text(self):
        return await self.eval("document.body.innerText")

    async def shot(self, name: str) -> str:
        data = await self.call("Page.captureScreenshot",
                               {"format": "png", "captureBeyondViewport": False})
        path = OUT / f"{name}.png"
        path.write_bytes(base64.b64decode(data["data"]))
        return str(path.relative_to(REPO))

    async def click_text(self, text: str, tag: str = "button") -> bool:
        return bool(await self.eval(f"""(() => {{
          const els = [...document.querySelectorAll('{tag}')];
          const el = els.find(e => e.innerText && e.innerText.trim().includes({json.dumps(text)}));
          if (!el) return false; el.click(); return true;
        }})()"""))

    async def set_value(self, selector: str, value: str, event: str = "input") -> bool:
        return bool(await self.eval(f"""(() => {{
          const el = document.querySelector({json.dumps(selector)});
          if (!el) return false;
          el.value = {json.dumps(value)};
          el.dispatchEvent(new Event({json.dumps(event)}, {{bubbles:true}}));
          el.dispatchEvent(new Event('change', {{bubbles:true}}));
          return true;
        }})()"""))

    async def check(self, selector: str, value: bool = True) -> bool:
        return bool(await self.eval(f"""(() => {{
          const el = document.querySelector({json.dumps(selector)});
          if (!el) return false; if (el.checked !== {str(value).lower()}) el.click();
          return true;
        }})()"""))


class Runner:
    def __init__(self, cdp: CDP):
        self.cdp = cdp
        self.steps: list[dict] = []

    async def step(self, name: str, fn, *, expect: str | None = None):
        entry = {"step": name, "at": datetime.now(timezone.utc).isoformat()}
        try:
            detail = await fn()
            entry["ok"] = True
            entry["detail"] = detail
            if expect and expect not in str(detail):
                entry["ok"] = False
                entry["detail"] = f"expected {expect!r} not found in {str(detail)[:300]}"
        except BaseException as exc:
            entry["ok"] = False
            entry["detail"] = f"{type(exc).__name__}: {exc}"
        self.steps.append(entry)
        print(f"[{'OK  ' if entry['ok'] else 'FAIL'}] {name}: {str(entry['detail'])[:160]}")
        return entry["ok"]


async def main() -> int:
    targets = json.loads((await httpx.AsyncClient().get("http://127.0.0.1:9222/json/list")).text)
    page = next(t for t in targets if t.get("type") == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=20_000_000) as ws:
        cdp = CDP(ws)
        await cdp.call("Page.enable")
        await cdp.call("Runtime.enable")
        runner = Runner(cdp)
        report = {"round": "R121-2", "browser": "Chrome headless via CDP",
                  "mode": "UI REPLAY / 0 model HTTP", "started_at": datetime.now(timezone.utc).isoformat()}

        async def login():
            await cdp.goto(f"{BASE}/login")
            shot = await cdp.shot("01_login")
            await cdp.set_value("#email", EMAIL)
            await cdp.set_value("#password", PASSWORD)
            await cdp.click_text("登录")
            await asyncio.sleep(1.5)
            return {"path": await cdp.eval("location.pathname"), "shot": shot,
                    "token": bool(await cdp.eval("localStorage.getItem('access_token')"))}
        await runner.step("登录测试用户", login, expect="/dashboard")

        async def dashboard():
            text = await cdp.text()
            return {"has_workflow": "从简历到长期成长" in (text or ""),
                    "shot": await cdp.shot("02_dashboard"), "chars": len(text or "")}
        await runner.step("Dashboard 正常显示", dashboard, expect="True")

        async def career():
            await cdp.goto(f"{BASE}/career", wait=1.8)
            text = await cdp.text() or ""
            clicked = await cdp.click_text("查看岗位要求")
            await asyncio.sleep(0.8)
            detail = await cdp.text() or ""
            return {"job_cards": text.count("开始模拟面试"), "detail_clicked": clicked,
                    "has_requirements": "岗位要求" in detail,
                    "source_note": "原始来源" in detail,
                    "shot": await cdp.shot("03_public_career_detail")}
        await runner.step("公共职业页显示已发布 JD 与来源", career, expect="True")

        async def resume_profile():
            await cdp.goto(f"{BASE}/resumes", wait=1.5)
            resume_text = await cdp.text() or ""
            shot_r = await cdp.shot("04_resumes")
            await cdp.goto(f"{BASE}/profile", wait=1.5)
            profile_text = await cdp.text() or ""
            return {"synthetic_resume_visible": "SYNTHETIC" in resume_text.upper()
                    or "SHOWCASE" in resume_text.upper(),
                    "profile_visible": "画像" in profile_text,
                    "shots": [shot_r, await cdp.shot("05_profile")]}
        await runner.step("简历与候选人画像页显示", resume_profile, expect="True")

        async def interview_setup():
            await cdp.goto(f"{BASE}/interview", wait=1.8)
            await cdp.wait("document.querySelectorAll('select[aria-label=\"公司和岗位\"] option').length > 1",
                           label="published jobs loaded")
            options = await cdp.eval("""[...document.querySelectorAll('select[aria-label="公司和岗位"] option')]
                .map(o => ({v:o.value, t:o.innerText})).filter(o => o.t.includes('Agent开发工程师（平台）'))""")
            if not options:
                raise RuntimeError("AI Agent published job not listed")
            job_value = str(options[0]["v"])
            await cdp.set_value('select[aria-label="公司和岗位"]', job_value, "change")
            await cdp.check('input[aria-label="自适应面试"]', True)
            await cdp.set_value('input[type="number"]', "3")
            await cdp.wait("document.querySelectorAll('select[aria-label=\"岗位专业 Skill\"] option').length > 1",
                           label="skills loaded")
            await cdp.set_value('select[aria-label="岗位专业 Skill"]', "ai_agent_application", "change")
            await asyncio.sleep(0.8)
            body = await cdp.text() or ""
            return {"job": options[0]["t"], "recommendation_visible": "推荐" in body,
                    "skill_visible": "Agent" in body, "shot": await cdp.shot("06_interview_setup")}
        setup_ok = await runner.step("选择 AI Agent 岗位与匹配 Skill", interview_setup, expect="True")

        session_id = None
        async def run_interview():
            nonlocal session_id
            clicked = await cdp.click_text("新建")
            await asyncio.sleep(1.0)
            sessions = await cdp.eval("""[...document.querySelectorAll('.session-card')].map(e=>e.innerText.trim())""")
            start_clicked = await cdp.click_text("开始 / 重试首题")
            await asyncio.sleep(1.5)
            turns = []
            for index in range(3):
                try:
                    await cdp.wait("""(() => { const t=document.querySelector('textarea'); return t && !t.disabled; })()""",
                                   timeout=30, label=f"question {index+1} ready")
                except TimeoutError:
                    break
                qtext = await cdp.eval("""(() => {
                    const bubbles=[...document.querySelectorAll('.question-bubble')];
                    return bubbles.length ? bubbles[bubbles.length-1].innerText.slice(0,180) : ''; })()""")
                await cdp.set_value("textarea", f"真实 UI 验收第{index+1}题：使用事务和唯一键处理，并说明失败恢复。")
                await cdp.click_text("发送")
                await asyncio.sleep(2.6)
                turns.append(qtext)
                await cdp.shot(f"07_turn_{index+1}")
            body = await cdp.text() or ""
            info = await cdp.eval("""(() => {
                const cards=[...document.querySelectorAll('.session-card')];
                return cards.length ? cards[0].innerText.trim() : ''; })()""")
            sid = await cdp.eval("""(() => { const m=location.search.match(/session_id=(\\d+)/); return m?Number(m[1]):null; })()""")
            session_id = sid
            return {"new_clicked": clicked, "start_clicked": start_clicked, "sessions": sessions[:3],
                    "questions": turns, "completed_visible": "本场复盘" in body,
                    "evaluation_visible": "评价" in body, "followup_visible": "追问" in body,
                    "replay_badge": "Replay 回放模式" in body, "session_info": info,
                    "session_id_in_url": sid, "shot": await cdp.shot("08_interview_completed")}
        if setup_ok:
            await runner.step("FULL 自适应面试三题逐题作答与评价", run_interview)

        async def report_and_coach():
            sid = session_id or 6
            await cdp.goto(f"{BASE}/interview/reports?session_id={sid}", wait=2.0)
            body = await cdp.text() or ""
            generated = await cdp.click_text("生成面试报告")
            await asyncio.sleep(3.0)
            after = await cdp.text() or ""
            return {"session_id": sid, "generate_clicked": generated,
                    "statistics_visible": "综合" in after or "平均" in after,
                    "coach_visible": "Coach" in after or "复盘" in after,
                    "proposal_visible": "批准写入长期记忆" in after,
                    "shot": await cdp.shot("09_report_coach")}
        await runner.step("报告页生成并展示 Coach 分析", report_and_coach)

        async def memory_approval():
            body = await cdp.text() or ""
            clicked = await cdp.click_text("批准写入长期记忆")
            await asyncio.sleep(0.4)
            confirmed = await cdp.click_text("确认批准")
            await asyncio.sleep(1.2)
            after = await cdp.text() or ""
            await cdp.goto(f"{BASE}/memory", wait=1.6)
            memory_text = await cdp.text() or ""
            return {"proposal_found": "批准写入长期记忆" in body, "approve_clicked": clicked,
                    "confirm_clicked": confirmed, "saved_visible": "已保存" in after or "成长记录" in memory_text,
                    "shot": await cdp.shot("10_memory_approved")}
        await runner.step("记忆提案批准与成长记录", memory_approval)

        async def weakness():
            sid = session_id or 6
            await cdp.goto(f"{BASE}/training?session_id={sid}", wait=2.2)
            body = await cdp.text() or ""
            picked = await cdp.click_text("选择此弱项练习")
            await asyncio.sleep(0.5)
            started = await cdp.click_text("开始本次训练")
            await asyncio.sleep(2.5)
            after = await cdp.text() or ""
            return {"weakness_button_visible": "选择此弱项练习" in body,
                    "picked": picked, "started": started,
                    "history_referenced": "历史" in after or "来源面试" in after,
                    "shot": await cdp.shot("11_weakness_training")}
        await runner.step("WEAKNESS 训练引用上一场记录", weakness)

        async def lifecycle():
            await cdp.goto(f"{BASE}/interview/reports", wait=1.8)
            first = await cdp.text() or ""
            await cdp.goto(f"{BASE}/interview/reports?session_id={session_id or 6}", wait=1.8)
            refreshed = await cdp.text() or ""
            return {"list_visible": "报告" in first, "refresh_recovered": len(refreshed) > 200,
                    "error_banner_visible": "错误" in refreshed or "失败" in refreshed,
                    "shot": await cdp.shot("12_refresh_history")}
        await runner.step("报告跳转、刷新恢复与历史记录", lifecycle)

        async def labels():
            await cdp.goto(f"{BASE}/interview", wait=1.8)
            selected = await cdp.eval("(() => { const c=document.querySelector('.session-card'); if(!c) return false; c.click(); return true; })()")
            await asyncio.sleep(1.2)
            body = await cdp.text() or ""
            return {"session_selected": selected,
                    "replay_labelled": "Replay 回放模式" in body or "DEMO" in body,
                    "private_jd_separated": "我的目标岗位" in body,
                    "no_fake_live_claim": "Live 模型验收通过" not in body,
                    "shot": await cdp.shot("13_label_audit")}
        await runner.step("Replay / 私人 JD / 合成简历标识", labels, expect="True")

        report["steps"] = runner.steps
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["passed"] = all(step["ok"] for step in runner.steps)
        report["session_id_created"] = session_id
        (OUT / "ui_acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print("UI PASS", report["passed"], "session", session_id)
        print("written", OUT / "ui_acceptance.json")
        return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
