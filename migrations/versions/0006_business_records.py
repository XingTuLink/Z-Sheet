"""create business_records and migrate the legacy app_data documents

Storage layering: confirmed rows move from one JSON document per app to one
row per business record, so editing a row rewrites only that row. The legacy
``app_data`` table is kept as an archive: downgrading rebuilds documents from
it, and the backfill below only reads it.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _legacy_tables() -> tuple[sa.TableClause, sa.TableClause]:
    app_data = sa.table(
        "app_data",
        sa.column("app_key", sa.String),
        sa.column("records", sa.JSON),
    )
    versions = sa.table(
        "model_versions",
        sa.column("app_key", sa.String),
        sa.column("version", sa.Integer),
        sa.column("snapshot", sa.JSON),
    )
    return app_data, versions


def _business_records() -> sa.TableClause:
    return sa.table(
        "business_records",
        sa.column("app_key", sa.String),
        sa.column("entity_key", sa.String),
        sa.column("row_key", sa.String),
        sa.column("data", sa.JSON),
        sa.column("model_version", sa.Integer),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _unique_key(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    suffix = 2
    while f"{base}#{suffix}" in taken:
        suffix += 1
    return f"{base}#{suffix}"


def _entity_key_fields(
    bind: Connection, versions: sa.TableClause, app_key: str
) -> tuple[dict[str, str], int]:
    """Map entity_key -> key_field using the app's current model snapshot."""
    row = bind.execute(
        sa.select(versions.c.snapshot, versions.c.version)
        .where(versions.c.app_key == app_key)
        .order_by(versions.c.version.desc())
        .limit(1)
    ).first()
    if row is None or not isinstance(row[0], dict):
        return {}, 1
    snapshot: dict[str, Any] = row[0]
    fields: dict[str, str] = {}
    for entity in snapshot.get("entities") or []:
        if isinstance(entity, dict) and entity.get("key") and entity.get("key_field"):
            fields[str(entity["key"])] = str(entity["key_field"])
    return fields, int(row[1])


def backfill_legacy_documents(bind: Connection) -> int:
    """Copy every app_data document into business_records.

    Idempotent: apps that already own business rows are skipped, so running
    the migration again (or on a partially migrated database) inserts
    nothing. Duplicate business keys inside one entity are preserved as
    ``key``, ``key#2``... because storage identity must be unique while the
    workbook really did repeat the key.
    """
    app_data, versions = _legacy_tables()
    records = _business_records()
    migrated = set(
        bind.execute(sa.select(records.c.app_key).distinct()).scalars().all()
    )

    inserted = 0
    documents = bind.execute(
        sa.select(app_data.c.app_key, app_data.c.records)
    ).all()
    for app_key, document in documents:
        if app_key in migrated or not isinstance(document, dict):
            continue
        key_fields, model_version = _entity_key_fields(bind, versions, app_key)
        now = datetime.now(UTC)
        for entity_key, rows in document.items():
            if not isinstance(rows, list):
                continue
            taken: set[str] = set()
            for index, data in enumerate(rows):
                if not isinstance(data, dict):
                    continue
                key_field = key_fields.get(str(entity_key))
                raw_key = data.get(key_field) if key_field else None
                base = (
                    str(raw_key)
                    if raw_key not in (None, "")
                    else f"row-{index + 1}"
                )
                row_key = _unique_key(base, taken)
                taken.add(row_key)
                bind.execute(
                    records.insert().values(
                        app_key=app_key,
                        entity_key=str(entity_key),
                        row_key=row_key,
                        data=data,
                        model_version=model_version,
                        created_at=now,
                        updated_at=now,
                    )
                )
                inserted += 1
    return inserted


def upgrade() -> None:
    op.create_table(
        "business_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("app_key", sa.String(length=64), nullable=False),
        sa.Column("entity_key", sa.String(length=64), nullable=False),
        sa.Column("row_key", sa.String(length=255), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("model_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "app_key",
            "entity_key",
            "row_key",
            name="uq_business_records_identity",
        ),
    )
    backfill_legacy_documents(op.get_bind())


def downgrade() -> None:
    # app_data is untouched by this revision, so dropping the row table loses
    # nothing: a downgrade simply returns to serving the archived documents.
    op.drop_table("business_records")
