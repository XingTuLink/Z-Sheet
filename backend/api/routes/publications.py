"""Publish apps under custom public slugs (Day 33).

Two surfaces:

* management endpoints under ``/api/v1/models/{app_key}/publication`` used by
  the owner UI (create / inspect / change slug / re-publish / unpublish);
* the public, strictly READ-ONLY endpoint ``/api/v1/p/{slug}/bootstrap``.
  No mutation endpoint exists under ``/p`` — the public page cannot write data
  even if crafted requests are sent. The future users system protects the
  management endpoints; the public surface needs no authentication by design.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend import audit
from backend.api import runtime_views
from backend.api.runtime_views import ListParams, list_params
from backend.api.schemas import (
    PublicationRequest,
    PublicationResponse,
    PublicationUpdateRequest,
    RuntimeBootstrapResponse,
)
from backend.auth.permissions import Actor, require_editor, require_viewer
from backend.domain.models import BusinessModel
from backend.logging_config import get_logger
from backend.runtime import record_io
from backend.storage.db import get_db
from backend.storage.models import AppPublicationRecord
from backend.storage.repositories import model_repository
from backend.storage.repositories import publication_repository as pubs

logger = get_logger("publication")

management_router = APIRouter(prefix="/api/v1/models", tags=["publications"])
public_router = APIRouter(prefix="/api/v1/p", tags=["public"])


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _serialize(db: Session, record: AppPublicationRecord) -> dict[str, Any]:
    current = model_repository.get_current(
        db, record.app_key, raise_if_missing=False
    )
    return {
        "app_key": record.app_key,
        "slug": record.slug,
        "status": record.status,
        "model_version": record.model_version,
        "current_version": current.version if current else record.model_version,
        "public": record.status == pubs.STATUS_PUBLISHED,
        "published_at": record.published_at,
    }


@management_router.get(
    "/{app_key}/publication",
    response_model=PublicationResponse,
)
def get_publication(
    app_key: str,
    actor: Actor = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Any:
    record = pubs.get_for_app(db, app_key)
    if record is None:
        return _error(status.HTTP_404_NOT_FOUND, "该应用尚未发布")
    return _serialize(db, record)


@management_router.post(
    "/{app_key}/publication",
    response_model=PublicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_publication(
    app_key: str,
    payload: PublicationRequest,
    actor: Actor = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Any:
    try:
        record = pubs.create_publication(db, app_key, payload.slug)
    except pubs.SlugError as exc:
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except (pubs.PublicationExistsError, pubs.SlugConflictError) as exc:
        return _error(status.HTTP_409_CONFLICT, str(exc))
    except model_repository.AppNotFoundError as exc:
        return _error(status.HTTP_404_NOT_FOUND, str(exc))
    db.commit()
    audit.record(
        db,
        app_key=app_key,
        actor=actor,
        action=audit.ACTION_APP_PUBLISH,
        summary=f"发布公开链接 /p/{record.slug}（v{record.model_version}）",
        detail={"slug": record.slug, "model_version": record.model_version},
    )
    return _serialize(db, record)


@management_router.put(
    "/{app_key}/publication",
    response_model=PublicationResponse,
)
def update_publication(
    app_key: str,
    payload: PublicationUpdateRequest,
    actor: Actor = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Any:
    if payload.slug is None and not payload.republish:
        return _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "未提供任何更新内容（需要新的链接标识或更新发布）",
        )
    record = pubs.get_for_app(db, app_key)
    if record is None:
        return _error(status.HTTP_404_NOT_FOUND, "该应用尚未发布")
    try:
        if payload.slug is not None:
            pubs.change_slug(db, record, payload.slug)
        if payload.republish:
            pubs.republish(db, record)
    except pubs.SlugError as exc:
        db.rollback()
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except pubs.SlugConflictError as exc:
        db.rollback()
        return _error(status.HTTP_409_CONFLICT, str(exc))
    except model_repository.AppNotFoundError as exc:
        db.rollback()
        return _error(status.HTTP_404_NOT_FOUND, str(exc))
    db.commit()
    audit.record(
        db,
        app_key=app_key,
        actor=actor,
        action=audit.ACTION_APP_PUBLISH,
        summary=f"更新公开链接 /p/{record.slug}（v{record.model_version}）",
        detail={"slug": record.slug, "model_version": record.model_version},
    )
    return _serialize(db, record)


@management_router.delete(
    "/{app_key}/publication",
    response_model=PublicationResponse,
)
def unpublish_publication(
    app_key: str,
    actor: Actor = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Any:
    record = pubs.get_for_app(db, app_key)
    if record is None:
        return _error(status.HTTP_404_NOT_FOUND, "该应用尚未发布")
    pubs.unpublish(db, record)
    db.commit()
    logger.info("发布下线接口被调用：app=%s", app_key)
    audit.record(
        db,
        app_key=app_key,
        actor=actor,
        action=audit.ACTION_APP_UNPUBLISH,
        summary=f"下线公开链接 /p/{record.slug}",
        detail={"slug": record.slug},
    )
    return _serialize(db, record)


def public_context(db: Session, slug: str) -> tuple[str, BusinessModel]:
    """Resolve a published slug to its live records and pinned model."""
    record = pubs.get_published_by_slug(db, slug)
    if record is None:
        # Same 404 for never-existed and unpublished: no enumeration hint.
        raise record_io.RecordNotFound("发布链接不存在或已下线")
    try:
        _, model = record_io.load_pinned_model(
            db, record.app_key, record.model_version
        )
    except model_repository.AppNotFoundError as exc:
        logger.warning("发布引用的版本缺失：app=%s v=%s", record.app_key, exc)
        raise record_io.RecordNotFound("发布内容暂不可用") from exc
    return record.app_key, model


@public_router.get(
    "/{slug}/bootstrap",
    response_model=RuntimeBootstrapResponse,
)
def public_bootstrap(slug: str, db: Session = Depends(get_db)) -> Any:
    """Read-only render payload for visitors of a published link."""
    record = pubs.get_published_by_slug(db, slug)
    if record is None:
        # Same 404 for never-existed and unpublished: no enumeration hint.
        return _error(status.HTTP_404_NOT_FOUND, "发布链接不存在或已下线")
    try:
        version, _ = record_io.load_pinned_model(
            db, record.app_key, record.model_version
        )
    except model_repository.AppNotFoundError as exc:
        logger.warning("发布引用的版本缺失：app=%s v=%s", record.app_key, exc)
        return _error(status.HTTP_404_NOT_FOUND, "发布内容暂不可用")
    return {
        "app_key": record.app_key,
        "version": version.version,
        "model": version.snapshot,
    }


@public_router.get("/{slug}/entities/{entity_key}/rows")
def public_rows(
    slug: str,
    entity_key: str,
    params: ListParams = Depends(list_params),
    db: Session = Depends(get_db),
) -> Any:
    return runtime_views.list_rows(db, public_context, slug, entity_key, params)


@public_router.get("/{slug}/entities/{entity_key}/rows/{row_key}")
def public_row(
    slug: str, entity_key: str, row_key: str, db: Session = Depends(get_db)
) -> Any:
    return runtime_views.get_row(db, public_context, slug, entity_key, row_key)


@public_router.get("/{slug}/entities/{entity_key}/related")
def public_related_rows(
    slug: str,
    entity_key: str,
    parent_entity: str = Query(...),
    link: str = Query(...),
    parent: str = Query(...),
    limit: int = Query(5, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Any:
    return runtime_views.related(
        db,
        public_context,
        slug,
        parent_entity=parent_entity,
        link=link,
        parent=parent,
        limit=limit,
        offset=offset,
    )


@public_router.get("/{slug}/dashboard")
def public_dashboard(slug: str, db: Session = Depends(get_db)) -> Any:
    return runtime_views.dashboard(db, public_context, slug)
