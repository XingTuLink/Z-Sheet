"""System catalogue and per-app overview for the workbench and admin pages.

Both endpoints are read-only and derive their data from tables that already
exist (model versions, business rows, publications, append batches), so no
extra activity log is introduced. Counts, current versions and timestamps are
computed in SQL rather than by loading whole JSON documents.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from backend.auth import auth_repository as auth_repo
from backend.auth import permissions
from backend.auth.permissions import Actor, require_viewer
from backend.logging_config import get_logger
from backend.storage.db import get_db
from backend.storage.models import (
    AppPublicationRecord,
    BusinessRecord,
    ModelVersionRecord,
)
from backend.storage.repositories import append_repository, model_repository
from backend.storage.repositories import publication_repository as pubs

router = APIRouter(prefix="/api/v1/apps", tags=["apps"])

logger = get_logger("apps")

RECENT_CHANGES_LIMIT = 10


class PublicationInfo(BaseModel):
    """Public-link state of one app (None when it was never published)."""

    status: str
    slug: str
    public: bool
    model_version: int
    published_at: datetime | None = None


class AppSummary(BaseModel):
    """One card on the workbench."""

    app_key: str
    name: str
    entity_count: int
    record_count: int
    current_version: int
    publication: PublicationInfo | None = None
    updated_at: datetime


class EntitySummary(BaseModel):
    entity_key: str
    name: str
    record_count: int


class ChangeEvent(BaseModel):
    kind: Literal["model", "append", "publication"]
    summary: str
    version: int | None = None
    status: str | None = None
    at: datetime


class AppOverview(BaseModel):
    """Per-app detail for the admin overview page."""

    app_key: str
    name: str
    entity_count: int
    record_count: int
    current_version: int
    version_count: int
    publication: PublicationInfo | None = None
    entities: list[EntitySummary]
    recent_changes: list[ChangeEvent]
    updated_at: datetime


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; compare and serialize them as UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _publication_info(record: AppPublicationRecord | None) -> PublicationInfo | None:
    if record is None:
        return None
    return PublicationInfo(
        status=record.status,
        slug=record.slug,
        public=record.status == pubs.STATUS_PUBLISHED,
        model_version=record.model_version,
        published_at=_as_utc(record.published_at),
    )


def _latest_versions(db: Session) -> list[ModelVersionRecord]:
    """Current version of every app in one query."""
    latest = (
        select(
            ModelVersionRecord.app_key,
            func.max(ModelVersionRecord.version).label("version"),
        )
        .group_by(ModelVersionRecord.app_key)
        .subquery()
    )
    return list(
        db.scalars(
            select(ModelVersionRecord).join(
                latest,
                and_(
                    ModelVersionRecord.app_key == latest.c.app_key,
                    ModelVersionRecord.version == latest.c.version,
                ),
            )
        ).all()
    )


def _record_stats(db: Session) -> dict[str, tuple[int, datetime | None]]:
    rows = db.execute(
        select(
            BusinessRecord.app_key,
            func.count(BusinessRecord.id),
            func.max(BusinessRecord.updated_at),
        ).group_by(BusinessRecord.app_key)
    ).all()
    return {
        str(app_key): (int(count), _as_utc(updated))
        for app_key, count, updated in rows
    }


def _publications(db: Session) -> dict[str, AppPublicationRecord]:
    return {
        record.app_key: record
        for record in db.scalars(select(AppPublicationRecord)).all()
    }


def _updated_at(
    version: ModelVersionRecord,
    record_stat: tuple[int, datetime | None] | None,
    publication: AppPublicationRecord | None,
) -> datetime:
    candidates = [_as_utc(version.created_at) or datetime.now(UTC)]
    if record_stat is not None and record_stat[1] is not None:
        candidates.append(record_stat[1])
    if publication is not None:
        candidates.append(_as_utc(publication.updated_at) or candidates[0])
    return max(candidates)


def _entity_names(snapshot: dict[str, Any]) -> dict[str, str]:
    entities = snapshot.get("entities") or []
    return {
        str(entity.get("key")): str(entity.get("name"))
        for entity in entities
        if isinstance(entity, dict)
    }


def _model_event(record: ModelVersionRecord) -> ChangeEvent:
    if record.version == 1 and record.patch is None:
        summary = "生成系统（v1）"
    elif record.patch is not None:
        detail = (record.source_request or "").strip() or "模型修改"
        summary = f"模型更新到 v{record.version}：{detail}"
    else:
        detail = (record.source_request or "").strip() or "模型更新"
        summary = f"{detail}（v{record.version}）"
    return ChangeEvent(
        kind="model",
        summary=summary,
        version=record.version,
        at=_as_utc(record.created_at) or datetime.now(UTC),
    )


APPEND_STATUS_LABELS = {
    "pending": "预览待确认",
    "applying": "正在追加",
    "applied": "已追加",
    "rolled_back": "已回滚",
}


def _append_event(record: Any, entity_names: dict[str, str]) -> ChangeEvent:
    entity_name = entity_names.get(record.entity_key, record.entity_key)
    file_name = str((record.payload or {}).get("file_name") or "").strip()
    label = APPEND_STATUS_LABELS.get(record.status, record.status)
    detail = f"追加数据到「{entity_name}」"
    if file_name:
        detail = f"{detail}：{file_name}"
    return ChangeEvent(
        kind="append",
        summary=f"{detail}（{label}）",
        version=record.new_version,
        status=record.status,
        at=_as_utc(record.created_at) or datetime.now(UTC),
    )


def _publication_event(record: AppPublicationRecord) -> ChangeEvent:
    if record.status == pubs.STATUS_PUBLISHED:
        summary = f"发布公开链接 /p/{record.slug}"
    else:
        summary = f"下线公开链接 /p/{record.slug}"
    return ChangeEvent(
        kind="publication",
        summary=summary,
        status=record.status,
        at=_as_utc(record.updated_at) or datetime.now(UTC),
    )


def _recent_changes(db: Session, app_key: str, entity_names: dict[str, str]) -> list[ChangeEvent]:
    events: list[ChangeEvent] = [
        _model_event(record)
        for record in db.scalars(
            select(ModelVersionRecord)
            .where(ModelVersionRecord.app_key == app_key)
            .order_by(ModelVersionRecord.version.desc())
            .limit(RECENT_CHANGES_LIMIT)
        ).all()
    ]
    events.extend(
        _append_event(record, entity_names)
        for record in append_repository.list_for_app(
            db, app_key, limit=RECENT_CHANGES_LIMIT
        )
    )
    publication = pubs.get_for_app(db, app_key)
    if publication is not None:
        events.append(_publication_event(publication))
    events.sort(key=lambda event: event.at, reverse=True)
    return events[:RECENT_CHANGES_LIMIT]


@router.get("", response_model=list[AppSummary])
def list_apps(
    request: Request, db: Session = Depends(get_db)
) -> list[AppSummary]:
    """Every generated system with the counters shown on a workbench card.

    Signed-in users only see systems they are a member of, plus systems that
    were created before accounts existed (no membership rows) so nothing
    disappears after the first registration.
    """
    actor = permissions.resolve_actor(request, db)
    versions = _latest_versions(db)
    allowed = permissions.accessible_app_keys(db, actor)
    if allowed is not None:
        unclaimed = {version.app_key for version in versions} - auth_repo.claimed_app_keys(db)
        allowed = allowed | unclaimed
        versions = [version for version in versions if version.app_key in allowed]
    stats = _record_stats(db)
    publication_map = _publications(db)
    return [
        AppSummary(
            app_key=version.app_key,
            name=str((version.snapshot.get("app") or {}).get("name") or version.app_key),
            entity_count=len(version.snapshot.get("entities") or []),
            record_count=stats.get(version.app_key, (0, None))[0],
            current_version=version.version,
            publication=_publication_info(publication_map.get(version.app_key)),
            updated_at=_updated_at(
                version, stats.get(version.app_key), publication_map.get(version.app_key)
            ),
        )
        for version in versions
    ]


@router.get("/{app_key}/overview", response_model=AppOverview)
def app_overview(
    app_key: str,
    actor: Actor = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> AppOverview | JSONResponse:
    """Entity counts, version history size and recent changes of one app."""
    try:
        current = model_repository.get_current(db, app_key, raise_if_missing=False)
    except ValueError as exc:
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    if current is None:
        return _error(status.HTTP_404_NOT_FOUND, f"系统 {app_key} 不存在")

    snapshot = current.snapshot
    names = _entity_names(snapshot)
    counts = {
        str(entity_key): int(count)
        for entity_key, count in db.execute(
            select(BusinessRecord.entity_key, func.count(BusinessRecord.id))
            .where(BusinessRecord.app_key == app_key)
            .group_by(BusinessRecord.entity_key)
        ).all()
    }
    entities = [
        EntitySummary(
            entity_key=str(entity.get("key")),
            name=str(entity.get("name")),
            record_count=counts.get(str(entity.get("key")), 0),
        )
        for entity in snapshot.get("entities") or []
        if isinstance(entity, dict)
    ]
    record_count, record_updated = db.execute(
        select(
            func.count(BusinessRecord.id),
            func.max(BusinessRecord.updated_at),
        ).where(BusinessRecord.app_key == app_key)
    ).one()
    record_count = int(record_count or 0)
    version_count = int(
        db.scalar(
            select(func.count(ModelVersionRecord.id)).where(
                ModelVersionRecord.app_key == app_key
            )
        )
        or 0
    )
    publication = pubs.get_for_app(db, app_key)
    return AppOverview(
        app_key=app_key,
        name=str((snapshot.get("app") or {}).get("name") or app_key),
        entity_count=len(entities),
        record_count=record_count,
        current_version=current.version,
        version_count=version_count,
        publication=_publication_info(publication),
        entities=entities,
        recent_changes=_recent_changes(db, app_key, names),
        updated_at=_updated_at(
            current, (record_count, _as_utc(record_updated)), publication
        ),
    )


__all__ = ["router"]
