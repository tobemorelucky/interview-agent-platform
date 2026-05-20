"""add CHECK constraint on users.role

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-20

"""

from collections.abc import Sequence

from alembic import op


revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('USER', 'ADMIN')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
