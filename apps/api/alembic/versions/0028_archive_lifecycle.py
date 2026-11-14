"""Archive lifecycle timestamps for resumes and private JD targets.

Round 21 proposed a data-retention design and the user approved L1 (resume
archive) and L2 (private-JD archive) only. `archived_at` is deliberately a
separate lifecycle column on both tables, not a new value inside an existing
status enum: `resumes.status` keeps describing *processing*, and
`user_job_targets.parse_status` is guarded by a CHECK that must not grow
business-lifecycle meanings. An archive must never be confused with
"processed" or "confirmed", and NULL means "not archived" so every existing
row keeps its exact current behaviour.

No data is rewritten: upgrade only adds two nullable columns, downgrade only
drops them. Real personal-data deletion (L3) is NOT part of this migration.
"""
from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("resumes", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_job_targets", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("user_job_targets", "archived_at")
    op.drop_column("resumes", "archived_at")
