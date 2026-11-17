"""R121 stage 3 follow-up: zero-HTTP, row-level audit of the Live session.

The earlier in-run quote check used a missing question_id on the evaluation row
and therefore could not map a quote to the answer; this audit maps by the real
answer_id and checks the persisted artifacts instead.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import asyncpg

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "data/r68_r121_live"
DSN = "postgresql://interview_agent:interview_agent@localhost:5432/interview_agent_showcase_r1_20261116"
SESSION = 14


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


async def main() -> int:
    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch("""
            select q.id question_id, q.question, a.id answer_id, m.content answer,
                   e.id evaluation_id, e.artifact_json, e.technical_correctness, e.coverage,
                   e.depth, e.relevance, e.communication
              from interview_session_questions q
              join interview_answers a on a.session_question_id=q.id and a.session_id=q.session_id
              join interview_messages m on m.id=a.message_id
              join interview_evaluations e on e.answer_id=a.id and e.session_id=q.session_id
             where q.session_id=$1 order by q.question_index, q.id""", SESSION)
        report_rows = await conn.fetch("""
            select id, generation_status, producer, prompt_version, statistics_json, evaluation_fingerprint
              from final_interview_reports where session_id=$1 order by id""", SESSION)
        steps = await conn.fetch("""
            select s.id, s.step_name, s.status, s.prompt_name, s.prompt_version, s.model_name, s.token_usage_json
              from experience_agent_step_runs s
              join interview_agent_calls c on c.agent_run_id=s.agent_run_id
             where c.session_id=$1 order by s.id""", SESSION)
        admissions = await conn.fetch("select route, model, status, charged_tokens, actual_tokens "
                                      "from model_admissions order by created_at")
    finally:
        await conn.close()

    audit = []
    for row in rows:
        artifact = row["artifact_json"] or {}
        artifact = json.loads(artifact) if isinstance(artifact, str) else artifact
        answer = row["answer"] or ""
        quotes = list(artifact.get("answer_quotes") or [])
        for obs in artifact.get("knowledge_observations") or []:
            quotes.extend(obs.get("answer_quotes") or [])
        covered = artifact.get("covered_points") or []
        quote_hits = [{"quote": q[:100], "in_answer": norm(q) in norm(answer)} for q in quotes[:6]]
        audit.append({
            "question_id": row["question_id"], "question": row["question"][:160],
            "answer_id": row["answer_id"], "answer_chars": len(answer),
            "evaluation_id": row["evaluation_id"],
            "scores": {k: row[k] for k in ("technical_correctness", "coverage", "depth",
                                           "relevance", "communication")},
            "covered_points": covered,
            "covered_point_tokens": [{"point": point,
                                      "literal_overlap": bool(re.search(
                                          r"[一-龥A-Za-z]{4,}", point) and
                                          any(token in answer for token in
                                              re.findall(r"[一-龥A-Za-z]{4,}", point)[:3]))}
                                     for point in covered],
            "quotes": quote_hits,
            "observation_states": [o.get("state") for o in artifact.get("knowledge_observations") or []],
            "next_action": artifact.get("next_action_recommendation"),
        })

    result = {
        "session_id": SESSION, "http_calls_to_model": 0,
        "evaluations": audit,
        "all_quotes_from_answer": all(q["in_answer"] for e in audit for q in e["quotes"]),
        "report": [dict(r, statistics_json=json.loads(r["statistics_json"])
                       if isinstance(r["statistics_json"], str) else r["statistics_json"])
                   for r in report_rows],
        "steps": [dict(r) for r in steps],
        "admission_total": len(admissions),
        "admission_tokens": sum((r["actual_tokens"] if r["actual_tokens"] is not None
                                 else (r["charged_tokens"] or 0)) for r in admissions),
        "routes": sorted({r["route"] for r in admissions}),
        "models": sorted({r["model"] for r in admissions}),
    }
    (OUT / "live_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str),
                                         encoding="utf-8")
    for item in audit:
        print(f"eval {item['evaluation_id']} answer {item['answer_id']} "
              f"scores={item['scores']} quotes_ok={[q['in_answer'] for q in item['quotes']]}")
        print("  covered:", item["covered_points"])
        print("  overlap:", [c["literal_overlap"] for c in item["covered_point_tokens"]])
    print("all_quotes_from_answer", result["all_quotes_from_answer"])
    print("admissions", result["admission_total"], result["admission_tokens"], result["routes"])
    print("written", OUT / "live_audit.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
