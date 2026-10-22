"""Append-only audit trail for important operations on a generated system.

Entries are written after the operation itself has committed. A failing audit
write is logged and never turns a successful operation into a 500: the data is
already saved by then, so surfacing an error would only confuse the user.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.auth.permissions import Actor
from backend.logging_config import get_logger
from backend.storage.models import AuditLogRecord

logger = get_logger("audit")

# Stable action ids: the frontend maps them to labels, so adding an operation
# never needs to reword existing history.
ACTION_APP_CREATE = "app.create"
ACTION_MODEL_PATCH = "model.patch"
ACTION_MODEL_RELATION = "model.relation"
ACTION_MODEL_ROLLBACK = "model.rollback"
ACTION_DATA_APPEND = "data.append"
ACTION_DATA_ROLLBACK = "data.rollback"
ACTION_RECORD_CREATE = "record.create"
ACTION_RECORD_UPDATE = "record.update"
ACTION_RECORD_DELETE = "record.delete"
ACTION_APP_PUBLISH = "app.publish"
ACTION_APP_UNPUBLISH = "app.unpublish"
ACTION_MEMBER_ADD = "member.add"
ACTION_MEMBER_UPDATE = "member.update"
ACTION_MEMBER_REMOVE = "member.remove"


def record(
    db: Session,
    *,
    app_key: str,
    actor: Actor,
    action: str,
    summary: str,
    detail: dict[str, Any] | None = None,
) -> None:
    try:
        db.add(
            AuditLogRecord(
                app_key=app_key,
                user_id=actor.user_id,
                actor=actor.label,
                action=action,
                summary=summary,
                detail=detail,
            )
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.warning(
            "审计日志写入失败：app=%s action=%s actor=%s",
            app_key,
            action,
            actor.label,
            exc_info=True,
        )


def list_entries(
    db: Session, app_key: str, *, limit: int = 50, offset: int = 0
) -> tuple[list[AuditLogRecord], int]:
    total = int(
        db.scalar(
            select(func.count(AuditLogRecord.id)).where(
                AuditLogRecord.app_key == app_key
            )
        )
        or 0
    )
    rows = db.scalars(
        select(AuditLogRecord)
        .where(AuditLogRecord.app_key == app_key)
        .order_by(AuditLogRecord.created_at.desc(), AuditLogRecord.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total
