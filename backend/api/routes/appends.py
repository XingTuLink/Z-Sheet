"""Append Excel/CSV rows to an entity of the generated system.

Preview caches the parsed rows server-side and returns what will happen;
confirm applies the chosen duplicate strategy and any new fields; rollback
restores the batch's inserts and overwrites. The client never sends row data
back, so it cannot tamper with what it previewed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import audit
from backend.auth import auth_repository as auth_repo
from backend.auth import permissions
from backend.ingestion.parser import MAX_UPLOAD_BYTES
from backend.logging_config import get_logger
from backend.storage.db import get_db
from backend.storage.repositories import append_repository, model_repository
from backend.understanding import append_import
from backend.understanding.confirmation import WORKSPACE_APP_KEY

router = APIRouter(prefix="/api/v1/ingestion", tags=["appends"])

logger = get_logger("appends")

MAX_UPLOAD_MB = MAX_UPLOAD_BYTES // (1024 * 1024)


class AppendConfirmRequest(BaseModel):
    batch_id: str
    strategy: str = "skip"


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": message})


@router.post("/appends/preview", response_model=append_import.AppendPreviewResult)
async def preview_append(
    request: Request,
    file: UploadFile = File(...),
    entity_key: str = Form(...),
    app_key: str = Form(WORKSPACE_APP_KEY),
    sheet_name: str | None = Form(None),
    db: Session = Depends(get_db),
) -> append_import.AppendPreviewResult | JSONResponse:
    permissions.ensure_access(db, request, app_key, auth_repo.ROLE_EDITOR)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return _error(
            f"文件超过 {MAX_UPLOAD_MB} MB 上限，请拆分或精简后再上传",
            status.HTTP_413_CONTENT_TOO_LARGE,
        )
    try:
        return append_import.preview_append(
            db,
            app_key=app_key,
            entity_key=entity_key,
            filename=file.filename or "upload",
            content=content,
            sheet_name=sheet_name or None,
        )
    except append_import.AppendError as exc:
        logger.info("追加预览被拒（%s）：%s", file.filename, exc)
        return _error(str(exc), exc.status_code)
    except ValueError as exc:
        return _error(str(exc), status.HTTP_422_UNPROCESSABLE_CONTENT)


@router.post("/appends/confirm", response_model=append_import.AppendResult)
def confirm_append(
    payload: AppendConfirmRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> append_import.AppendResult | JSONResponse:
    batch = append_repository.get(db, payload.batch_id)
    if batch is None:
        return _error("追加批次不存在或已过期", status.HTTP_404_NOT_FOUND)
    actor = permissions.ensure_access(
        db, request, batch.app_key, auth_repo.ROLE_EDITOR
    )
    try:
        result = append_import.confirm_append(db, payload.batch_id, payload.strategy)
    except append_import.AppendError as exc:
        logger.warning("追加确认被拒（批次=%s）：%s", payload.batch_id, exc)
        return _error(str(exc), exc.status_code)
    except model_repository.VersionConflictError as exc:
        logger.warning("追加确认时版本冲突（批次=%s）：%s", payload.batch_id, exc)
        return _error(str(exc), status.HTTP_409_CONFLICT)
    audit.record(
        db,
        app_key=batch.app_key,
        actor=actor,
        action=audit.ACTION_DATA_APPEND,
        summary=(
            f"追加数据到 {result.entity_key}：新增 {result.inserted} 行"
            f"（覆盖 {result.overwritten}、跳过 {result.skipped}，v{result.version}）"
        ),
        detail={
            "batch_id": result.batch_id,
            "entity": result.entity_key,
            "inserted": result.inserted,
            "overwritten": result.overwritten,
            "skipped": result.skipped,
            "version": result.version,
        },
    )
    return result


@router.post(
    "/appends/{batch_id}/rollback", response_model=append_import.AppendResult
)
def rollback_append(
    batch_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> append_import.AppendResult | JSONResponse:
    batch = append_repository.get(db, batch_id)
    if batch is None:
        return _error("追加批次不存在或已过期", status.HTTP_404_NOT_FOUND)
    actor = permissions.ensure_access(
        db, request, batch.app_key, auth_repo.ROLE_EDITOR
    )
    try:
        result = append_import.rollback_append(db, batch_id)
    except append_import.AppendError as exc:
        logger.warning("追加回滚被拒（批次=%s）：%s", batch_id, exc)
        return _error(str(exc), exc.status_code)
    except model_repository.VersionConflictError as exc:
        logger.warning("追加回滚时版本冲突（批次=%s）：%s", batch_id, exc)
        return _error(str(exc), status.HTTP_409_CONFLICT)
    audit.record(
        db,
        app_key=batch.app_key,
        actor=actor,
        action=audit.ACTION_DATA_ROLLBACK,
        summary=f"回滚追加批次（{result.entity_key}，v{result.version}）",
        detail={"batch_id": batch_id, "entity": result.entity_key},
    )
    return result


@router.get("/appends", response_model=list[append_import.AppendBatchMeta])
def list_appends(
    request: Request,
    app_key: str = WORKSPACE_APP_KEY,
    db: Session = Depends(get_db),
) -> list[append_import.AppendBatchMeta] | JSONResponse:
    permissions.ensure_access(db, request, app_key, auth_repo.ROLE_VIEWER)
    try:
        return append_import.list_batches(db, app_key)
    except ValueError as exc:
        return _error(str(exc), status.HTTP_422_UNPROCESSABLE_CONTENT)


__all__ = ["router"]
