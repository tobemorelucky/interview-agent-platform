"""Private immutable candidate snapshots and deterministic resume evidence.

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def pk():
    return sa.Column("id", sa.BigInteger(), primary_key=True)


def fk(name, target, *, nullable=False, user=False, unique=False):
    return sa.Column(name, sa.Integer() if user else sa.BigInteger(),
                     sa.ForeignKey(target + ".id", ondelete="RESTRICT"), nullable=nullable, unique=unique)


def created():
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


IMMUTABLE = (
    "candidate_profile_versions", "resume_evidence", "user_confirmed_evidence",
    "candidate_facts", "candidate_fact_evidence", "candidate_project_experiences",
    "candidate_work_experiences", "candidate_education_experiences", "candidate_user_skills",
    "candidate_profile_corrections",
)


def upgrade():
    op.create_table("candidate_profiles", pk(), fk("user_id", "users", user=True, unique=True),
                    sa.Column("current_version_id", sa.BigInteger(), nullable=True), created(),
                    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table("candidate_profile_versions", pk(), fk("candidate_profile_id", "candidate_profiles"),
                    sa.Column("version_number", sa.Integer(), nullable=False),
                    fk("base_version_id", "candidate_profile_versions", nullable=True),
                    fk("source_resume_id", "resumes"),
                    sa.Column("generation_key", sa.String(64), nullable=False, unique=True),
                    sa.Column("summary", sa.Text()), sa.Column("unknowns", sa.JSON(), nullable=False),
                    sa.Column("snapshot_json", sa.JSON(), nullable=False),
                    sa.Column("producer", sa.String(40), nullable=False),
                    sa.Column("provider", sa.String(100)), sa.Column("model", sa.String(200)),
                    sa.Column("prompt_version", sa.String(50)),
                    fk("agent_run_id", "experience_agent_runs", nullable=True), created(),
                    sa.UniqueConstraint("candidate_profile_id", "version_number", name="uq_candidate_profile_version"))
    op.create_foreign_key("fk_profile_current_version", "candidate_profiles", "candidate_profile_versions", ["current_version_id"], ["id"], ondelete="RESTRICT")
    op.create_table("resume_evidence", pk(), fk("resume_id", "resumes"),
                    sa.Column("evidence_key", sa.String(80), nullable=False, unique=True),
                    sa.Column("section", sa.String(100), nullable=False), sa.Column("page", sa.Integer()),
                    sa.Column("start_offset", sa.Integer(), nullable=False), sa.Column("end_offset", sa.Integer(), nullable=False),
                    sa.Column("excerpt", sa.Text(), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False), created())
    op.create_table("user_confirmed_evidence", pk(), fk("candidate_profile_id", "candidate_profiles"),
                    fk("user_id", "users", user=True), sa.Column("evidence_key", sa.String(80), nullable=False, unique=True),
                    sa.Column("statement", sa.Text(), nullable=False), sa.Column("reason", sa.Text(), nullable=False), created())
    op.create_table("candidate_facts", pk(), fk("version_id", "candidate_profile_versions"),
                    sa.Column("field_path", sa.String(200), nullable=False), sa.Column("statement", sa.Text(), nullable=False),
                    sa.Column("provenance", sa.String(32), nullable=False),
                    sa.UniqueConstraint("version_id", "field_path", name="uq_candidate_fact_path"))
    op.create_table("candidate_fact_evidence", pk(), fk("fact_id", "candidate_facts"),
                    fk("resume_evidence_id", "resume_evidence", nullable=True),
                    fk("user_evidence_id", "user_confirmed_evidence", nullable=True),
                    sa.CheckConstraint("(resume_evidence_id IS NULL) <> (user_evidence_id IS NULL)", name="ck_candidate_evidence_one_source"))
    for table, label in (("candidate_project_experiences", "name"), ("candidate_work_experiences", "employer"), ("candidate_education_experiences", "institution")):
        extras = [sa.Column("experience_type", sa.String(20), nullable=False)] if table == "candidate_work_experiences" else []
        op.create_table(table, pk(), fk("version_id", "candidate_profile_versions"), fk("fact_id", "candidate_facts", unique=True),
                        sa.Column(label, sa.Text(), nullable=False), sa.Column("description", sa.Text(), nullable=False), *extras)
    op.create_table("candidate_user_skills", pk(), fk("version_id", "candidate_profile_versions"),
                    fk("fact_id", "candidate_facts", unique=True), sa.Column("name", sa.Text(), nullable=False),
                    sa.Column("evidence_state", sa.String(32), nullable=False),
                    sa.CheckConstraint("evidence_state IN ('MENTIONED', 'DEMONSTRATED', 'STRONG_EVIDENCE')", name="ck_candidate_skill_state"))
    op.create_table("candidate_profile_corrections", pk(), fk("base_version_id", "candidate_profile_versions"),
                    fk("new_version_id", "candidate_profile_versions", unique=True),
                    sa.Column("field_path", sa.String(200), nullable=False),
                    sa.Column("old_value", sa.JSON(), nullable=False), sa.Column("new_value", sa.JSON(), nullable=False),
                    sa.Column("changed_by", sa.String(20), nullable=False), fk("user_id", "users", user=True),
                    sa.Column("reason", sa.Text(), nullable=False), created())
    op.create_table("candidate_profile_generations", pk(), fk("resume_id", "resumes"),
                    sa.Column("generation_key", sa.String(64), nullable=False, unique=True),
                    sa.Column("status", sa.String(32), nullable=False), sa.Column("error", sa.Text()),
                    fk("version_id", "candidate_profile_versions", nullable=True),
                    fk("agent_run_id", "experience_agent_runs", nullable=True), created(),
                    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    for table, columns in {
        "candidate_profile_versions": ("candidate_profile_id", "source_resume_id"),
        "resume_evidence": ("resume_id",), "candidate_facts": ("version_id",),
        "candidate_fact_evidence": ("fact_id",), "candidate_profile_generations": ("resume_id",),
        **{table: ("version_id",) for table in ("candidate_project_experiences", "candidate_work_experiences", "candidate_education_experiences", "candidate_user_skills")},
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])
    op.execute("""CREATE FUNCTION reject_candidate_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'candidate history is immutable: %', TG_TABLE_NAME USING ERRCODE = '23514'; END; $$""")
    for table in IMMUTABLE:
        op.execute(f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_candidate_history_mutation()")
    op.execute("""CREATE FUNCTION validate_candidate_current_version() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.current_version_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM candidate_profile_versions WHERE id = NEW.current_version_id AND candidate_profile_id = NEW.id
      ) THEN RAISE EXCEPTION 'current version belongs to another profile' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END; $$""")
    op.execute("CREATE TRIGGER validate_current_version BEFORE INSERT OR UPDATE ON candidate_profiles FOR EACH ROW EXECUTE FUNCTION validate_candidate_current_version()")
    # A published historical snapshot cannot acquire additional facts/links either.
    op.execute("""CREATE FUNCTION reject_sealed_candidate_insert() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE target_version bigint;
    BEGIN
      IF TG_TABLE_NAME = 'candidate_fact_evidence' THEN
        SELECT version_id INTO target_version FROM candidate_facts WHERE id = NEW.fact_id;
      ELSE target_version := NEW.version_id; END IF;
      IF EXISTS (
        SELECT 1 FROM candidate_profile_versions v
        JOIN candidate_profiles p ON p.id = v.candidate_profile_id
        JOIN candidate_profile_versions current_v ON current_v.id = p.current_version_id
        WHERE v.id = target_version AND v.version_number <= current_v.version_number
      ) THEN RAISE EXCEPTION 'published candidate snapshot is sealed' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END; $$""")
    for table in ("candidate_facts", "candidate_fact_evidence", "candidate_project_experiences",
                  "candidate_work_experiences", "candidate_education_experiences", "candidate_user_skills"):
        op.execute(f"CREATE TRIGGER sealed_history BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION reject_sealed_candidate_insert()")


def downgrade():
    op.drop_constraint("fk_profile_current_version", "candidate_profiles", type_="foreignkey")
    for table in ("candidate_profile_generations", "candidate_profile_corrections", "candidate_user_skills",
                  "candidate_education_experiences", "candidate_work_experiences", "candidate_project_experiences",
                  "candidate_fact_evidence", "candidate_facts", "user_confirmed_evidence", "resume_evidence",
                  "candidate_profile_versions", "candidate_profiles"):
        op.drop_table(table)
    op.execute("DROP FUNCTION validate_candidate_current_version()")
    op.execute("DROP FUNCTION reject_candidate_history_mutation()")
    op.execute("DROP FUNCTION reject_sealed_candidate_insert()")
