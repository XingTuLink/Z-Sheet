"""Model import/export and version history endpoints (Day 3)."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from backend.api.schemas import (
    ModelImportRequest,
    PatchSubmitRequest,
    VersionDetail,
    VersionMeta,
)
from backend.domain.serialization import model_to_yaml
from backend.patch.apply import PatchError
from backend.storage.db import get_db
from backend.storage.models import ModelVersionRecord
from backend.storage.repositories import model_repository as repo

router = APIRouter(prefix="/api/v1/models", tags=["models"])


def _version_meta(record: ModelVersionRecord) -> VersionMeta:
    created_at = record.created_at
    if created_at.tzinfo is None:  # SQLite returns naive datetimes; stored as UTC
        created_at = created_at.replace(tzinfo=UTC)
    return VersionMeta(
        id=record.id,
        app_key=record.app_key,
        version=record.version,
        operator=record.operator,
        source_request=record.source_request,
        validation_status=record.validation_status,
        has_patch=record.patch is not None,
        created_at=created_at,
    )


def _version_detail(record: ModelVersionRecord) -> VersionDetail:
    meta = _version_meta(record).model_dump()
    return VersionDetail(
        **meta,
        snapshot=record.snapshot,
        patch=record.patch,
    )


def _validation_error_response(exc: PydanticValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "detail": "validation failed",
            # include_context=False drops non-serializable ctx error objects.
            "errors": exc.errors(include_context=False),
        },
    )


@router.post("/import", status_code=status.HTTP_201_CREATED, response_model=VersionDetail)
def import_model(body: ModelImportRequest, db: Session = Depends(get_db)) -> Any:
    try:
        record = repo.import_model(
            db, body.app_key, body.yaml_content, operator=body.operator
        )
    except repo.AppExistsError:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": f"app {body.app_key!r} already exists"},
        )
    except PydanticValidationError as exc:
        return _validation_error_response(exc)
    except ValueError as exc:  # malformed app_key
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    return _version_detail(record)


@router.get("/{app_key}", response_model=VersionDetail)
def get_current_model(app_key: str, db: Session = Depends(get_db)) -> Any:
    try:
        record = repo.get_current(db, app_key)
    except repo.AppNotFoundError:
        return JSONResponse(
            status_code=404, content={"detail": f"app {app_key!r} not found"}
        )
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    return _version_detail(record)


@router.get("/{app_key}/export")
def export_current_model(
    app_key: str,
    format: str = Query("yaml", pattern="^(yaml|json)$"),
    db: Session = Depends(get_db),
) -> Response:
    try:
        record = repo.get_current(db, app_key)
    except repo.AppNotFoundError:
        return JSONResponse(
            status_code=404, content={"detail": f"app {app_key!r} not found"}
        )
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    model = repo.snapshot_to_model(record)
    if format == "json":
        return JSONResponse(content=model.model_dump(mode="json"))
    yaml_text = model_to_yaml(model)
    return PlainTextResponse(
        yaml_text,
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{app_key}.model.yaml"'},
    )


@router.get("/{app_key}/versions", response_model=list[VersionMeta])
def list_versions(app_key: str, db: Session = Depends(get_db)) -> Any:
    try:
        records = repo.list_versions(db, app_key)
    except repo.AppNotFoundError:
        return JSONResponse(
            status_code=404, content={"detail": f"app {app_key!r} not found"}
        )
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    return [_version_meta(r) for r in records]


@router.get("/{app_key}/versions/{version:int}", response_model=VersionDetail)
def get_version(app_key: str, version: int, db: Session = Depends(get_db)) -> Any:
    try:
        record = repo.get_version(db, app_key, version)
    except repo.AppNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"detail": f"version {version} of app {app_key!r} not found"},
        )
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    return _version_detail(record)


@router.post(
    "/{app_key}/patches",
    status_code=status.HTTP_201_CREATED,
    response_model=VersionDetail,
)
def submit_patch(
    app_key: str, body: PatchSubmitRequest, db: Session = Depends(get_db)
) -> Any:
    try:
        record = repo.apply_patch_as_new_version(
            db,
            app_key,
            body.ops,
            source_request=body.source_request,
            operator=body.operator,
        )
    except repo.AppNotFoundError:
        return JSONResponse(
            status_code=404, content={"detail": f"app {app_key!r} not found"}
        )
    except (PydanticValidationError, PatchError) as exc:
        if isinstance(exc, PydanticValidationError):
            return _validation_error_response(exc)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": "patch could not be applied", "error": str(exc)},
        )
    except ValueError as exc:  # malformed app_key
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    return _version_detail(record)
