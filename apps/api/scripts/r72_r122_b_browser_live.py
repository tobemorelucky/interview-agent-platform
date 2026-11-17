"""R122 stage 2: Java-backend FULL Live driven through the real browser + real model.

Flow: login B -> dashboard -> public career (JD5 detail + source) -> resume/profile
-> interview setup (JD5, java_backend, FULL, question_count=3) -> create (auto
first question) -> answer 3 frozen replies -> observe evaluation/followup/complete
-> report page + Coach.  Every step screenshots the page and is reconciled with
the model_admissions ledger; the run ABORTS if HTTP exceeds 13 or tokens 90000.

Frozen replies are fixed strings held in this file only; they are typed into the
answer box and never passed as prompt parameters. The model sees only what a
real candidate would type.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import websockets

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))
sys.path.insert(0, str(API_ROOT / "tests"))

from interview_api.core.config import settings  # noqa: E402

SANDBOX = "interview_agent_showcase_r1_20261116"
BASE = "http://127.0.0.1:5173"
OUT = REPO / "data/r72_r122_b_live"
OUT.mkdir(parents=True, exist_ok=True)
PREP = json.loads((REPO / "data/r71_r122_prep_b/prep_b.json").read_text(encoding="utf-8"))
EMAIL, PASSWORD = PREP["email"], PREP["password"]
BUDGET = {"max_http": 13, "max_tokens": 90000}

# Three frozen replies: (1) fairly complete, (2) incomplete -> needs follow-up,
# (3) contains an explicit technical error. Fixed before any model call.
FROZEN = [
    "我用 Spring Boot 做订单服务，按领域拆成订单、库存和通知三个微服务。数据库用 MySQL，"
    "订单表按 user_id 分库分表，查询走覆盖索引避免回表。缓存用 Redis，热点 key 加随机过期时间防击穿，"
    "缓存和数据库的一致性用先更新数据库再删缓存，配合 binlog 订阅做最终一致对账。消息用 Kafka，"
    "生产者开启事务和 acks=all，消费者用幂等表按消息 ID 去重。"
    "缓存穿透我用空值缓存加布隆过滤器。分布式锁用 Redisson，看门狗续期避免死锁。",
    "我用过 Redis 缓存，就是把它放在数据库前面，查询的时候先读缓存，没有就读数据库再写回。"
    "具体一致性细节项目里是别人处理的，我主要负责调用。",
    "分布式事务我直接用 Redis 的 SETNX 就能保证强一致性，因为 Redis 是单线程的，"
    "所以只要抢到锁整个事务就是原子的，不需要什么两阶段提交或者补偿机制，MySQL 那边也不用管回滚。",
]


class CDP:
    def __init__(self, ws):
        self.ws, self.ident = ws, 0

    async def call(self, method, params=None):
        self.ident += 1
        ident = self.ident
        await self.ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=120))
            if msg.get("id") == ident:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method}: {msg['error']}")
                return msg.get("result", {})

    async def eval(self, expr):
        r = await self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        return r.get("result", {}).get("value")

    async def goto(self, url, wait=1.5):
        await self.call("Page.navigate", {"url": url})
        await asyncio.sleep(wait)
        for _ in range(20):
            if await self.eval("document.readyState") == "complete":
                break
            await asyncio.sleep(0.2)

    async def wait(self, expr, timeout=90, label=""):
        loop = asyncio.get_event_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            if await self.eval(expr):
                return True
            await asyncio.sleep(0.5)
        raise TimeoutError(f"wait timeout: {label or expr[:80]}")

    async def text(self):
        return await self.eval("document.body.innerText") or ""

    async def shot(self, name):
        result = await self.call("Page.captureScreenshot", {"format": "png"})
        data = result["data"]
        import base64
        p = OUT / f"{name}.png"
        p.write_bytes(base64.b64decode(data))
        return p.name

    async def click_text(self, text, tag="button"):
        return bool(await self.eval(f"""(() => {{
          const els=[...document.querySelectorAll('{tag}')];
          const el=els.find(e=>e.innerText && e.innerText.includes({json.dumps(text)}));
          if(el){{el.click();return true}} return false; }})()"""))

    async def set_value(self, selector, value, event="input"):
        return bool(await self.eval(f"""(() => {{
          const el=document.querySelector({json.dumps(selector)}); if(!el) return false;
          const setter=Object.getOwnPropertyDescriptor(el.constructor.prototype,'value').set;
          setter.call(el, {json.dumps(value)});
          el.dispatchEvent(new Event({json.dumps(event)}, {{bubbles:true}})); return true; }})()"""))


async def ledger_count(client):
    # Read-only via a tiny API-free psql-equivalent is not exposed; use the audit log
    # of model routes recorded by a dedicated counters endpoint is not available.
    # Instead count admissions via the API's no such endpoint -> use DB through a
    # short asyncpg connection here (read-only, no writes).
    import asyncpg
    conn = await asyncpg.connect("postgresql://interview_agent:interview_agent@localhost:5432/" + SANDBOX)
    n = await conn.fetchval("select count(*) from model_admissions")
    tok = await conn.fetchval("select coalesce(sum(coalesce(actual_tokens,charged_tokens,reserved_tokens,0)),0) from model_admissions")
    await conn.close()
    return n, tok


class Recorder:
    def __init__(self):
        self.steps = []

    async def step(self, name, fn):
        entry = {"name": name}
        try:
            entry["detail"] = await fn()
            entry["ok"] = True
        except Exception as e:
            entry["ok"] = False
            entry["error"] = f"{type(e).__name__}: {e}"[:300]
        self.steps.append(entry)
        print(f"[{'OK' if entry['ok'] else 'FAIL'}] {name}", flush=True)
        if not entry["ok"]:
            raise RuntimeError(entry["error"])
        return entry["detail"]


async def main() -> int:
    targets = json.loads((await httpx.AsyncClient().get("http://127.0.0.1:9222/json/list")).text)
    page = next(t for t in targets if t["type"] == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=20_000_000) as ws:
        return await _run(cdp=CDP(ws), rec=Recorder())


async def _run(cdp, rec) -> int:
    n0, t0 = await ledger_count(None)
    report = {"round": "R122-B", "mode": "Java FULL Live / real governed model / browser-driven",
              "email": EMAIL, "budget": BUDGET, "ledger_before": {"http": n0, "tokens": t0}}

    async def guard_budget():
        n, t = await ledger_count(None)
        report["ledger_now"] = {"http": n, "tokens": t}
        if n - n0 > BUDGET["max_http"] or t - t0 > BUDGET["max_tokens"]:
            raise RuntimeError(f"BUDGET EXCEEDED http={n-n0} tokens={t-t0}")
        return {"delta_http": n - n0, "delta_tokens": t - t0}

    async def login():
        await cdp.goto(f"{BASE}/login")
        await cdp.set_value("#email", EMAIL)
        await cdp.set_value("#password", PASSWORD)
        await cdp.click_text("登录")
        await cdp.wait("location.pathname.includes('dashboard')", timeout=25, label="login redirect")
        return {"path": await cdp.eval("location.pathname"), "shot": await cdp.shot("b01_login")}
    await rec.step("登录合成用户B", login)

    async def career():
        await cdp.goto(f"{BASE}/career", wait=2.0)
        body = await cdp.text()
        has = "后端开发" in body or "数据平台" in body
        await cdp.click_text("查看岗位要求")
        await asyncio.sleep(0.8)
        detail = await cdp.text()
        return {"jd_visible": has, "source_visible": "来源" in detail or "官方" in detail,
                "shot": await cdp.shot("b02_public_career")}
    await rec.step("公共职业页查看 JD5 与来源", career)

    async def resume_profile():
        await cdp.goto(f"{BASE}/resumes", wait=1.6)
        r = await cdp.text()
        await cdp.goto(f"{BASE}/profile", wait=1.6)
        p = await cdp.text()
        return {"resume_visible": "Java" in r or "Spring" in r or "合成" in r,
                "profile_visible": len(p) > 200, "shot": await cdp.shot("b03_resume_profile")}
    await rec.step("简历与候选人画像页", resume_profile)

    chosen = {}
    async def setup():
        await cdp.goto(f"{BASE}/interview", wait=2.0)
        await cdp.wait("document.querySelectorAll('select[aria-label=\"公司和岗位\"] option').length > 1",
                       timeout=25, label="jobs loaded")
        opts = await cdp.eval("""[...document.querySelectorAll('select[aria-label=\"公司和岗位\"] option')].map(e=>({v:e.value,t:e.innerText}))""")
        jd = next((o for o in opts if "后端开发" in o["t"] or "数据平台" in o["t"]), None)
        if not jd:
            raise RuntimeError("JD5 not in targets")
        await cdp.set_value('select[aria-label="公司和岗位"]', jd["v"], "change")
        await cdp.wait("document.querySelectorAll('select[aria-label=\"岗位专业 Skill\"] option').length > 1",
                       timeout=25, label="skills loaded")
        await cdp.set_value('input[type="number"]', "3")
        rec_js = await cdp.eval("""(() => { const s=document.querySelector('select[aria-label="岗位专业 Skill"]');
            return s ? s.value : ''; })()""")
        await asyncio.sleep(0.6)
        body = await cdp.text()
        chosen["job"] = jd["t"]
        chosen["auto_skill"] = rec_js
        if rec_js != "java_backend":
            await cdp.set_value('select[aria-label="岗位专业 Skill"]', "java_backend", "change")
        return {"job": jd["t"], "auto_recommended_skill": rec_js,
                "recommendation_visible": "推荐" in body, "shot": await cdp.shot("b04_setup")}
    det = await rec.step("选择 JD5 与 java_backend / FULL / 3题", setup)

    session_id = {"v": None}
    async def answer_flow():
        clicked = await cdp.click_text("新建")
        # front-end auto-starts first question; wait for question bubble + textarea enabled
        await cdp.wait("""(() => { const t=document.querySelector('textarea'); return t && !t.disabled; })()""",
                       timeout=120, label="first question ready")
        budget1 = await guard_budget()
        questions = []
        for i, frozen in enumerate(FROZEN):
            qtext = await cdp.eval("""(() => { const b=[...document.querySelectorAll('.question-bubble')];
                return b.length ? b[b.length-1].innerText.slice(0,200) : ''; })()""")
            ok = await cdp.set_value("textarea", frozen)
            if not ok:
                raise RuntimeError("answer box not found")
            await cdp.click_text("发送")
            questions.append({"question": qtext, "answer_len": len(frozen)})
            await cdp.shot(f"b05_turn_{i+1}")
            if i < len(FROZEN) - 1:
                # wait until a NEW question bubble appears (evaluation + next question done)
                await cdp.wait(f"""(() => document.querySelectorAll('.question-bubble').length >= {i+2})()""",
                               timeout=120, label=f"question {i+2} bubble")
            else:
                await cdp.wait("""document.body.innerText.includes('本场复盘') || document.body.innerText.includes('已完成')""",
                               timeout=120, label="session completed")
            await guard_budget()
        sid = await cdp.eval("""(() => { const m=location.search.match(/session_id=(\\d+)/); return m?Number(m[1]):null; })()""")
        session_id["v"] = sid
        body = await cdp.text()
        return {"new_clicked": clicked, "questions": questions, "session_id": sid,
                "completed_visible": "本场复盘" in body or "已完成" in body,
                "evaluation_visible": "评价" in body, "followup_visible": "追问" in body,
                "budget_after_answer": report["ledger_now"], "shot": await cdp.shot("b06_completed")}
    await rec.step("浏览器创建会话并回答三题（真实模型）", answer_flow)

    async def report_coach():
        sid = session_id["v"] or 0
        await cdp.goto(f"{BASE}/interview/reports?session_id={sid}", wait=2.2)
        body = await cdp.text()
        gen = await cdp.click_text("生成面试报告")
        await cdp.wait("""(() => { const t=document.body.innerText; return t.includes('综合')||t.includes('平均')||t.includes('复盘'); })()""",
                       timeout=120, label="coach report")
        after = await cdp.text()
        await guard_budget()
        return {"session_id": sid, "generated": gen, "stats_visible": "综合" in after or "平均" in after,
                "coach_visible": "Coach" in after or "复盘" in after,
                "shot": await cdp.shot("b07_report_coach")}
    await rec.step("报告页生成并展示 Coach", report_coach)

    n1, t1 = await ledger_count(None)
    report["steps"] = rec.steps
    report["chosen"] = chosen
    report["ledger_after"] = {"http": n1, "tokens": t1}
    report["consumed"] = {"http": n1 - n0, "tokens": t1 - t0}
    report["budget_ok"] = (n1 - n0 <= BUDGET["max_http"] and t1 - t0 <= BUDGET["max_tokens"])
    (OUT / "b_live.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"consumed": report["consumed"], "budget_ok": report["budget_ok"],
                      "session_id": session_id["v"], "steps_ok": all(s["ok"] for s in rec.steps)}, ensure_ascii=False), flush=True)
    return 0 if report["budget_ok"] and all(s["ok"] for s in rec.steps) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
