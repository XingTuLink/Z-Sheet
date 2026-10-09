"""Runtime bootstrap and row CRUD endpoints for the deterministic renderer."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend.api.schemas import RecordMutationResponse, RecordPayload, RuntimeBootstrapResponse
from backend.runtime import record_io
from backend.storage.db import get_db

router = APIRouter(prefix="/api/v1/runtime", tags=["runtime"])


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


@router.get(
    "/{app_key}/bootstrap",
    response_model=RuntimeBootstrapResponse,
)
def bootstrap(app_key: str, db: Session = Depends(get_db)) -> Any:
    try:
        data = record_io.load_runtime_data(db, app_key)
    except record_io.RecordNotFound as exc:
        return _error(status.HTTP_404_NOT_FOUND, str(exc))
    except ValueError as exc:
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    return {
        "app_key": app_key,
        "version": data.record.version,
        "model": data.record.snapshot,
        "records": data.records,
    }


@router.post(
    "/{app_key}/entities/{entity_key}/records",
    response_model=RecordMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_record(
    app_key: str,
    entity_key: str,
    payload: RecordPayload,
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
    return {"app_key": app_key, "entity": entity_key, "key": row_key, "row": row}


@router.delete(
    "/{app_key}/entities/{entity_key}/records/{row_key}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_record(
    app_key: str,
    entity_key: str,
    row_key: str,
    db: Session = Depends(get_db),
) -> Response:
    try:
        record_io.delete_row(db, app_key, entity_key, row_key)
    except record_io.RecordNotFound as exc:
        return _error(status.HTTP_404_NOT_FOUND, str(exc))
    except ValueError as exc:
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
