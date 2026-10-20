"""create append_batches for appending data to an existing entity

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "append_batches",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("app_key", sa.String(length=64), nullable=False),
        sa.Column("entity_key", sa.String(length=64), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("new_version", sa.Integer(), nullable=True),
        sa.Column("strategy", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_append_batches_app_key", "append_batches", ["app_key"])
    op.create_index("ix_append_batches_entity_key", "append_batches", ["entity_key"])


def downgrade() -> None:
    op.drop_index("ix_append_batches_entity_key", table_name="append_batches")
    op.drop_index("ix_append_batches_app_key", table_name="append_batches")
    op.drop_table("append_batches")
