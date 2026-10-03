"""Frozen evidence-backed job matching runs.

Revision ID: 0024
Revises: 0023
"""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def fk(name, target, nullable=False):
    return sa.Column(name, sa.BigInteger(), sa.ForeignKey(target, ondelete="RESTRICT"), nullable=nullable)


def upgrade():
    op.create_table("job_match_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True), fk("user_id", "users.id"),
        fk("candidate_profile_version_id", "candidate_profile_versions.id"),
        sa.Column("request_key", sa.String(100), nullable=False), sa.Column("policy_version", sa.String(50), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False), sa.Column("input_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("coverage_json", sa.JSON(), nullable=False), sa.Column("producer", sa.String(50), nullable=False),
        sa.Column("provider", sa.String(100)), sa.Column("model", sa.String(200)),
        sa.Column("attempt_version", sa.Integer(), server_default="0", nullable=False), sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "request_key", name="uq_job_match_request"),
        sa.CheckConstraint("status IN ('READY','RUNNING','COMPLETED','FAILED')", name="ck_job_match_run_status"))
    op.create_index("ix_job_match_runs_user_id", "job_match_runs", ["user_id"])
    op.create_table("job_match_candidates",
        sa.Column("id", sa.BigInteger(), primary_key=True), fk("run_id", "job_match_runs.id"),
        fk("company_id", "companies.id"), fk("job_id", "jobs.id"), fk("job_description_version_id", "job_description_versions.id"),
        sa.Column("input_json", sa.JSON(), nullable=False), sa.Column("eligibility_status", sa.String(20), nullable=False),
        sa.Column("eligibility_reasons", sa.JSON(), nullable=False), sa.Column("unknown_constraints", sa.JSON(), nullable=False),
        sa.Column("evidence_matrix_json", sa.JSON(), nullable=False), sa.Column("ranking_components_json", sa.JSON(), nullable=False),
        sa.Column("pre_rank", sa.Integer()), sa.Column("agent_rank", sa.Integer()), sa.Column("final_rank", sa.Integer()),
        sa.UniqueConstraint("run_id", "job_id", name="uq_job_match_candidate"),
        sa.UniqueConstraint("run_id", "final_rank", name="uq_job_match_final_rank"),
        sa.CheckConstraint("eligibility_status IN ('SATISFIED','NOT_SATISFIED','UNKNOWN')", name="ck_job_match_eligibility"),
        sa.CheckConstraint("eligibility_status != 'NOT_SATISFIED' OR (pre_rank IS NULL AND agent_rank IS NULL AND final_rank IS NULL)", name="ck_job_match_excluded_rank"))
    op.create_index("ix_job_match_candidates_run_id", "job_match_candidates", ["run_id"])
    op.create_table("job_match_recommendations",
        sa.Column("id", sa.BigInteger(), primary_key=True), fk("candidate_id", "job_match_candidates.id"),
        sa.Column("fit_band", sa.String(32), nullable=False), sa.Column("analysis_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("candidate_id", name="uq_job_match_recommendation_candidate"),
        sa.CheckConstraint("fit_band IN ('STRONG_MATCH','GOOD_MATCH','EXPLORATORY','UNCERTAIN')", name="ck_job_match_fit_band"))
    op.create_table("job_match_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True), fk("run_id", "job_match_runs.id"),
        sa.Column("attempt_number", sa.Integer(), nullable=False), fk("agent_run_id", "experience_agent_runs.id", True),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "attempt_number", name="uq_job_match_attempt"))
    op.create_index("ix_job_match_attempts_run_id", "job_match_attempts", ["run_id"])
    op.execute("""CREATE FUNCTION guard_job_match_run() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'JobMatchRun history is immutable'; END IF;
      IF TG_OP = 'UPDATE' THEN
        IF OLD.status = 'COMPLETED' OR
          (to_jsonb(NEW) - ARRAY['status','producer','provider','model','attempt_version','error','completed_at']) IS DISTINCT FROM
          (to_jsonb(OLD) - ARRAY['status','producer','provider','model','attempt_version','error','completed_at'])
        THEN RAISE EXCEPTION 'JobMatchRun inputs/history are immutable'; END IF;
        IF NEW.status = 'COMPLETED' AND (NOT EXISTS (SELECT 1 FROM job_match_candidates WHERE run_id=NEW.id AND eligibility_status!='NOT_SATISFIED') OR
          EXISTS (SELECT 1 FROM job_match_candidates c LEFT JOIN job_match_recommendations r ON r.candidate_id=c.id
            WHERE c.run_id=NEW.id AND c.eligibility_status!='NOT_SATISFIED' AND (r.id IS NULL OR c.agent_rank IS NULL OR c.final_rank IS NULL)))
        THEN RAISE EXCEPTION 'Cannot complete an incomplete matching run'; END IF;
      END IF;
      IF NOT EXISTS (SELECT 1 FROM candidate_profile_versions v JOIN candidate_profiles p ON p.id=v.candidate_profile_id
         WHERE v.id=NEW.candidate_profile_version_id AND p.user_id=NEW.user_id)
      THEN RAISE EXCEPTION 'JobMatchRun profile ownership mismatch'; END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER job_match_run_guard BEFORE INSERT OR UPDATE OR DELETE ON job_match_runs FOR EACH ROW EXECUTE FUNCTION guard_job_match_run()")
    op.execute("""CREATE FUNCTION guard_job_match_child() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE rid bigint; state text; candidate job_match_candidates%ROWTYPE;
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Matching history cannot be deleted'; END IF;
      IF TG_TABLE_NAME = 'job_match_candidates' THEN
        rid := NEW.run_id;
        IF TG_OP='UPDATE' AND
           (to_jsonb(NEW)-ARRAY['agent_rank','final_rank']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['agent_rank','final_rank'])
        THEN RAISE EXCEPTION 'Candidate inputs are immutable'; END IF;
        IF NOT EXISTS (SELECT 1 FROM jobs j JOIN job_description_versions v ON v.job_id=j.id
          WHERE j.id=NEW.job_id AND j.company_id=NEW.company_id AND v.id=NEW.job_description_version_id)
        THEN RAISE EXCEPTION 'Candidate company/job/JD mismatch'; END IF;
      ELSE
        SELECT * INTO candidate FROM job_match_candidates WHERE id=NEW.candidate_id;
        rid := candidate.run_id;
        IF TG_OP='UPDATE' OR candidate.eligibility_status='NOT_SATISFIED'
        THEN RAISE EXCEPTION 'Recommendation is immutable and requires eligible candidate'; END IF;
      END IF;
      SELECT status INTO state FROM job_match_runs WHERE id=rid FOR UPDATE;
      IF state='COMPLETED' THEN RAISE EXCEPTION 'Completed matching history is sealed'; END IF;
      RETURN NEW;
    END $$""")
    for table in ("job_match_candidates", "job_match_recommendations"):
        op.execute(f"CREATE TRIGGER {table}_guard BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION guard_job_match_child()")


def downgrade():
    op.drop_table("job_match_attempts")
    op.drop_table("job_match_recommendations")
    op.drop_table("job_match_candidates")
    op.drop_table("job_match_runs")
    op.execute("DROP FUNCTION guard_job_match_child()")
    op.execute("DROP FUNCTION guard_job_match_run()")
