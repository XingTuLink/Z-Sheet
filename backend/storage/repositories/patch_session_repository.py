"""Server-side NL patch preview sessions (Day 25).

Preview runs parse + generate once and caches the grounded intents together
with the generated ops under a random token. Confirm/reject submit only the
token and their decision: structure never travels back from the client, so a
patch cannot be tampered with between preview and apply — the same anti-forgery
pattern as Day 22 understanding sessions.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.patch.intent_parser import IntentParseResult
from backend.storage.models import PatchSessionRecord

# Previews are short-lived review artefacts, not durable history.
SESSION_TTL = timedelta(hours=1)

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"


class PatchSessionPayload(BaseModel):
    """Everything Confirm needs without talking to the LLM or the client again."""

    model_config = ConfigDict(extra="forbid")

    user_text: str
    parse_result: IntentParseResult
    ops: list[dict[str, Any]]
    requires_confirmation: bool
    notes: list[str]
    operator: str = "local"


def create_session(
    db: Session,
    app_key: str,
    base_version: int,
    payload: PatchSessionPayload,
) -> str:
    """Persist a pending preview and return its unguessable token."""
    session_id = secrets.token_urlsafe(24)
    record = PatchSessionRecord(
        session_id=session_id,
        app_key=app_key,
        base_version=base_version,
        payload=payload.model_dump(mode="json"),
        status=STATUS_PENDING,
    )
    db.add(record)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(record)
    return session_id


def get_session(
    db: Session, session_id: str, app_key: str
) -> PatchSessionRecord | None:
    """Return the session when it exists and belongs to ``app_key``, else None.

    Expiry and pending/decided state are left to the caller so it can answer
    an expired preview and an already decided one with different statuses.
    """
    record = db.scalar(
        select(PatchSessionRecord).where(
            PatchSessionRecord.session_id == session_id
        )
    )
    if record is None or record.app_key != app_key:
        return None
    return record


def is_expired(record: PatchSessionRecord) -> bool:
    created_at = record.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - created_at > SESSION_TTL


def load_payload(record: PatchSessionRecord) -> PatchSessionPayload:
    return PatchSessionPayload.model_validate(record.payload)


def mark_decided(db: Session, record: PatchSessionRecord, status: str) -> None:
    """Flip a pending session to confirmed/rejected exactly once."""
    record.status = status
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


__all__ = [
    "SESSION_TTL",
    "STATUS_CONFIRMED",
    "STATUS_PENDING",
    "STATUS_REJECTED",
    "PatchSessionPayload",
    "create_session",
    "get_session",
    "is_expired",
    "load_payload",
    "mark_decided",
]
