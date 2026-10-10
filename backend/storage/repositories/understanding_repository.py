"""Server-side understanding sessions keyed by uploaded file hash (Day 22).

Parse writes the full ProfiledParsedWorkbook (LLM or rules) here; Confirm reads
the same snapshot back. The client never supplies structure, only decisions,
and a non-deterministic LLM cannot be called twice with diverging results.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.storage.models import UnderstandingSessionRecord
from backend.understanding.schemas import ProfiledParsedWorkbook

# Bump whenever parser/understanding semantics change: stale sessions from an
# older pipeline (e.g. cached before aggregate rows were filtered) must never
# be reused by Confirm.
SESSION_SCHEMA_VERSION = "v2"


def content_hash(content: bytes) -> str:
    """Stable identity for an uploaded workbook (sha256 hex)."""
    return hashlib.sha256(content).hexdigest()


def session_key(content: bytes) -> str:
    """Cache key for the understanding result: file content + pipeline version."""
    digest = hashlib.sha256()
    digest.update(SESSION_SCHEMA_VERSION.encode())
    digest.update(b"\x00")
    digest.update(content)
    return digest.hexdigest()


def save_understanding(
    db: Session, key: str, result: ProfiledParsedWorkbook
) -> None:
    """Upsert the understanding result for one session key."""
    existing = db.get(UnderstandingSessionRecord, key)
    if existing is not None:
        existing.payload = result.model_dump(mode="json")
        existing.engine = result.understanding_engine
        record = existing
    else:
        record = UnderstandingSessionRecord(
            content_hash=key,
            payload=result.model_dump(mode="json"),
            engine=result.understanding_engine,
        )
        db.add(record)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def load_understanding(
    db: Session, key: str
) -> ProfiledParsedWorkbook | None:
    """Return the reviewed understanding snapshot, or None on cache miss."""
    record = db.scalar(
        select(UnderstandingSessionRecord).where(
            UnderstandingSessionRecord.content_hash == key
        )
    )
    if record is None:
        return None
    return ProfiledParsedWorkbook.model_validate(record.payload)


__all__ = [
    "SESSION_SCHEMA_VERSION",
    "content_hash",
    "load_understanding",
    "save_understanding",
    "session_key",
]
