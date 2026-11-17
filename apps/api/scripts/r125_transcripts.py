"""Write the R125 conversation evidence from real sessions, with no model HTTP.

Usage (test database only):
    M1_TEST_DATABASE_URL=... DATABASE_URL=... python scripts/r125_transcripts.py

Every line below comes from a real ``MockInterviewService`` session: the question
text is produced by the scripted model, but the decision, the gate and the
persisted state are the product's own.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

# The snapshot's job_target_id points at user_job_targets; importing only the
# interview models would fail at the first flush with NoReferencedTableError.
import interview_api.modules.models  # noqa: F401,E402
import interview_api.modules.job_target.models  # noqa: F401,E402

import test_r125_conversation as cases  # noqa: E402

SCENARIOS = [
    ("AI Agent / RAG：回答很浅，先澄清再深入", cases.RAG_RESUME, cases.RAG_FACTS, cases.RAG_JD,
     "AI Agent 应用开发工程师", "ai_agent_application", cases.RAG_PLAN, cases.RAG_RULES, cases.RAG_ANSWERS),
    ("Java 后端：项目经历充分，结合项目深入后换题", cases.JAVA_RESUME, cases.JAVA_FACTS, cases.JAVA_JD,
     "Java 后端开发工程师", "java_backend", cases.JAVA_PLAN, cases.JAVA_RULES, cases.JAVA_ANSWERS),
    ("连续回答“不知道”：合理结束该知识点，不纠缠", cases.JAVA_RESUME, cases.JAVA_FACTS, cases.JAVA_JD,
     "Java 后端开发工程师", "java_backend", cases.UNKNOWN_PLAN, cases.UNKNOWN_RULES, cases.UNKNOWN_ANSWERS),
]


def render(title, result):
    lines = [f"### {title}", ""]
    made = result["main"].made
    for row in result["transcript"]:
        if row["role"] == "interviewer":
            tag = {"OPENING": "开场", "QUESTION": "新题", "FOLLOW_UP": "追问"}.get(row["kind"], row["kind"])
            lines.append(f"**面试官（{tag}）**：{row['text']}")
        elif row["role"] == "candidate":
            lines.append(f"**候选人**：{row['text']}")
        else:
            lines.append(f"> 会话结束：{row['text']}")
        lines.append("")
    lines.append("本轮内部决策（不展示给候选人）：")
    lines.append("")
    lines.append("| 轮次 | 策略 | 目标主题 | 题源 |")
    lines.append("| --- | --- | --- | --- |")
    for index, row in enumerate(made, 1):
        lines.append(f"| {index} | {row['strategy']} | {row['topic']} | {row['source']} |")
    lines.append("")
    return "\n".join(lines)


async def main():
    url = os.environ.get("M1_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("需要 M1_TEST_DATABASE_URL（专用测试库）")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    out = []
    try:
        for title, resume, facts, jd, job_title, skill, plan, rules, answers in SCENARIOS:
            result = await cases._run(factory, resume=resume, facts=facts, jd=jd, job_title=job_title,
                                      skill=skill, plan=plan, rules=rules, answers=answers)
            out.append(render(title, result))
            print(f"done: {title} ({len(result['transcript'])} rows)")
    finally:
        await engine.dispose()
    target = ROOT.parents[1] / "data" / "r125_transcripts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# R125 三段连续模拟面试（零模型 HTTP）\n\n" + "\n".join(out), encoding="utf-8")
    print("written:", target)


asyncio.run(main())
