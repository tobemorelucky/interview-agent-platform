"""R122 stage 3/4 audit: read-only per-hop facts for a Live session."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

import asyncpg  # noqa: E402

SANDBOX = "interview_agent_showcase_r1_20261116"
DSN = "postgresql://interview_agent:interview_agent@localhost:5432/" + SANDBOX


def load(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


async def audit(session_id: int) -> dict:
    c = await asyncpg.connect(DSN)
    out = {"session_id": session_id}
    s = await c.fetchrow("""select s.id,s.user_id,s.status,s.title,s.question_count,s.state_version,
        s.completion_json, (cs.context_json->'training'->>'mode') mode,
        (select count(*) from interview_evaluations e where e.session_id=s.id) evals
        from interview_sessions s join interview_context_snapshots cs on cs.id=s.context_snapshot_id
        where s.id=$1""", session_id)
    out["session"] = dict(s) if s else None

    rows = await c.fetch("""select q.id qid, q.question, q.job_requirement_id, q.question_kind,
        q.evaluation_json, a.id aid, m.content answer, e.id eid, e.artifact_json,
        e.technical_correctness, e.coverage, e.depth, e.relevance, e.communication, e.prompt_version
        from interview_session_questions q
        left join interview_answers a on a.session_question_id=q.id and a.session_id=q.session_id
        left join interview_messages m on m.id=a.message_id
        left join interview_evaluations e on e.answer_id=a.id and e.session_id=q.session_id
        where q.session_id=$1 order by q.id""", session_id)
    turns = []
    for r in rows:
        turns.append({"qid": r["qid"], "q": (r["question"] or "")[:200], "kind": r["question_kind"],
                      "req": r["job_requirement_id"], "aid": r["aid"], "answer": (r["answer"] or "")[:600],
                      "eid": r["eid"], "artifact": load(r["artifact_json"]),
                      "scores": {"correct": r["technical_correctness"], "coverage": r["coverage"],
                                 "depth": r["depth"], "relevance": r["relevance"], "comm": r["communication"]},
                      "prompt_version": r["prompt_version"]})
    out["turns"] = turns

    reps = await c.fetch("""select id,session_id,generation_status,producer,prompt_version,
        evaluation_fingerprint, statistics_json, coach_analysis_json
        from final_interview_reports where session_id=$1 order by id""", session_id)
    out["reports"] = [{"id": r["id"], "status": r["generation_status"], "producer": r["producer"],
                       "prompt_version": r["prompt_version"], "fp": r["evaluation_fingerprint"],
                       "statistics": load(r["statistics_json"]),
                       "coach": load(r["coach_analysis_json"])} for r in reps]

    uid = s["user_id"] if s else None
    if uid:
        out["memory"] = [{"topic": r["topic"], "revoked": r["revoked"], "revision": r["revision"],
                          "projection": load(r["projection"])}
                         for r in await c.fetch(
            "select topic,revoked,revision,projection from capability_memories where user_id=$1 order by topic", uid)]
    await c.close()
    return out


def collect_quotes(obj) -> list[str]:
    out = []
    if obj is None:
        return out
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        for v in obj.values():
            out += collect_quotes(v)
    elif isinstance(obj, list):
        for v in obj:
            out += collect_quotes(v)
    return out


def quote_provenance(turns: list[dict]) -> dict:
    results, all_ok = [], True
    for t in turns:
        ans = t["answer"] or ""
        art = t["artifact"] or {}
        # quotes may live under evidence/quotes/supporting_quotes; gather string leaves
        qkeys = {k: v for k, v in art.items() if "quote" in k.lower() or "evidence" in k.lower() or "support" in k.lower()}
        quotes = collect_quotes(qkeys)
        quotes = [q for q in quotes if isinstance(q, str) and len(q) >= 6]
        hits = [q[:50] for q in quotes if q in ans]
        ok = bool(quotes) and len(hits) == len(quotes)
        all_ok = all_ok and ok
        results.append({"eid": t["eid"], "quote_fields": list(qkeys), "quotes": len(quotes),
                        "hits": len(hits), "ok": ok, "samples": hits[:3],
                        "covered_points": art.get("covered_points")})
    return {"all_ok": all_ok, "per_turn": results}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("session_id", type=int)
    args = ap.parse_args()
    data = asyncio.run(audit(args.session_id))
    data["quote_provenance"] = quote_provenance(data["turns"]) if data.get("turns") else None
    out = REPO / f"data/r73_r122_audit/session_{args.session_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"status": data["session"]["status"] if data["session"] else None,
                      "evals": data["session"]["evals"] if data["session"] else None,
                      "reports": len(data["reports"]),
                      "quote_all_ok": data["quote_provenance"]["all_ok"] if data["quote_provenance"] else None,
                      "out": str(out)}, ensure_ascii=False))
