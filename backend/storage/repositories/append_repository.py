"""Persistence for append-data batches (preview, apply, rollback).

The preview payload is stored server-side under a random id, so Confirm only
receives that id plus the duplicate-key strategy — never row data it could
tamper with. ``result`` keeps the inserted row keys and the previous values of
overwritten rows, which is what makes a full rollback possible.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from backend.storage.models import AppendBatchRecord


def new_batch_id() -> str:
    return secrets.token_urlsafe(18)


def create(
    db: Session,
    *,
    app_key: str,
    entity_key: str,
    base_version: int,
    payload: dict[str, Any],
) -> AppendBatchRecord:
    record = AppendBatchRecord(
        id=new_batch_id(),
        app_key=app_key,
        entity_key=entity_key,
        base_version=base_version,
        status="pending",
        payload=payload,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get(db: Session, batch_id: str) -> AppendBatchRecord | None:
    return db.get(AppendBatchRecord, batch_id)


def list_for_app(
    db: Session, app_key: str, *, limit: int = 20
) -> Sequence[AppendBatchRecord]:
    return db.scalars(
        select(AppendBatchRecord)
        .where(AppendBatchRecord.app_key == app_key)
        .order_by(AppendBatchRecord.created_at.desc())
        .limit(limit)
    ).all()


def transition(
    db: Session, batch_id: str, *, expected: str, new_status: str
) -> bool:
    """Atomically move one batch between statuses (CAS claim).

    Returns False when another request already moved it, so a double confirm
    or double rollback cannot run twice.
    """
    result = db.execute(
        update(AppendBatchRecord)
        .where(
            AppendBatchRecord.id == batch_id,
            AppendBatchRecord.status == expected,
        )
        .values(status=new_status, updated_at=datetime.now(UTC))
    )
    db.commit()
    return bool(cast(CursorResult[Any], result).rowcount)


def mark_applied(
    db: Session,
    record: AppendBatchRecord,
    *,
    strategy: str,
    new_version: int,
    result: dict[str, Any],
) -> None:
    record.strategy = strategy
    record.new_version = new_version
    record.result = result
    record.status = "applied"
    record.updated_at = datetime.now(UTC)
    db.add(record)


def mark_rolled_back(db: Session, record: AppendBatchRecord) -> None:
    record.status = "rolled_back"
    record.updated_at = datetime.now(UTC)
    db.add(record)


__all__ = [
    "create",
    "get",
    "list_for_app",
    "mark_applied",
    "mark_rolled_back",
    "new_batch_id",
    "transition",
]
