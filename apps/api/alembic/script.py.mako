"""Alembic migration script type hint markers."""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Connection

revision: str
down_revision: str | None
branch_labels: str | Sequence[str] | None
depends_on: str | Sequence[str] | None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
