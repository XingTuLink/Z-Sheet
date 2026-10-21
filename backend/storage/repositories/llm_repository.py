"""Persistence for saved LLM connection profiles.

Profiles are global (the running instance uses one model at a time). Activating
a profile clears the flag on every other row in the same transaction, so the
"exactly one active" invariant does not depend on callers.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.storage.models import LlmProfileRecord


def list_all(db: Session) -> Sequence[LlmProfileRecord]:
    return db.scalars(
        select(LlmProfileRecord).order_by(LlmProfileRecord.id.asc())
    ).all()


def get(db: Session, profile_id: int) -> LlmProfileRecord | None:
    return db.get(LlmProfileRecord, profile_id)


def get_active(db: Session) -> LlmProfileRecord | None:
    return db.scalar(
        select(LlmProfileRecord)
        .where(LlmProfileRecord.is_active.is_(True))
        .order_by(LlmProfileRecord.id.asc())
        .limit(1)
    )


def find_by_name(db: Session, name: str) -> LlmProfileRecord | None:
    return db.scalar(
        select(LlmProfileRecord).where(LlmProfileRecord.name == name)
    )


def create(
    db: Session,
    *,
    name: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    enable_thinking: bool,
    max_retries: int,
    activate: bool,
) -> LlmProfileRecord:
    record = LlmProfileRecord(
        name=name,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        enable_thinking=enable_thinking,
        max_retries=max_retries,
        is_active=False,
    )
    db.add(record)
    db.flush()
    if activate or get_active(db) is None:
        _set_active(db, record)
    db.commit()
    db.refresh(record)
    return record


def update_profile(
    db: Session,
    record: LlmProfileRecord,
    *,
    name: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    enable_thinking: bool | None = None,
    max_retries: int | None = None,
) -> LlmProfileRecord:
    """Patch fields; ``api_key=None`` keeps the stored key unchanged."""
    if name is not None:
        record.name = name
    if base_url is not None:
        record.base_url = base_url
    if api_key is not None:
        record.api_key = api_key
    if model is not None:
        record.model = model
    if timeout is not None:
        record.timeout = timeout
    if enable_thinking is not None:
        record.enable_thinking = enable_thinking
    if max_retries is not None:
        record.max_retries = max_retries
    record.updated_at = datetime.now(UTC)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def activate(db: Session, record: LlmProfileRecord) -> LlmProfileRecord:
    _set_active(db, record)
    db.commit()
    db.refresh(record)
    return record


def deactivate(db: Session) -> None:
    """Return to the .env fallback without deleting any profile."""
    db.execute(
        update(LlmProfileRecord)
        .where(LlmProfileRecord.is_active.is_(True))
        .values(is_active=False, updated_at=datetime.now(UTC))
    )
    db.commit()


def delete(db: Session, record: LlmProfileRecord) -> None:
    db.delete(record)
    db.commit()


def _set_active(db: Session, record: LlmProfileRecord) -> None:
    db.execute(
        update(LlmProfileRecord)
        .where(LlmProfileRecord.id != record.id)
        .values(is_active=False)
    )
    record.is_active = True
    record.updated_at = datetime.now(UTC)
    db.add(record)


__all__ = [
    "activate",
    "create",
    "deactivate",
    "delete",
    "find_by_name",
    "get",
    "get_active",
    "list_all",
    "update_profile",
]
