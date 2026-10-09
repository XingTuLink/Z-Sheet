"""create understanding_sessions table

Day 22: store the understanding result (LLM or rules) keyed by a hash of the
uploaded file so Confirm consumes exactly what the user reviewed instead of
re-running inference (which would make a second, possibly different LLM call).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "understanding_sessions",
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("engine", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("content_hash"),
    )


def downgrade() -> None:
    op.drop_table("understanding_sessions")
