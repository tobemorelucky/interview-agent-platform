"""Round 126: explicit exclusion for answered-but-not-technical turns.

A user asking "能提前结束面试吗" is a request, not an answer. The turn has to stay
in the transcript, but it must never be counted as a technical answer or averaged
into a score. Deleting it would rewrite history; re-scoring it would fake an
evaluation. The smallest honest change is a nullable reason column on the two
places the aggregates read, so a row is either counted (NULL) or explicitly out.

Additive only: no column is dropped, no constraint or FK changes, and existing
rows keep NULL, which is exactly the "counted" semantics they had before.
"""
from alembic import op
import sqlalchemy as sa

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("interview_session_questions", "interview_evaluations"):
        op.add_column(table, sa.Column("excluded_reason", sa.String(length=40), nullable=True))
        op.add_column(table, sa.Column("excluded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    for table in ("interview_session_questions", "interview_evaluations"):
        op.drop_column(table, "excluded_at")
        op.drop_column(table, "excluded_reason")
