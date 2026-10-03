"""Versioned coaching reports and explicitly approved Memory provenance.

Revision ID: 0023
Revises: 0022
"""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = depends_on = None


def pk():
    return sa.Column("id", sa.BigInteger(), primary_key=True)


def fk(name, target, *, nullable=False, user=False, unique=False, delete="RESTRICT", primary=False):
    return sa.Column(name, sa.Integer() if user else sa.BigInteger(),
        sa.ForeignKey(target + ".id", ondelete=delete), nullable=nullable, unique=unique, primary_key=primary)


def timestamp(name="created_at", *, nullable=False, default=True):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable,
        server_default=sa.func.now() if default else None)


def upgrade():
    op.create_table("final_interview_reports", pk(), fk("user_id", "users", user=True),
        fk("session_id", "interview_sessions"), fk("context_snapshot_id", "interview_context_snapshots"),
        sa.Column("generation_number", sa.Integer(), nullable=False),
        sa.Column("generation_key", sa.String(64), nullable=False, unique=True),
        sa.Column("evaluation_fingerprint", sa.String(64), nullable=False),
        fk("base_current_report_id", "final_interview_reports", nullable=True),
        sa.Column("generation_status", sa.String(20), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attempt_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("statistics_json", sa.JSON(), nullable=False), sa.Column("coach_analysis_json", sa.JSON()),
        sa.Column("producer", sa.String(64), nullable=False), sa.Column("provider", sa.String(100)),
        sa.Column("model", sa.String(200)), sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("error", sa.Text()), timestamp(), timestamp("completed_at", nullable=True, default=False),
        sa.UniqueConstraint("session_id", "generation_number", name="uq_final_report_generation"),
        sa.CheckConstraint("NOT is_current OR generation_status = 'COMPLETED'", name="ck_final_report_current_completed"))
    op.create_index("ix_final_interview_reports_user_id", "final_interview_reports", ["user_id"])
    op.create_index("ix_final_interview_reports_session_id", "final_interview_reports", ["session_id"])
    op.create_index("uq_final_report_current", "final_interview_reports", ["session_id"], unique=True,
        postgresql_where=sa.text("is_current"))
    op.create_table("interview_report_attempts", pk(), fk("report_id", "final_interview_reports"),
        sa.Column("attempt_number", sa.Integer(), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        fk("agent_run_id", "experience_agent_runs", nullable=True), sa.Column("error", sa.Text()),
        timestamp("started_at"), timestamp("completed_at", nullable=True, default=False),
        sa.UniqueConstraint("report_id", "attempt_number", name="uq_report_attempt_number"))
    op.create_index("ix_interview_report_attempts_report_id", "interview_report_attempts", ["report_id"])
    op.create_table("interview_performance_observations", pk(), fk("user_id", "users", user=True),
        fk("session_id", "interview_sessions"), fk("report_id", "final_interview_reports"),
        sa.Column("observation_type", sa.String(32), nullable=False), sa.Column("text", sa.Text(), nullable=False),
        sa.Column("supporting_evaluation_ids", sa.JSON(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False), timestamp())
    op.create_index("ix_interview_performance_observations_user_id", "interview_performance_observations", ["user_id"])
    op.create_index("ix_interview_performance_observations_report_id", "interview_performance_observations", ["report_id"])
    op.create_table("performance_observation_evaluations", pk(), fk("observation_id", "interview_performance_observations"),
        fk("evaluation_id", "interview_evaluations"),
        sa.UniqueConstraint("observation_id", "evaluation_id", name="uq_observation_evaluation"))
    op.create_table("memory_proposals", pk(), fk("user_id", "users", user=True, delete="CASCADE"),
        fk("session_id", "interview_sessions"), fk("report_id", "final_interview_reports"),
        sa.Column("proposal_type", sa.String(32), nullable=False), sa.Column("original_content", sa.Text(), nullable=False),
        sa.Column("corrected_content", sa.Text()), sa.Column("content_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("confidence", sa.String(40), nullable=False), sa.Column("supporting_evaluation_ids", sa.JSON(), nullable=False),
        sa.Column("observation_ids", sa.JSON(), nullable=False), fk("confirmed_by", "users", user=True, nullable=True),
        sa.Column("confirmation_reason", sa.Text()), timestamp("confirmed_at", nullable=True, default=False),
        sa.Column("changed_by", sa.String(20)), timestamp("corrected_at", nullable=True, default=False),
        fk("memory_item_id", "user_memory_items", nullable=True, unique=True), timestamp(),
        sa.UniqueConstraint("report_id", "content_fingerprint", name="uq_memory_proposal_report_content"),
        sa.CheckConstraint("status IN ('PENDING','APPROVED','CORRECTED','REJECTED')", name="ck_memory_proposal_status"),
        sa.CheckConstraint("proposal_type IN ('GOAL','PREFERENCE','EXPERIENCE','SKILL_OBSERVATION','PREPARATION_FOCUS')", name="ck_memory_proposal_type"),
        sa.CheckConstraint("confidence IN ('SINGLE_EVALUATION','REPEATED_EVALUATIONS')", name="ck_memory_proposal_confidence"),
        sa.CheckConstraint("status NOT IN ('APPROVED','CORRECTED') OR (memory_item_id IS NOT NULL AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)", name="ck_memory_proposal_approved_memory"),
        sa.CheckConstraint("status != 'CORRECTED' OR (corrected_content IS NOT NULL AND corrected_at IS NOT NULL AND changed_by = 'user')", name="ck_memory_proposal_corrected"),
        sa.CheckConstraint("status NOT IN ('PENDING','REJECTED') OR memory_item_id IS NULL", name="ck_memory_proposal_uncommitted"))
    op.create_index("idx_memory_proposals_user_status", "memory_proposals", ["user_id", "status"])
    op.create_table("memory_proposal_evaluations", fk("proposal_id", "memory_proposals", primary=True, delete="CASCADE"),
        fk("evaluation_id", "interview_evaluations", primary=True))
    op.create_table("memory_proposal_observations", fk("proposal_id", "memory_proposals", primary=True, delete="CASCADE"),
        fk("observation_id", "interview_performance_observations", primary=True))

    op.execute("""CREATE FUNCTION m5_report_guard() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NOT EXISTS (SELECT 1 FROM interview_sessions s JOIN interview_context_snapshots c
          ON c.id=NEW.context_snapshot_id WHERE s.id=NEW.session_id AND s.user_id=NEW.user_id
            AND s.workflow_mode='m4' AND s.status='COMPLETED'
            AND c.interview_session_id=s.id AND c.user_id=NEW.user_id AND s.context_snapshot_id=c.id) THEN
          RAISE EXCEPTION 'Report requires the completed owned M4 Snapshot';
        END IF;
        IF NEW.base_current_report_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM final_interview_reports r
          WHERE r.id=NEW.base_current_report_id AND r.session_id=NEW.session_id AND r.user_id=NEW.user_id) THEN
          RAISE EXCEPTION 'Report generation base ownership mismatch';
        END IF;
        IF TG_OP='UPDATE' AND OLD.generation_status='COMPLETED'
          AND (to_jsonb(NEW)-'is_current') IS DISTINCT FROM (to_jsonb(OLD)-'is_current') THEN
          RAISE EXCEPTION 'Completed Report content is immutable';
        END IF;
        RETURN NEW;
      END $$""")
    op.execute("CREATE TRIGGER m5_report_guard BEFORE INSERT OR UPDATE ON final_interview_reports FOR EACH ROW EXECUTE FUNCTION m5_report_guard()")
    op.execute("""CREATE FUNCTION m5_observation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE n bigint;
      BEGIN
        IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'Performance observation is immutable'; END IF;
        IF NOT EXISTS (SELECT 1 FROM final_interview_reports r WHERE r.id=NEW.report_id
          AND r.session_id=NEW.session_id AND r.user_id=NEW.user_id) THEN
          RAISE EXCEPTION 'Observation report ownership mismatch';
        END IF;
        SELECT COUNT(DISTINCT value) INTO n FROM jsonb_array_elements(NEW.supporting_evaluation_ids::jsonb);
        IF n=0 OR n != NEW.occurrence_count OR (NEW.observation_type='RECURRING_ISSUE' AND n<2) THEN
          RAISE EXCEPTION 'Observation support count invalid';
        END IF;
        IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(NEW.supporting_evaluation_ids::jsonb) ref
          WHERE NOT EXISTS (SELECT 1 FROM interview_evaluations e WHERE e.id=ref::bigint AND e.session_id=NEW.session_id)) THEN
          RAISE EXCEPTION 'Observation references another session evaluation';
        END IF;
        RETURN NEW;
      END $$""")
    op.execute("CREATE TRIGGER m5_observation_guard BEFORE INSERT OR UPDATE ON interview_performance_observations FOR EACH ROW EXECUTE FUNCTION m5_observation_guard()")
    op.execute("""CREATE FUNCTION m5_proposal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NOT EXISTS (SELECT 1 FROM final_interview_reports r WHERE r.id=NEW.report_id
          AND r.session_id=NEW.session_id AND r.user_id=NEW.user_id) THEN
          RAISE EXCEPTION 'Proposal report ownership mismatch';
        END IF;
        IF TG_OP='UPDATE' AND (to_jsonb(NEW)-ARRAY['status','corrected_content','confirmed_by','confirmation_reason',
            'confirmed_at','changed_by','corrected_at','memory_item_id']) IS DISTINCT FROM
          (to_jsonb(OLD)-ARRAY['status','corrected_content','confirmed_by','confirmation_reason',
            'confirmed_at','changed_by','corrected_at','memory_item_id']) THEN
          RAISE EXCEPTION 'Original Proposal and provenance are immutable';
        END IF;
        IF TG_OP='UPDATE' AND OLD.status!='PENDING' AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
          RAISE EXCEPTION 'Finalized Proposal decision is immutable';
        END IF;
        IF NEW.memory_item_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM user_memory_items m
          WHERE m.id=NEW.memory_item_id AND m.user_id=NEW.user_id AND m.source_type='MEMORY_PROPOSAL' AND m.source_id=NEW.id) THEN
          RAISE EXCEPTION 'Proposal Memory ownership or provenance mismatch';
        END IF;
        RETURN NEW;
      END $$""")
    op.execute("CREATE TRIGGER m5_proposal_guard BEFORE INSERT OR UPDATE ON memory_proposals FOR EACH ROW EXECUTE FUNCTION m5_proposal_guard()")
    op.execute("""CREATE FUNCTION m5_support_guard() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'Supporting provenance is immutable'; END IF;
        IF TG_TABLE_NAME='performance_observation_evaluations' THEN
          IF NOT EXISTS (SELECT 1 FROM interview_performance_observations o JOIN interview_evaluations e
            ON e.id=NEW.evaluation_id AND e.session_id=o.session_id WHERE o.id=NEW.observation_id
              AND o.supporting_evaluation_ids::jsonb @> jsonb_build_array(NEW.evaluation_id)) THEN
            RAISE EXCEPTION 'Observation evaluation relation mismatch';
          END IF;
        ELSIF TG_TABLE_NAME='memory_proposal_evaluations' THEN
          IF NOT EXISTS (SELECT 1 FROM memory_proposals p JOIN interview_evaluations e
            ON e.id=NEW.evaluation_id AND e.session_id=p.session_id WHERE p.id=NEW.proposal_id
              AND p.supporting_evaluation_ids::jsonb @> jsonb_build_array(NEW.evaluation_id)) THEN
            RAISE EXCEPTION 'Proposal evaluation relation mismatch';
          END IF;
        ELSE
          IF NOT EXISTS (SELECT 1 FROM memory_proposals p JOIN interview_performance_observations o
            ON o.id=NEW.observation_id AND o.session_id=p.session_id AND o.report_id=p.report_id
              AND o.user_id=p.user_id WHERE p.id=NEW.proposal_id
              AND p.observation_ids::jsonb @> jsonb_build_array(NEW.observation_id)) THEN
            RAISE EXCEPTION 'Proposal observation relation mismatch';
          END IF;
        END IF;
        RETURN NEW;
      END $$""")
    for table in ("performance_observation_evaluations", "memory_proposal_evaluations", "memory_proposal_observations"):
        op.execute(f"CREATE TRIGGER m5_support_guard BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION m5_support_guard()")


def downgrade():
    for table in ("performance_observation_evaluations", "memory_proposal_evaluations", "memory_proposal_observations"):
        op.execute(f"DROP TRIGGER m5_support_guard ON {table}")
    op.execute("DROP FUNCTION m5_support_guard()")
    op.execute("DROP TRIGGER m5_proposal_guard ON memory_proposals")
    op.execute("DROP FUNCTION m5_proposal_guard()")
    op.execute("DROP TRIGGER m5_observation_guard ON interview_performance_observations")
    op.execute("DROP FUNCTION m5_observation_guard()")
    op.execute("DROP TRIGGER m5_report_guard ON final_interview_reports")
    op.execute("DROP FUNCTION m5_report_guard()")
    for table in ("memory_proposal_observations", "memory_proposal_evaluations", "memory_proposals",
                  "performance_observation_evaluations", "interview_performance_observations",
                  "interview_report_attempts", "final_interview_reports"):
        op.drop_table(table)
