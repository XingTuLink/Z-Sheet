"""create app_publications table

Day 33: publish a confirmed app under a custom public slug so visitors can
open a read-only version at /p/{slug}. A publication PINS a model version
(the public page does not change while the owner keeps editing) while the
business rows stay live. The row is designed to survive the future users /
approval-workflow work: creator_id stays NULL until the users table exists
and status already distinguishes published/unpublished.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_publications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("app_key", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        # Reserved for the future users system (Day 0.2): no FK yet because
        # the users table does not exist; the migration adding it will backfill.
        sa.Column("creator_id", sa.String(length=128), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # One publication row per app (re-publishing updates the same row);
    # slugs are the public namespace and must be globally unique.
    op.create_index(
        "uq_app_publications_app_key", "app_publications", ["app_key"], unique=True
    )
    op.create_index(
        "uq_app_publications_slug", "app_publications", ["slug"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_app_publications_slug", table_name="app_publications")
    op.drop_index("uq_app_publications_app_key", table_name="app_publications")
    op.drop_table("app_publications")
