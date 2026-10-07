"""create model_versions table

Revision ID: 0001
Revises:
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("app_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("patch", sa.JSON(), nullable=True),
        sa.Column("operator", sa.String(length=128), nullable=False),
        sa.Column("source_request", sa.Text(), nullable=True),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_key", "version", name="uq_model_versions_app_version"),
    )
    op.create_index("ix_model_versions_app_key", "model_versions", ["app_key"])


def downgrade() -> None:
    op.drop_index("ix_model_versions_app_key", table_name="model_versions")
    op.drop_table("model_versions")
