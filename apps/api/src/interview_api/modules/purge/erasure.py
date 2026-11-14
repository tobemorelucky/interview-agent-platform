"""Round 23 / L3: declarative, resource-scoped personal-data erasure.

Why "erase then maybe delete": an `interview_context_snapshots` row is frozen
and RESTRICT-referenced by both `interview_sessions.context_snapshot_id` and
`final_interview_reports.context_snapshot_id`. It cannot be deleted without
breaking history. So every column that can hold resume/JD-derived text is
*overwritten* with a fixed marker first (irreversible, not a `deleted_at` flag),
and physical row deletion is attempted only when the resource has no history.
That makes FK order irrelevant for correctness: even a row we cannot delete no
longer holds any personal content.

Scope category map (see docs/102 section 2 for the full dependency graph):
  1 DELETE        rows reachable through the resource's own lineage
  2 REDACT        content columns of pinned skeletons + user-level aggregates
  3 KEEP          non-content audit: ids, FKs, timestamps, enum status, token counts
  4 EXTERNAL      backups, provider logs, capture/replay, historical Live DBs
  5 BLOCK         UNKNOWN model outcome -- we cannot prove what the provider saw

User-level aggregates (capability_memories, user_memory_items, chat_*) have NO
resource foreign key and can mix several resumes. They cannot be split safely,
so they are redacted by `user_id` -- the requesting user's own data only. This
never touches another user's rows, and the UI/scope_json state this explicitly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from .constants import ERASED_HASH, ERASED_JSON, ERASED_TEXT, PRIVATE_JD, RESUME

# kind: text | hash | json_obj | json_arr
# Only content-bearing columns are listed. Enum statuses, foreign keys,
# timestamps and token counts are deliberately kept (category 3).
ERASE_PLAN: dict[str, list[tuple[str, str]]] = {
    "resumes": [("raw_text", "text"), ("filename", "text"), ("storage_key", "text"),
                ("content_hash", "hash"), ("error_message", "text"), ("task_id", "text"),
                ("processing_stage", "text"), ("stage_message", "text")],
    "user_job_targets": [("company_name", "text"), ("job_title", "text"), ("raw_text", "text"),
                         ("source_reference", "text"), ("content_fingerprint", "hash"),
                         ("structured_json", "json_obj"), ("requirements_json", "json_arr"),
                         ("parse_error", "text")],
    "resume_evidence": [("evidence_key", "hash"), ("section", "text"),
                        ("excerpt", "text"), ("content_hash", "hash")],
    "resume_reports": [("summary_json", "json_obj"), ("retrieval_queries_json", "json_arr"),
                       ("retrieved_context_json", "json_arr"), ("questions_json", "json_arr"),
                       ("suggestions_json", "json_arr")],
    "candidate_profile_versions": [("generation_key", "hash"), ("summary", "text"),
                                   ("unknowns", "json_arr"), ("snapshot_json", "json_obj")],
    "candidate_facts": [("field_path", "text"), ("statement", "text"), ("provenance", "text")],
    "candidate_education_experiences": [("institution", "text"), ("description", "text")],
    "candidate_work_experiences": [("employer", "text"), ("description", "text"),
                                   ("experience_type", "text")],
    "candidate_project_experiences": [("name", "text"), ("description", "text")],
    "candidate_user_skills": [("name", "text")],
    "candidate_profile_corrections": [("field_path", "text"), ("old_value", "text"),
                                      ("new_value", "text"), ("changed_by", "text"),
                                      ("reason", "text")],
    "candidate_profile_generations": [("generation_key", "hash"), ("error", "text")],
    "user_confirmed_evidence": [("evidence_key", "hash"), ("statement", "text"), ("reason", "text")],
    "interview_context_snapshots": [("context_json", "json_obj"), ("content_fingerprint", "hash")],
    "interview_sessions": [("title", "text"), ("memory_summary", "text"),
                           ("question_generation_error", "text"),
                           ("interview_plan_json", "json_obj"), ("planner_trace_json", "json_obj"),
                           ("completion_json", "json_obj"), ("target_position", "text")],
    "interview_messages": [("content", "text"), ("metadata_json", "json_obj")],
    "interview_answers": [("error", "text"), ("result_events", "json_arr")],
    "interview_session_questions": [("question", "text"), ("standard_answer", "text"),
                                    ("dimension", "text"), ("answer_summary", "text"),
                                    ("source_fingerprint", "hash"),
                                    ("evidence_json", "json_obj"), ("missing_points_json", "json_arr"),
                                    ("evaluation_json", "json_obj"), ("candidate_refs_json", "json_obj")],
    "interview_evaluations": [("artifact_json", "json_obj"), ("token_usage_json", "json_obj")],
    "final_interview_reports": [("generation_key", "hash"), ("evaluation_fingerprint", "hash"),
                                ("statistics_json", "json_obj"), ("coach_analysis_json", "json_obj"),
                                ("error", "text")],
    "interview_performance_observations": [("text", "text")],
    "memory_proposals": [("original_content", "text"), ("corrected_content", "text"),
                         ("content_fingerprint", "hash"), ("confirmation_reason", "text")],
    "job_match_runs": [("request_key", "hash"), ("input_snapshot_json", "json_obj"),
                       ("input_fingerprint", "hash"), ("coverage_json", "json_obj"), ("error", "text")],
    "job_match_candidates": [("input_json", "json_obj"), ("eligibility_reasons", "json_arr"),
                             ("unknown_constraints", "json_arr"), ("evidence_matrix_json", "json_obj"),
                             ("ranking_components_json", "json_obj")],
    "job_match_recommendations": [("analysis_json", "json_obj")],
    # Audit logs: keep action/resource/time/ip skeleton, redact payloads.
    "audit_logs": [("before_json", "json_obj"), ("after_json", "json_obj"),
                   ("metadata_json", "json_obj"), ("error_message", "text")],
}

# Category 2, user-level aggregates. Scoped strictly by the OWNER's user_id.
USER_LEVEL_PLAN: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "capability_memories": ("user_id = :uid", [
        ("topic", "text"), ("exclusions", "text"), ("correction_note", "text"),
        ("projection", "json_obj"), ("input_fingerprint", "hash")]),
    "capability_memory_versions": (
        "memory_id IN (SELECT id FROM capability_memories WHERE user_id = :uid)", [
            ("previous_state", "json_obj"), ("projection", "json_obj"), ("actor", "text")]),
    "user_memory_items": ("user_id = :uid", [
        ("key", "text"), ("content", "text"), ("summary", "text"), ("metadata_json", "json_obj")]),
    "user_memory_events": ("user_id = :uid", [
        ("before_json", "json_obj"), ("after_json", "json_obj"), ("reason", "text")]),
    "user_skill_profiles": ("user_id = :uid", [
        ("skill_name", "text"), ("weakness_summary", "text"), ("strength_summary", "text"),
        ("metadata_json", "json_obj")]),
    "chat_sessions": ("user_id = :uid", [("title", "text")]),
    "chat_messages": ("session_id IN (SELECT id FROM chat_sessions WHERE user_id = :uid)", [
        ("content", "text"), ("citations_json", "json_arr")]),
}

# How each resource-lineage table's rows are located. Never `user_id` alone.
ROW_SCOPES: dict[str, str] = {
    "resumes": "id = :resume_rid AND user_id = :uid",
    "user_job_targets": "id = :target_rid AND user_id = :uid",
    "resume_evidence": "resume_id = :resume_rid",
    "resume_reports": "resume_id = :resume_rid",
    "candidate_profile_versions": "id IN :version_ids",
    "candidate_facts": "version_id IN :version_ids",
    "candidate_education_experiences": "version_id IN :version_ids",
    "candidate_work_experiences": "version_id IN :version_ids",
    "candidate_project_experiences": "version_id IN :version_ids",
    "candidate_user_skills": "version_id IN :version_ids",
    "candidate_fact_evidence": "fact_id IN :fact_ids",
    "candidate_profile_corrections": "base_version_id IN :version_ids",
    "candidate_profile_generations": "id IN :generation_ids",
    "user_confirmed_evidence": "candidate_profile_id IN :profile_ids",
    "interview_context_snapshots": "id IN :snapshot_ids",
    "interview_sessions": "id IN :session_ids",
    "interview_messages": "session_id IN :session_ids",
    "interview_answers": "session_id IN :session_ids",
    "interview_session_questions": "session_id IN :session_ids",
    "interview_evaluations": "session_id IN :session_ids",
    "final_interview_reports": "session_id IN :session_ids",
    "interview_performance_observations": "session_id IN :session_ids",
    "memory_proposals": "session_id IN :session_ids",
    "job_match_runs": "id IN :match_ids",
    "job_match_candidates": "run_id IN :match_ids",
    "job_match_recommendations": "candidate_id IN :candidate_ids",
    "audit_logs": "id IN :audit_ids",
}

# Category 3: preserved, no personal content; counts recorded for the verifier.
KEEP_AUDIT_TABLES = ("model_admissions", "model_budget_owners", "interview_agent_calls")
# Category 4: outside this service's immediate control. Reported, not blocked.
EXTERNAL_BOUNDARIES = (
    "postgresql_physical_backups_and_wal",
    "minio_object_versioning_or_replication",
    "external_model_provider_prompt_logs",
    "local_capture_replay_and_log_files",
    "historical_live_databases",
)
# Public knowledge pipeline: source_snapshots/claims/tool_invocations describe
# public JD research, not a private resume/JD. Verified to have no user FK.
PUBLIC_PIPELINE_TABLES = ("claims", "claim_evidence", "tool_invocations",
                          "experience_source_items", "experience_agent_runs",
                          "experience_agent_step_runs", "source_snapshots")

DELETE_ORDER = [
    ("job_match_recommendations", "candidate_id IN :candidate_ids"),
    ("job_match_attempts", "run_id IN :match_ids"),
    ("job_match_candidates", "run_id IN :match_ids"),
    ("job_match_runs", "id IN :match_ids"),
    ("candidate_fact_evidence", "fact_id IN :fact_ids"),
    ("candidate_education_experiences", "version_id IN :version_ids"),
    ("candidate_work_experiences", "version_id IN :version_ids"),
    ("candidate_project_experiences", "version_id IN :version_ids"),
    ("candidate_user_skills", "version_id IN :version_ids"),
    ("candidate_facts", "version_id IN :version_ids"),
    ("candidate_profile_corrections", "base_version_id IN :version_ids"),
    ("candidate_profile_generations", "id IN :generation_ids"),
    ("user_confirmed_evidence", "candidate_profile_id IN :profile_ids"),
    ("resume_evidence", "resume_id = :resume_rid"),
    ("resume_reports", "resume_id = :resume_rid"),
]
# Only deletable with no history: versions are RESTRICT-referenced by snapshots.
HARD_DELETE_TAIL = [("candidate_profile_versions", "id IN :version_ids")]

EXPANDING = ("version_ids", "fact_ids", "generation_ids", "snapshot_ids", "session_ids",
             "match_ids", "candidate_ids", "profile_ids", "audit_ids")

# ── Round 23 decisive constraint ────────────────────────────────────────────
# The schema protects history with database triggers, not just FKs:
#   immutable_history            BEFORE UPDATE OR DELETE  (candidate_* / evidence)
#   m4_snapshot_immutable        BEFORE UPDATE            (interview_context_snapshots)
#   m5_report_guard / m5_observation_guard / m5_proposal_guard
#   job_match_run_guard / job_match_candidates_guard / recommendations_guard
# They raise unconditionally. A resource purge must NOT disable or rewrite
# these triggers: that would break the immutability guarantee the ledger and
# historical reports rely on. Instead, detect their presence BEFORE any write
# and BLOCK with a precise, non-sensitive diagnosis. The agreed resolution is
# a future, separately approved migration that adds a narrow purge exception.
IMMUTABLE_DEPENDENCIES: dict[str, tuple[str, str]] = {
    # table: (scope expression, trigger that pins it)
    "resume_evidence": ("resume_id = :resume_rid", "immutable_history"),
    "candidate_profile_versions": ("id IN :version_ids", "immutable_history"),
    "candidate_facts": ("version_id IN :version_ids", "immutable_history"),
    "candidate_education_experiences": ("version_id IN :version_ids", "immutable_history"),
    "candidate_work_experiences": ("version_id IN :version_ids", "immutable_history"),
    "candidate_project_experiences": ("version_id IN :version_ids", "immutable_history"),
    "candidate_user_skills": ("version_id IN :version_ids", "immutable_history"),
    "candidate_fact_evidence": ("fact_id IN :fact_ids", "immutable_history"),
    "candidate_profile_corrections": ("base_version_id IN :version_ids", "immutable_history"),
    "user_confirmed_evidence": ("candidate_profile_id IN :profile_ids", "immutable_history"),
    "interview_context_snapshots": ("id IN :snapshot_ids", "m4_snapshot_immutable"),
    "final_interview_reports": ("session_id IN :session_ids", "m5_report_guard"),
    "interview_performance_observations": ("session_id IN :session_ids", "m5_observation_guard"),
    "memory_proposals": ("session_id IN :session_ids", "m5_proposal_guard"),
    "job_match_runs": ("id IN :match_ids", "job_match_run_guard"),
    "job_match_candidates": ("run_id IN :match_ids", "job_match_candidates_guard"),
    "job_match_recommendations": ("candidate_id IN :candidate_ids", "job_match_recommendations_guard"),
}

# Category 5: user-level aggregates have no resource FK, so they cannot be
# attributed to ONE resume/JD. Erasing them would damage the same user's other
# materials. Their presence blocks a success claim (needs L4 / attribution work).
ATTRIBUTION_DEPENDENCIES: dict[str, str] = {
    "capability_memories": "user_id = :uid",
    "capability_memory_versions": "memory_id IN (SELECT id FROM capability_memories WHERE user_id = :uid)",
    "user_memory_items": "user_id = :uid",
    "user_memory_events": "user_id = :uid",
    "user_skill_profiles": "user_id = :uid",
    "chat_messages": "session_id IN (SELECT id FROM chat_sessions WHERE user_id = :uid)",
    "chat_sessions": "user_id = :uid",
}


async def preflight_blockers(db: AsyncSession, scope: dict) -> dict:
    """Read-only. Returns exactly which pinned rows would prevent an honest
    resource-level purge. Called BEFORE any mutation, so BLOCKED is fully
    recoverable (no partial erase, nothing to roll back)."""
    params = _params(scope)
    immutable: dict[str, int] = {}
    triggers: dict[str, str] = {}
    for table, (where, trigger) in IMMUTABLE_DEPENDENCIES.items():
        sql = f"SELECT count(*) FROM {table} WHERE {where}"
        n = await db.scalar(_expand(text(sql), sql), params)
        if n:
            immutable[table] = int(n)
            triggers[table] = trigger
    attribution: dict[str, int] = {}
    for table, where in ATTRIBUTION_DEPENDENCIES.items():
        n = await db.scalar(text(f"SELECT count(*) FROM {table} WHERE {where}"), dict(uid=scope["uid"]))
        if n:
            attribution[table] = int(n)
    return dict(immutable=immutable, triggers=triggers, attribution=attribution,
                blocked=bool(immutable or attribution))


def _params(scope: dict) -> dict:
    p = dict(scope)
    # Older persisted scopes (before the typed-rid fix) still work.
    if "resume_rid" not in p:
        p["resume_rid"] = p["rid"] if p.get("resource_type") == RESUME else -1
    if "target_rid" not in p:
        p["target_rid"] = p["rid"] if p.get("resource_type") == PRIVATE_JD else -1
    for k in EXPANDING:
        if not p.get(k):
            p[k] = [-1]
    return p


def _expand(stmt, sql: str):
    for k in EXPANDING:
        if f"IN :{k}" in sql:
            stmt = stmt.bindparams(bindparam(k, expanding=True))
    return stmt


async def _ids(db: AsyncSession, sql: str, **params) -> list[int]:
    stmt = text(sql)
    for k, v in params.items():
        if isinstance(v, list):
            stmt = stmt.bindparams(bindparam(k, expanding=True))
    return [r[0] for r in await db.execute(stmt, params)]


async def resolve_scope(db: AsyncSession, uid: int, resource_type: str, rid: int) -> dict:
    """Resolve the exact lineage of ONE resource. No user-wide scans."""
    # A resource id is only meaningful WITHIN its own type. Using a JD id as
    # a resume_id (or vice versa) would scope/delete another resource's rows.
    resume_rid = rid if resource_type == RESUME else -1
    target_rid = rid if resource_type == PRIVATE_JD else -1
    s: dict = dict(uid=uid, rid=rid, resume_rid=resume_rid, target_rid=target_rid,
                   resource_type=resource_type,
                   version_ids=[], fact_ids=[], generation_ids=[], profile_ids=[],
                   snapshot_ids=[], session_ids=[], match_ids=[], candidate_ids=[],
                   audit_ids=[], object_key=None, strategy="HARD_DELETE",
                   audit_keep={}, user_level_counts={}, external_boundaries=list(EXTERNAL_BOUNDARIES),
                   public_pipeline_untouched=list(PUBLIC_PIPELINE_TABLES))
    if resource_type == RESUME:
        row = (await db.execute(text(
            "SELECT storage_key FROM resumes WHERE id = :rid AND user_id = :uid"),
            dict(rid=rid, uid=uid))).first()
        s["object_key"] = row[0] if row else None
        s["version_ids"] = await _ids(db,
            "SELECT id FROM candidate_profile_versions WHERE source_resume_id = :rid", rid=rid)
        vids = s["version_ids"] or [-1]
        s["fact_ids"] = await _ids(db, "SELECT id FROM candidate_facts WHERE version_id IN :v", v=vids)
        s["generation_ids"] = await _ids(db,
            "SELECT id FROM candidate_profile_generations WHERE resume_id = :rid OR version_id IN :v",
            rid=rid, v=vids)
        s["match_ids"] = await _ids(db,
            "SELECT id FROM job_match_runs WHERE candidate_profile_version_id IN :v", v=vids)
        s["profile_ids"] = await _ids(db,
            "SELECT id FROM candidate_profiles WHERE user_id = :uid", uid=uid)
    mids = s["match_ids"] or [-1]
    s["candidate_ids"] = await _ids(db,
        "SELECT id FROM job_match_candidates WHERE run_id IN :m", m=mids)
    if resource_type == RESUME:
        vids = s["version_ids"] or [-1]
        s["snapshot_ids"] = await _ids(db,
            "SELECT id FROM interview_context_snapshots WHERE candidate_profile_version_id IN :v", v=vids)
    else:
        s["snapshot_ids"] = await _ids(db,
            "SELECT id FROM interview_context_snapshots WHERE job_target_id = :rid", rid=rid)
    snids = s["snapshot_ids"] or [-1]
    snap_sessions = await _ids(db,
        "SELECT interview_session_id FROM interview_context_snapshots WHERE id IN :s", s=snids)
    direct = await _ids(db, "SELECT id FROM interview_sessions WHERE resume_id = :rid",
                        rid=rid) if resource_type == RESUME else []
    s["session_ids"] = sorted({*snap_sessions, *direct})
    s["audit_ids"] = await _ids(db,
        "SELECT id FROM audit_logs WHERE actor_user_id = :uid AND resource_id = :ridstr "
        "AND resource_type = :rt", uid=uid, ridstr=str(rid),
        rt="RESUME" if resource_type == RESUME else "PRIVATE_JD")
    for t in KEEP_AUDIT_TABLES:
        where = ("owner_key IN (SELECT owner_key FROM model_budget_owners WHERE user_id = :uid)"
                 if t == "model_admissions" else
                 "user_id = :uid" if t == "model_budget_owners" else
                 "session_id IN (SELECT id FROM interview_sessions WHERE user_id = :uid)")
        n = await db.scalar(text(f"SELECT count(*) FROM {t} WHERE {where}"), dict(uid=uid))
        s["audit_keep"][t] = int(n or 0)
    for t, (where, _) in USER_LEVEL_PLAN.items():
        n = await db.scalar(text(f"SELECT count(*) FROM {t} WHERE {where}"), dict(uid=uid))
        s["user_level_counts"][t] = int(n or 0)
    s["strategy"] = "ERASE_KEEP_SKELETON" if (s["snapshot_ids"] or s["session_ids"]) else "HARD_DELETE"
    return s


async def _json_types(db: AsyncSession) -> dict[str, str]:
    rows = await db.execute(text("""
        SELECT table_name || '.' || column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND data_type IN ('json', 'jsonb')
    """))
    return {r[0]: r[1] for r in rows}


def _value(kind: str):
    if kind == "hash":
        return ERASED_HASH, False
    if kind == "json_obj":
        return json.dumps(ERASED_JSON), True
    if kind == "json_arr":
        return json.dumps([]), True
    return ERASED_TEXT, False


async def erase_content(db: AsyncSession, scope: dict) -> dict:
    """Overwrite every in-scope personal column. Idempotent, FK-safe."""
    jtypes = await _json_types(db)
    params = _params(scope)
    touched: dict[str, int] = {}

    async def apply(table: str, columns, where: str, extra: dict) -> int:
        assignments, local = [], {}
        for col, kind in columns:
            key = f"v_{col}"
            actual = jtypes.get(f"{table}.{col}")
            # The database column type wins over the declarative hint: a JSON
            # column must receive valid JSON even if the plan labelled it text.
            if actual in ("json", "jsonb"):
                value = json.dumps([] if kind == "json_arr" else ERASED_JSON)
                assignments.append(f"{col} = CAST(:{key} AS {actual})")
            else:
                value = ERASED_HASH if kind == "hash" else ERASED_TEXT
                assignments.append(f"{col} = :{key}")
            local[key] = value
        sql = f"UPDATE {table} SET {', '.join(assignments)} WHERE {where}"
        result = await db.execute(_expand(text(sql), sql), {**params, **extra, **local})
        return result.rowcount

    for table, columns in ERASE_PLAN.items():
        touched[table] = await apply(table, columns, ROW_SCOPES[table], {})
    # User-level aggregates: owner's rows only. Explicitly recorded in scope.
    for table, (where, columns) in USER_LEVEL_PLAN.items():
        touched[table] = await apply(table, columns, where, {})
    return touched


async def delete_derivatives(db: AsyncSession, scope: dict, *, include_versions: bool) -> dict:
    """Delete children in RESTRICT-safe order. Tolerated failures leave only
    content-free skeletons (erase_content already ran)."""
    params = _params(scope)
    deleted: dict[str, int] = {}
    for table, where in DELETE_ORDER + (HARD_DELETE_TAIL if include_versions else []):
        sql = f"DELETE FROM {table} WHERE {where}"
        try:
            result = await db.execute(_expand(text(sql), sql), params)
            deleted[table] = result.rowcount
        except Exception:
            deleted[table] = -1
    return deleted


async def delete_resource_row(db: AsyncSession, scope: dict, resource_type: str) -> int:
    """Only for HARD_DELETE (no history). `interview_sessions.resume_id` is SET
    NULL by its FK; `candidate_profiles.current_version_id` is cleared first."""
    params = _params(scope)
    await db.execute(_expand(text(
        "UPDATE candidate_profiles SET current_version_id = NULL "
        "WHERE user_id = :uid AND current_version_id IN :version_ids"),
        "current_version_id IN :version_ids"), params)
    table = "resumes" if resource_type == RESUME else "user_job_targets"
    return (await db.execute(text(f"DELETE FROM {table} WHERE id = :rid AND user_id = :uid"),
                             {**params, "rid": params["resume_rid"] if resource_type == RESUME
                              else params["target_rid"]})).rowcount


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
