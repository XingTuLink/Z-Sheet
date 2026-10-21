"""Runtime bootstrap and row CRUD endpoints for the deterministic renderer."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend import audit
from backend.api import runtime_views
from backend.api.runtime_views import ListParams, list_params
from backend.api.schemas import (
    RecordMutationResponse,
    RecordPayload,
    RuntimeBootstrapResponse,
)
from backend.auth.permissions import Actor, require_editor, require_viewer
from backend.runtime import record_io
from backend.storage.db import get_db

router = APIRouter(prefix="/api/v1/runtime", tags=["runtime"])


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


@router.get(
    "/{app_key}/bootstrap",
    response_model=RuntimeBootstrapResponse,
)
def bootstrap(
    app_key: str,
    actor: Actor = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Any:
    try:
        record, _ = record_io.load_current_model(db, app_key)
    except record_io.RecordNotFound as exc:
        return _error(status.HTTP_404_NOT_FOUND, str(exc))
    except ValueError as exc:
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    return {
        "app_key": app_key,
        "version": record.version,
        "model": record.snapshot,
    }


@router.get("/{app_key}/entities/{entity_key}/rows")
def list_rows(
    app_key: str,
    entity_key: str,
    params: ListParams = Depends(list_params),
    actor: Actor = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Any:
    return runtime_views.list_rows(
        db, runtime_views.owner_context, app_key, entity_key, params
    )


@router.get("/{app_key}/entities/{entity_key}/rows/{row_key}")
def get_row(
    app_key: str,
    entity_key: str,
    row_key: str,
    actor: Actor = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Any:
    return runtime_views.get_row(
        db, runtime_views.owner_context, app_key, entity_key, row_key
    )


@router.get("/{app_key}/entities/{entity_key}/related")
def related_rows(
    app_key: str,
    entity_key: str,
    parent_entity: str = Query(...),
    link: str = Query(...),
    parent: str = Query(...),
    limit: int = Query(5, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor: Actor = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Any:
    return runtime_views.related(
        db,
        runtime_views.owner_context,
        app_key,
        parent_entity=parent_entity,
        link=link,
        parent=parent,
        limit=limit,
        offset=offset,
    )


@router.get("/{app_key}/dashboard")
def dashboard(
    app_key: str,
    actor: Actor = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Any:
    return runtime_views.dashboard(db, runtime_views.owner_context, app_key)


@router.post(
    "/{app_key}/entities/{entity_key}/records",
    response_model=RecordMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_record(
    app_key: str,
    entity_key: str,
    payload: RecordPayload,
    actor: Actor = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Any:
    try:
        key, row = record_io.create_row(db, app_key, entity_key, payload.row)
    except record_io.RecordNotFound as exc:
        return _error(status.HTTP_404_NOT_FOUND, str(exc))
    except record_io.RecordConflict as exc:
        return _error(status.HTTP_409_CONFLICT, str(exc))
    except ValueError as exc:
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    audit.record(
        db,
        app_key=app_key,
        actor=actor,
        action=audit.ACTION_RECORD_CREATE,
        summary=f"新增记录 {entity_key}/{key}",
        detail={"entity": entity_key, "row_key": key},
    )
    return {"app_key": app_key, "entity": entity_key, "key": key, "row": row}


@router.put(
    "/{app_key}/entities/{entity_key}/records/{row_key}",
    response_model=RecordMutationResponse,
)
def update_record(
    app_key: str,
    entity_key: str,
    row_key: str,
    payload: RecordPayload,
    actor: Actor = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Any:
    try:
        row = record_io.update_row(db, app_key, entity_key, row_key, payload.row)
    except record_io.RecordNotFound as exc:
        return _error(status.HTTP_404_NOT_FOUND, str(exc))
    except record_io.RecordConflict as exc:
        return _error(status.HTTP_409_CONFLICT, str(exc))
    except ValueError as exc:
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    audit.record(
        db,
        app_key=app_key,
        actor=actor,
        action=audit.ACTION_RECORD_UPDATE,
        summary=f"修改记录 {entity_key}/{row_key}",
        detail={"entity": entity_key, "row_key": row_key},
    )
    return {"app_key": app_key, "entity": entity_key, "key": row_key, "row": row}


@router.delete(
    "/{app_key}/entities/{entity_key}/records/{row_key}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_record(
    app_key: str,
    entity_key: str,
    row_key: str,
    actor: Actor = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Response:
    try:
        record_io.delete_row(db, app_key, entity_key, row_key)
    except record_io.RecordNotFound as exc:
        return _error(status.HTTP_404_NOT_FOUND, str(exc))
    except ValueError as exc:
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    audit.record(
        db,
        app_key=app_key,
        actor=actor,
        action=audit.ACTION_RECORD_DELETE,
        summary=f"删除记录 {entity_key}/{row_key}",
        detail={"entity": entity_key, "row_key": row_key},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
