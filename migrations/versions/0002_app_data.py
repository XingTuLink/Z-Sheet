"""create app_data table

Day 15: row data confirmed by the user is persisted per app (one JSON
document holding entity_key -> rows), so the runtime bootstrap can serve
the user's own uploaded data rather than only the bundled demo seed.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_data",
        sa.Column("app_key", sa.String(length=64), nullable=False),
        sa.Column("records", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("app_key"),
    )


def downgrade() -> None:
    op.drop_table("app_data")
