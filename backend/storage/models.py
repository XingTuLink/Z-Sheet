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
    """Confirmed business rows for one app (Day 15 Confirm 全链路).

    A single JSON document per app maps entity_key -> list of rows. Day 18
    will introduce per-row CRUD storage; until then the whole document is
    replaced whenever the user re-confirms an uploaded workbook.
    """

    __tablename__ = "app_data"

    app_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    records: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
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
