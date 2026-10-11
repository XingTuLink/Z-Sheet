"""create patch_sessions table

Day 25: NL Patch preview sessions. The preview endpoint stores the parsed
intents and generated patch ops under a random server-side token; confirm and
reject only carry that token, so the client cannot tamper with the patch.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "patch_sessions",
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("app_key", sa.String(length=64), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index(
        op.f("ix_patch_sessions_app_key"), "patch_sessions", ["app_key"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_patch_sessions_app_key"), table_name="patch_sessions")
    op.drop_table("patch_sessions")
