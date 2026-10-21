"""Read the audit trail of one generated system."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import audit
from backend.auth.permissions import Actor, require_viewer
from backend.storage.db import get_db
from backend.storage.models import AuditLogRecord

router = APIRouter(prefix="/api/v1/apps", tags=["audit"])


class AuditEntry(BaseModel):
    id: int
    action: str
    actor: str
    user_id: int | None = None
    summary: str
    detail: dict[str, Any] | None = None
    at: datetime


class AuditPage(BaseModel):
    app_key: str
    items: list[AuditEntry]
    total: int
    limit: int
    offset: int


def _entry(record: AuditLogRecord) -> AuditEntry:
    at = record.created_at
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return AuditEntry(
        id=record.id,
        action=record.action,
        actor=record.actor,
        user_id=record.user_id,
        summary=record.summary,
        detail=record.detail,
        at=at,
    )


@router.get("/{app_key}/audit", response_model=AuditPage)
def list_audit(
    app_key: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor: Actor = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> AuditPage:
    rows, total = audit.list_entries(db, app_key, limit=limit, offset=offset)
    return AuditPage(
        app_key=app_key,
        items=[_entry(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
