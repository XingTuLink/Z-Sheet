"""Model import/export and version history endpoints (Day 3)."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from backend import audit
from backend.api.schemas import (
    ModelImportRequest,
    PatchSubmitRequest,
    RollbackRequest,
    VersionDetail,
    VersionMeta,
)
from backend.auth import auth_repository as auth_repo
from backend.auth.permissions import Actor, current_actor, require_editor, require_viewer
from backend.domain.serialization import model_to_yaml
from backend.logging_config import get_logger
from backend.patch.apply import PatchError
from backend.storage.db import get_db
from backend.storage.models import ModelVersionRecord
from backend.storage.repositories import model_repository as repo

router = APIRouter(prefix="/api/v1/models", tags=["models"])

logger = get_logger("models")


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
def import_model(
    body: ModelImportRequest,
    actor: Actor = Depends(current_actor),
    db: Session = Depends(get_db),
) -> Any:
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
    if actor.user_id is not None:
        auth_repo.ensure_owner(db, body.app_key, actor.user_id)
        app_name = str(
            (record.snapshot.get("app") or {}).get("name") or body.app_key
        )
        audit.record(
            db,
            app_key=body.app_key,
            actor=actor,
            action=audit.ACTION_APP_CREATE,
            summary=f"导入系统「{app_name}」（v{record.version}）",
            detail={"version": record.version},
        )
    return _version_detail(record)


@router.get("/{app_key}", response_model=VersionDetail)
def get_current_model(
    app_key: str,
    actor: Actor = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Any:
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
    actor: Actor = Depends(require_viewer),
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
def list_versions(
    app_key: str,
    actor: Actor = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Any:
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
def get_version(
    app_key: str,
    version: int,
    actor: Actor = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Any:
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
    "/{app_key}/undo",
    status_code=status.HTTP_201_CREATED,
    response_model=VersionDetail,
)
def undo_latest_change(
    app_key: str,
    body: RollbackRequest,
    actor: Actor = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Any:
    """Undo the latest change: restore the previous version (append-only)."""
    try:
        current = repo.get_current(db, app_key)
    except repo.AppNotFoundError:
        return JSONResponse(
            status_code=404, content={"detail": f"app {app_key!r} not found"}
        )
    except ValueError as exc:  # malformed app_key
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    if current.version <= 1:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "当前已是最早版本，没有可撤销的修改"},
        )
    try:
        record = repo.restore_version(
            db, app_key, current.version - 1, operator=body.operator
        )
    except (repo.AppNotFoundError, repo.VersionConflictError) as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    logger.info(
        "撤销最新修改：app=%s v%d→v%d（operator=%s）",
        app_key,
        current.version,
        record.version,
        body.operator,
    )
    audit.record(
        db,
        app_key=app_key,
        actor=actor,
        action=audit.ACTION_MODEL_ROLLBACK,
        summary=f"撤销最新修改：v{current.version}→v{record.version}",
        detail={"from": current.version, "to": record.version, "target": current.version - 1},
    )
    return _version_detail(record)


@router.post(
    "/{app_key}/versions/{version:int}/rollback",
    status_code=status.HTTP_201_CREATED,
    response_model=VersionDetail,
)
def rollback_to_version(
    app_key: str,
    version: int,
    body: RollbackRequest,
    actor: Actor = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Any:
    """Rollback: append a new version equal to any historical snapshot."""
    try:
        current = repo.get_current(db, app_key)
    except repo.AppNotFoundError:
        return JSONResponse(
            status_code=404, content={"detail": f"app {app_key!r} not found"}
        )
    except ValueError as exc:  # malformed app_key
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    if version == current.version:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "目标版本就是当前版本，无需回滚"},
        )
    try:
        record = repo.restore_version(db, app_key, version, operator=body.operator)
    except repo.AppNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"detail": f"version {version} of app {app_key!r} not found"},
        )
    except repo.VersionConflictError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    logger.info(
        "回滚到历史版本：app=%s v%d→v%d（目标快照 v%d，operator=%s）",
        app_key,
        current.version,
        record.version,
        version,
        body.operator,
    )
    audit.record(
        db,
        app_key=app_key,
        actor=actor,
        action=audit.ACTION_MODEL_ROLLBACK,
        summary=f"回滚到历史版本 v{version}（当前 v{record.version}）",
        detail={"from": current.version, "to": record.version, "target": version},
    )
    return _version_detail(record)


@router.post(
    "/{app_key}/patches",
    status_code=status.HTTP_201_CREATED,
    response_model=VersionDetail,
)
def submit_patch(
    app_key: str,
    body: PatchSubmitRequest,
    actor: Actor = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Any:
    """Raw JSON Patch write path.

    The UI edits through the preview/confirm sessions (see ``routes.patch``);
    this endpoint is the low-level write API, so it has no review gate and must
    enforce design 8.3 layer 3 itself.
    """
    try:
        record = repo.apply_patch_as_new_version(
            db,
            app_key,
            body.ops,
            source_request=body.source_request,
            operator=body.operator,
            enforce_business_safety=True,
        )
    except repo.AppNotFoundError:
        return JSONResponse(
            status_code=404, content={"detail": f"app {app_key!r} not found"}
        )
    except repo.BusinessSafetyError as exc:
        logger.warning(
            "直写 Patch 被业务安全拦截：app=%s 风险=%s",
            app_key,
            [risk.category for risk in exc.risks],
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "detail": str(exc),
                "risks": [
                    {"category": risk.category, "message": risk.message}
                    for risk in exc.risks
                ],
            },
        )
    except repo.VersionConflictError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)}
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
    logger.info(
        "直写 Patch 生成新版本：app=%s v%d（ops=%d，operator=%s）",
        app_key,
        record.version,
        len(body.ops),
        body.operator,
    )
    audit.record(
        db,
        app_key=app_key,
        actor=actor,
        action=audit.ACTION_MODEL_PATCH,
        summary=f"模型更新到 v{record.version}：" + (body.source_request or "直接应用修改"),
        detail={"version": record.version, "ops": len(body.ops)},
    )
    return _version_detail(record)
