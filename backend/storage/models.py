"""ORM models for model versioning (design doc 28.7).

Every successful model change appends a new immutable row. The current
version of an app is the row with the highest version number for its app_key.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.storage.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ModelVersionRecord(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("app_key", "version", name="uq_model_versions_app_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    # Full BusinessModel snapshot as JSON-serializable dict.
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # Raw JSON Patch array that produced this version; NULL for the import (v1).
    patch: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)

    operator: Mapped[str] = mapped_column(String(128), nullable=False, default="local")
    source_request: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="passed")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class AppDataRecord(Base):
    """Legacy whole-document storage for confirmed business rows.

    Superseded by :class:`BusinessRecord` (one row per business row). Kept so
    the legacy table stays in the metadata: 0006 migrates its documents into
    ``business_records`` and downgrading can rebuild the archive losslessly.
    """

    __tablename__ = "app_data"

    app_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    records: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class BusinessRecord(Base):
    """One confirmed business row (storage layering).

    Rows live one-per-record instead of one JSON document per app, so editing
    a single row rewrites only that row. ``row_key`` is the business key from
    the entity's key field; the unique constraint is the last line of defence
    against duplicate inserts. ``model_version`` records which model version
    was current when the row was written.
    """

    __tablename__ = "business_records"
    __table_args__ = (
        UniqueConstraint(
            "app_key", "entity_key", "row_key", name="uq_business_records_identity"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_key: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    row_key: Mapped[str] = mapped_column(String(255), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    model_version: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class PatchSessionRecord(Base):
    """Server-side NL patch preview awaiting the user's decision (Day 25).

    Preview stores the parsed intents plus the fully generated (and dry-run
    validated) patch ops here under a random token. Confirm/reject reads the
    cached ops back: the client only submits the token and its decision, so it
    cannot tamper with the patch. ``base_version`` pins the model the preview
    was generated against; confirm refuses if the app moved on meanwhile.
    """

    __tablename__ = "patch_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    app_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class AppPublicationRecord(Base):
    """Public link state for one app (Day 33 publish feature).

    Exactly one row per app. Publishing pins ``model_version``: visitors of
    the slug keep seeing that model while the owner keeps editing; records
    stay live. Changing ``slug`` immediately invalidates the old URL; setting
    ``status`` to unpublished takes the link down (the row, and hence slug
    ownership, is retained so nobody can squat a previously used slug).
    ``creator_id`` is reserved NULL for the future users / approval system.
    """

    __tablename__ = "app_publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    model_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="published")
    creator_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class UnderstandingSessionRecord(Base):
    """Server-side understanding result keyed by uploaded file hash (Day 22).

    Parse stores the (possibly LLM-produced) ProfiledParsedWorkbook here;
    Confirm reads it back instead of re-inferring. The client therefore cannot
    influence the confirmed model — it only submits accept/reject decisions —
    and a second, non-deterministic LLM call cannot disagree with what the
    user reviewed.
    """

    __tablename__ = "understanding_sessions"

    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    engine: Mapped[str] = mapped_column(String(16), nullable=False, default="rules")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
