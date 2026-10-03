"""Pinned interview context, independent answers/evaluations and controller state.

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = depends_on = None


def pk():
    return sa.Column("id", sa.BigInteger(), primary_key=True)


def fk(name, target, *, nullable=False, unique=False, delete="CASCADE", integer=False):
    return sa.Column(name, sa.Integer() if integer else sa.BigInteger(), sa.ForeignKey(target + ".id", ondelete=delete), nullable=nullable, unique=unique)


def created():
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade():
    for column in (
        sa.Column("workflow_mode", sa.String(20), nullable=False, server_default="legacy"),
        sa.Column("context_snapshot_id", sa.BigInteger()), sa.Column("active_question_id", sa.BigInteger()),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_json", sa.JSON()), sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("interview_sessions", column)
    op.create_table("interview_context_snapshots", pk(), fk("user_id", "users", delete="RESTRICT", integer=True),
        fk("interview_session_id", "interview_sessions", unique=True),
        fk("candidate_profile_version_id", "candidate_profile_versions", delete="RESTRICT"),
        fk("company_id", "companies", delete="RESTRICT"), fk("job_id", "jobs", delete="RESTRICT"),
        fk("job_description_version_id", "job_description_versions", delete="RESTRICT"),
        sa.Column("context_json", sa.JSON(), nullable=False), sa.Column("content_fingerprint", sa.String(64), nullable=False), created())
    op.create_foreign_key("fk_interview_snapshot", "interview_sessions", "interview_context_snapshots", ["context_snapshot_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_interview_active_question", "interview_sessions", "interview_session_questions", ["active_question_id"], ["id"], ondelete="SET NULL")
    for column in (
        fk("source_question_id", "interview_questions", nullable=True, delete="RESTRICT"),
        fk("job_requirement_id", "job_requirements", nullable=True, delete="RESTRICT"),
        sa.Column("source_fingerprint", sa.String(64)), sa.Column("candidate_refs_json", sa.JSON()),
        sa.Column("question_kind", sa.String(20)),
    ):
        op.add_column("interview_session_questions", column)
    op.create_table("interview_answers", pk(), fk("session_id", "interview_sessions"),
        fk("session_question_id", "interview_session_questions", unique=True), fk("message_id", "interview_messages", unique=True),
        sa.Column("idempotency_key", sa.String(100), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text()), sa.Column("result_events", sa.JSON()), created(),
        sa.UniqueConstraint("session_id", "idempotency_key", name="uq_interview_answer_key"))
    dimensions = ("technical_correctness", "coverage", "depth", "relevance", "communication")
    op.create_table("interview_evaluations", pk(), fk("session_id", "interview_sessions"),
        fk("session_question_id", "interview_session_questions"), fk("answer_id", "interview_answers", unique=True),
        sa.Column("artifact_json", sa.JSON(), nullable=False),
        *(sa.Column(key, sa.Integer(), nullable=False) for key in dimensions),
        sa.Column("provider", sa.String(100), nullable=False), sa.Column("model", sa.String(200), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False), sa.Column("structured_retry", sa.Integer(), nullable=False),
        sa.Column("token_usage_json", sa.JSON(), nullable=False), fk("agent_run_id", "experience_agent_runs", delete="RESTRICT"), created(),
        *(sa.CheckConstraint(f"{key} BETWEEN 1 AND 5", name=f"ck_interview_eval_{key}") for key in dimensions))
    op.create_table("interview_agent_calls", pk(), fk("session_id", "interview_sessions"),
        fk("answer_id", "interview_answers", nullable=True), fk("agent_run_id", "experience_agent_runs", unique=True, delete="RESTRICT"))
    # Immutable content boundary; deletion with the owning session remains permitted.
    op.execute("""CREATE FUNCTION m4_snapshot_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'InterviewContextSnapshot is immutable'; END $$""")
    op.execute("CREATE TRIGGER m4_snapshot_immutable BEFORE UPDATE ON interview_context_snapshots FOR EACH ROW EXECUTE FUNCTION m4_snapshot_immutable()")
    op.execute("""CREATE FUNCTION m4_session_context_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.context_snapshot_id IS NOT NULL AND NEW.context_snapshot_id IS DISTINCT FROM OLD.context_snapshot_id THEN
            RAISE EXCEPTION 'Interview context cannot be replaced';
          END IF;
          IF NEW.context_snapshot_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM interview_context_snapshots s WHERE s.id=NEW.context_snapshot_id
              AND s.interview_session_id=NEW.id AND s.user_id=NEW.user_id) THEN
            RAISE EXCEPTION 'Interview context ownership mismatch';
          END IF;
          IF NEW.active_question_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM interview_session_questions q WHERE q.id=NEW.active_question_id AND q.session_id=NEW.id) THEN
            RAISE EXCEPTION 'Active question ownership mismatch';
          END IF;
          RETURN NEW;
        END $$""")
    op.execute("CREATE TRIGGER m4_session_context_guard BEFORE UPDATE ON interview_sessions FOR EACH ROW EXECUTE FUNCTION m4_session_context_guard()")
    # Public entities are not versioned. Protect every selected ref, including
    # questions not asked yet, while preserving admin content-edit capability.
    op.execute("""CREATE FUNCTION m4_public_ref_restrict() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE collection_key text;
        BEGIN
          collection_key := CASE TG_TABLE_NAME WHEN 'interview_questions' THEN 'public_questions'
            WHEN 'interview_experiences' THEN 'interview_experiences' ELSE 'requirements' END;
          IF EXISTS (SELECT 1 FROM interview_context_snapshots s,
              jsonb_array_elements(s.context_json::jsonb -> collection_key) ref
              WHERE (ref ->> 'id')::bigint = OLD.id) THEN
            RAISE EXCEPTION 'Public entity is pinned by an InterviewContextSnapshot' USING ERRCODE='23503';
          END IF;
          RETURN OLD;
        END $$""")
    for table in ("interview_questions", "interview_experiences", "job_requirements"):
        op.execute(f"CREATE TRIGGER m4_public_ref_restrict BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION m4_public_ref_restrict()")
    op.create_index("idx_m4_evaluation_session", "interview_evaluations", ["session_id"])
    op.create_index("idx_m4_agent_session", "interview_agent_calls", ["session_id"])


def downgrade():
    for table in ("interview_questions", "interview_experiences", "job_requirements"):
        op.execute(f"DROP TRIGGER m4_public_ref_restrict ON {table}")
    op.execute("DROP FUNCTION m4_public_ref_restrict()")
    op.execute("DROP TRIGGER m4_session_context_guard ON interview_sessions")
    op.execute("DROP FUNCTION m4_session_context_guard()")
    op.execute("DROP TRIGGER m4_snapshot_immutable ON interview_context_snapshots")
    op.execute("DROP FUNCTION m4_snapshot_immutable()")
    op.drop_constraint("fk_interview_active_question", "interview_sessions", type_="foreignkey")
    op.drop_constraint("fk_interview_snapshot", "interview_sessions", type_="foreignkey")
    for table in ("interview_agent_calls", "interview_evaluations", "interview_answers", "interview_context_snapshots"):
        op.drop_table(table)
    for name in ("source_question_id", "job_requirement_id", "source_fingerprint", "candidate_refs_json", "question_kind"):
        op.drop_column("interview_session_questions", name)
    for name in ("workflow_mode", "context_snapshot_id", "active_question_id", "state_version", "completion_json", "started_at", "completed_at"):
        op.drop_column("interview_sessions", name)
