"""Round 23 / L3 test fixture: build a realistic personal-data dependency graph.

Every personal-content column is seeded with a unique CANARY string. Tests can
then scan the whole database for that canary after a purge, which is a stronger
assertion than checking a handful of fields.

Insert order respects the schema's immutability triggers: facts/evidence are
inserted BEFORE `candidate_profiles.current_version_id` is set (otherwise the
`sealed_history` trigger rejects them), and reports/observations are created
only after the owning M4 session + snapshot exist.
"""
from __future__ import annotations

import re
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


def _typed(sql: str) -> str:
    """A bind reused across varchar and text columns makes asyncpg deduce
    inconsistent types. Force it to text (implicitly assignable to varchar),
    while leaving JSON casts alone. Word boundary keeps :c2/:cid intact."""
    holder = "\x00CJSON\x00"
    sql = sql.replace("CAST(:c AS json)", holder)
    sql = re.sub(r":c\b(?!\d|\w)", "CAST(:c AS text)", sql)
    return sql.replace(holder, "CAST(:c AS json)")


async def _one(db, sql, **p):
    return (await db.execute(text(_typed(sql)), p)).scalar()


async def seed_world(factory: async_sessionmaker, *, with_session: bool = True,
                     with_jd: bool = True, with_second_user: bool = True,
                     with_derived: bool = True, canary: str | None = None) -> dict:
    """Return ids of a full world: owner A, other user B, resume, profile,
    private JD, session + snapshot + evaluation + coach + memory + ledger."""
    tag = uuid4().hex[:10]
    C = canary or f"CANARY-R23-{tag}"
    CJD = f"JDCANARY-R23-{tag}"  # distinct marker: a JD purge must not erase a resume
    out = {"canary": C, "jd_canary": CJD, "tag": tag, "with_session": with_session,
           "with_derived": with_derived, "with_jd": with_jd}

    async with factory() as db:
        async def ins(sql, **p):
            return await _one(db, sql + " RETURNING id", **p)

        out["uid_a"] = await ins(
            "INSERT INTO users (email, password_hash, username, role) VALUES (:e,'x',:un,'USER')",
            e=f"r23-a-{tag}@example.invalid", un=f"r23a{tag}")
        out["uid_b"] = await ins(
            "INSERT INTO users (email, password_hash, username, role) VALUES (:e,'x',:un,'USER')",
            e=f"r23-b-{tag}@example.invalid", un=f"r23b{tag}") if with_second_user else None
        uid_b = out["uid_b"] or out["uid_a"]

        # Public company/job/JD (must never be touched by a personal purge)
        out["company"] = await ins(
            "INSERT INTO companies (canonical_name, normalized_name, status) VALUES (:n,:n2,'ACTIVE')",
            n=f"R23 公共公司 {tag}", n2=f"r23{tag}")
        out["job"] = await ins(
            "INSERT INTO jobs (company_id, canonical_title, normalized_title, status) "
            "VALUES (:cid,:n,:n2,'ACTIVE')", cid=out["company"],
            n=f"公共岗位 {tag}", n2=f"公共岗位 {tag}")
        out["jdv"] = await ins(
            "INSERT INTO job_description_versions (job_id, version_number, raw_text, content_fingerprint, publication_status) "
            "VALUES (:j,1,'公共 JD 原文，不含个人信息','pubhash','PUBLISHED')", j=out["job"])
        await ins("INSERT INTO job_requirements (job_description_version_id, statement) "
                  "VALUES (:j,'公共要求')", j=out["jdv"])

        # Owner A's resume + raw object key
        out["object_key"] = f"resumes/r23-{tag}.txt"
        out["resume"] = await ins(
            "INSERT INTO resumes (user_id, filename, storage_key, file_type, status, raw_text, content_hash) "
            "VALUES (:u,:f,:k,'txt','COMPLETED',:c,:h)",
            u=out["uid_a"], f=f"resume-{tag}.txt", k=out["object_key"], c=C, h=C + "-hash")
        out["resume_other"] = await ins(
            "INSERT INTO resumes (user_id, filename, storage_key, file_type, status, raw_text) "
            "VALUES (:u,'unrelated.txt','resumes/unrelated.txt','txt','COMPLETED','UNRELATED-NEVER-TOUCH')",
            u=out["uid_a"])
        out["resume_b"] = await ins(
            "INSERT INTO resumes (user_id, filename, storage_key, file_type, status, raw_text) "
            "VALUES (:u,'b.txt','resumes/b.txt','txt','COMPLETED',:c)",
            u=uid_b, c=f"B-CANNOT-TOUCH-{tag}")

        # Private JD of owner A
        if with_jd:
            out["target"] = await ins(
                "INSERT INTO user_job_targets (user_id, company_name, job_title, raw_text, source_type, "
                "content_fingerprint, structured_json, requirements_json, parse_status, parse_mode, version) "
                "VALUES (:u,:c,:c,:c,'USER_SUPPLIED',:h,CAST('{}' AS jsonb),CAST('[]' AS jsonb),'CONFIRMED','RULE',1)",
                u=out["uid_a"], c=CJD, h=CJD + "-jdhash")
        else:
            out["target"] = None

        # ── Derived personal data (only when requested) ──
        out["version"] = out["fact"] = out["evidence"] = out["profile"] = None
        out["match"] = out["cand"] = None
        if with_derived:
            out["evidence"] = await ins(
                "INSERT INTO resume_evidence (resume_id, evidence_key, section, start_offset, end_offset, excerpt, content_hash) "
                "VALUES (:r,:k,'工作经历',0,10,:c,:h)",
                r=out["resume"], k=C + "-key", c=C, h=C + "-ehash")
            await ins("INSERT INTO resume_reports (resume_id, user_id, summary_json, retrieval_queries_json, "
                      "retrieved_context_json, questions_json, suggestions_json) "
                      "VALUES (:r,:u,CAST(:c AS json),'[]','[]','[]','[]')",
                      r=out["resume"], u=out["uid_a"], c='{"text":"' + C + '"}')

            out["profile"] = await ins(
                "INSERT INTO candidate_profiles (user_id) VALUES (:u)", u=out["uid_a"])
            out["version"] = await ins(
                "INSERT INTO candidate_profile_versions (candidate_profile_id, version_number, source_resume_id, "
                "generation_key, unknowns, snapshot_json, producer, summary) "
                "VALUES (:p,1,:r,:k,'[]',CAST(:c AS json),'REPLAY',:c2)",
                p=out["profile"], r=out["resume"], k=C + "-gen",
                c='{"work_experience":[{"employer":"' + C + '"}]}', c2=C)
            # Insert facts BEFORE setting current_version_id (sealed_history).
            out["fact"] = await ins(
                "INSERT INTO candidate_facts (version_id, field_path, statement, provenance) "
                "VALUES (:v,'work_experience.0.employer',:c,'RESUME')", v=out["version"], c=C)
            await ins("INSERT INTO candidate_fact_evidence (fact_id, resume_evidence_id) VALUES (:f,:e)",
                      f=out["fact"], e=out["evidence"])
            await ins("INSERT INTO candidate_work_experiences (version_id, fact_id, employer, description, experience_type) "
                      "VALUES (:v,:f,:c,:c,'WORK')", v=out["version"], f=out["fact"], c=C)
            await ins("INSERT INTO candidate_user_skills (version_id, fact_id, name, evidence_state) VALUES (:v,:f,:c,'MENTIONED')",
                      v=out["version"], f=out["fact"], c=C)
            await ins("INSERT INTO candidate_profile_generations (resume_id, version_id, generation_key, status) "
                      "VALUES (:r,:v,:k,'COMPLETED')", r=out["resume"], v=out["version"], k=C + "-gen2")
            await ins("INSERT INTO user_confirmed_evidence (candidate_profile_id, user_id, evidence_key, statement, reason) "
                      "VALUES (:p,:u,:k,:c,:c)", p=out["profile"], u=out["uid_a"], k=C + "-uce", c=C)
            await db.execute(text("UPDATE candidate_profiles SET current_version_id=:v WHERE id=:p"),
                             dict(v=out["version"], p=out["profile"]))

            # Job matching lineage (run status READY so child inserts are legal)
            out["match"] = await ins(
                "INSERT INTO job_match_runs (user_id, candidate_profile_version_id, request_key, policy_version, "
                "prompt_version, input_snapshot_json, input_fingerprint, status, coverage_json, producer) "
                "VALUES (:u,:v,:k,'v1','v1',CAST(:c AS json),:h,'READY','{}','REPLAY')",
                u=out["uid_a"], v=out["version"], k=C + "-match", c='{"profile":"' + C + '"}', h=C + "-mhash")
            out["cand"] = await ins(
                "INSERT INTO job_match_candidates (run_id, company_id, job_id, job_description_version_id, input_json, "
                "eligibility_status, eligibility_reasons, unknown_constraints, evidence_matrix_json, ranking_components_json) "
                "VALUES (:m,:co,:j,:jd,CAST(:c AS json),'SATISFIED','[]','[]','{}','{}')",
                m=out["match"], co=out["company"], j=out["job"], jd=out["jdv"], c='{"note":"' + C + '"}')
            await ins("INSERT INTO job_match_recommendations (candidate_id, fit_band, analysis_json) "
                      "VALUES (:cid,'GOOD_MATCH',CAST(:j AS json))", cid=out["cand"], j='{"text":"' + C + '"}')

        # ── Session + frozen snapshot + all derived personal content ──
        out["session"] = out["snapshot"] = out["report"] = None
        if with_session:
            out["session"] = await ins(
                "INSERT INTO interview_sessions (user_id, resume_id, title, status, workflow_mode, memory_summary, "
                "target_position, interview_plan_json, planner_trace_json, completion_json) "
                "VALUES (:u,:r,:c,'COMPLETED','m4',:c,:c,CAST(:j AS json),'{}','{}')",
                u=out["uid_a"], r=out["resume"], c=C, j='{"plan":"' + C + '"}')
            version_id = out["version"] or 0
            jd_id = out["target"]
            out["snapshot"] = await ins(
                "INSERT INTO interview_context_snapshots (user_id, interview_session_id, candidate_profile_version_id, "
                "job_target_id, context_json, content_fingerprint) "
                "VALUES (:u,:s,:v,:t,CAST(:c AS json),:h)",
                u=out["uid_a"], s=out["session"], v=version_id, t=jd_id,
                c='{"profile":{"employer":"' + C + '"},"candidate_evidence":[{"excerpt":"' + C + '"}],'
                  '"requirements":[{"statement":"' + CJD + '"}]}', h=C + "-snaphash")
            await db.execute(text("UPDATE interview_sessions SET context_snapshot_id=:sn WHERE id=:s"),
                             dict(sn=out["snapshot"], s=out["session"]))
            out["message"] = await ins(
                "INSERT INTO interview_messages (session_id, role, content, metadata_json) "
                "VALUES (:s,'USER',:c,CAST(:j AS json))", s=out["session"], c=C,
                j='{"note":"' + C + '"}')
            out["question"] = await ins(
                "INSERT INTO interview_session_questions (session_id, question_index, question, standard_answer, "
                "source, answer_summary, evidence_json, candidate_refs_json) "
                "VALUES (:s,1,:c,:c,'DYNAMIC',:c,CAST(:j AS json),'{}')",
                s=out["session"], c=C, j='{"evidence":"' + C + '"}')
            out["answer"] = await ins(
                "INSERT INTO interview_answers (session_id, session_question_id, message_id, idempotency_key, status, "
                "result_events) VALUES (:s,:q,:m,:k,'COMPLETED',CAST(:j AS json))",
                s=out["session"], q=out["question"], m=out["message"], k=C + "-idem",
                j='[{"text":"' + C + '"}]')
            out["agent_run"] = await ins(
                "INSERT INTO experience_agent_runs (graph_name, graph_version, status) VALUES ('r23','1','COMPLETED')")
            out["evaluation"] = await ins(
                "INSERT INTO interview_evaluations (session_id, session_question_id, answer_id, artifact_json, "
                "technical_correctness, coverage, depth, relevance, communication, provider, model, prompt_version, "
                "structured_retry, token_usage_json, agent_run_id) "
                "VALUES (:s,:q,:a,CAST(:j AS json),4,4,4,4,4,'REPLAY','model','v1',0,'{}',:ar)",
                s=out["session"], q=out["question"], a=out["answer"], ar=out["agent_run"],
                j='{"summary":"' + C + '"}')
            out["report"] = await ins(
                "INSERT INTO final_interview_reports (user_id, session_id, context_snapshot_id, generation_number, "
                "generation_key, evaluation_fingerprint, generation_status, statistics_json, coach_analysis_json, producer, prompt_version) "
                "VALUES (:u,:s,:sn,1,:k,:h,'COMPLETED',CAST(:j AS json),CAST(:j AS json),'REPLAY','v1')",
                u=out["uid_a"], s=out["session"], sn=out["snapshot"], k=C + "-report",
                h=C + "-rfp", j='{"coach":"' + C + '"}')
            await ins(
                "INSERT INTO interview_performance_observations (user_id, session_id, report_id, observation_type, text, "
                "supporting_evaluation_ids, occurrence_count) VALUES (:u,:s,:r,'WEAKNESS',:c,CAST(:refs AS json),1)",
                u=out["uid_a"], s=out["session"], r=out["report"], c=C,
                refs='["' + str(out["evaluation"]) + '"]')
            await ins(
                "INSERT INTO memory_proposals (user_id, session_id, report_id, proposal_type, original_content, "
                "content_fingerprint, confidence, supporting_evaluation_ids, observation_ids) "
                "VALUES (:u,:s,:r,'EXPERIENCE',:c,:h,'SINGLE_EVALUATION','[]','[]')",
                u=out["uid_a"], s=out["session"], r=out["report"], c=C, h=C + "-mphash")

        # User-level aggregates (owner A only)
        await ins("INSERT INTO user_memory_items (user_id, memory_type, content, summary) VALUES (:u,'FACT',:c,:c)",
                  u=out["uid_a"], c=C)
        await ins("INSERT INTO capability_memories (user_id, topic, revision, revoked, exclusions, projection, input_fingerprint) "
                  "VALUES (:u,:c,1,false,'[]',CAST(:j AS json),:h)",
                  u=out["uid_a"], c=C, j='{"projection":"' + C + '"}', h=C + "-cmhash")
        await ins("INSERT INTO user_skill_profiles (user_id, skill_name, weakness_summary, strength_summary) "
                  "VALUES (:u,:c,:c,:c)", u=out["uid_a"], c=C)
        out["chat"] = await ins("INSERT INTO chat_sessions (user_id, title) VALUES (:u,:c)", u=out["uid_a"], c=C)
        await ins("INSERT INTO chat_messages (session_id, role, content) VALUES (:s,'USER',:c)",
                  s=out["chat"], c=C)
        if out["uid_b"]:
            await ins("INSERT INTO user_memory_items (user_id, memory_type, content) VALUES (:u,'FACT',:c)",
                      u=out["uid_b"], c=f"B-MEMORY-{tag}")

        # Audit + ledger
        await ins("INSERT INTO audit_logs (action, actor_user_id, resource_type, resource_id, before_json, after_json, metadata_json) "
                  "VALUES ('RESUME_CREATE',:u,'RESUME',:rid,CAST(:j AS json),CAST(:j AS json),'{}')",
                  u=out["uid_a"], rid=str(out["resume"]), j='{"raw_text":"' + C + '"}')
        if with_jd:
            await ins("INSERT INTO audit_logs (action, actor_user_id, resource_type, resource_id, before_json, after_json, metadata_json) "
                      "VALUES ('JD_CREATE',:u,'PRIVATE_JD',:rid,CAST(:j AS json),CAST(:j AS json),'{}')",
                      u=out["uid_a"], rid=str(out["target"]), j='{"raw_text":"' + CJD + '"}')
        await db.execute(text("INSERT INTO model_budget_owners (owner_key, user_id) VALUES (:k,:u) ON CONFLICT DO NOTHING"),
                         dict(k=f"user:{out['uid_a']}", u=out["uid_a"]))
        await db.execute(text("INSERT INTO model_admissions (call_key, owner_key, route, model, status, reserved_tokens, charged_tokens, expires_at) "
                  "VALUES (:k,:o,'resume','model','SETTLED',100,100, now() + interval '1 day')"),
                  dict(k=f"admission-{tag}", o=f"user:{out['uid_a']}"))

        await db.commit()
    return out
