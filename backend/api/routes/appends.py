"""Append Excel/CSV rows to an entity of the generated system.

Preview caches the parsed rows server-side and returns what will happen;
confirm applies the chosen duplicate strategy and any new fields; rollback
restores the batch's inserts and overwrites. The client never sends row data
back, so it cannot tamper with what it previewed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.ingestion.parser import MAX_UPLOAD_BYTES
from backend.logging_config import get_logger
from backend.storage.db import get_db
from backend.storage.repositories import model_repository
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
    file: UploadFile = File(...),
    entity_key: str = Form(...),
    app_key: str = Form(WORKSPACE_APP_KEY),
    sheet_name: str | None = Form(None),
    db: Session = Depends(get_db),
) -> append_import.AppendPreviewResult | JSONResponse:
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
    db: Session = Depends(get_db),
) -> append_import.AppendResult | JSONResponse:
    try:
        return append_import.confirm_append(db, payload.batch_id, payload.strategy)
    except append_import.AppendError as exc:
        logger.warning("追加确认被拒（批次=%s）：%s", payload.batch_id, exc)
        return _error(str(exc), exc.status_code)
    except model_repository.VersionConflictError as exc:
        logger.warning("追加确认时版本冲突（批次=%s）：%s", payload.batch_id, exc)
        return _error(str(exc), status.HTTP_409_CONFLICT)


@router.post(
    "/appends/{batch_id}/rollback", response_model=append_import.AppendResult
)
def rollback_append(
    batch_id: str,
    db: Session = Depends(get_db),
) -> append_import.AppendResult | JSONResponse:
    try:
        return append_import.rollback_append(db, batch_id)
    except append_import.AppendError as exc:
        logger.warning("追加回滚被拒（批次=%s）：%s", batch_id, exc)
        return _error(str(exc), exc.status_code)
    except model_repository.VersionConflictError as exc:
        logger.warning("追加回滚时版本冲突（批次=%s）：%s", batch_id, exc)
        return _error(str(exc), status.HTTP_409_CONFLICT)


@router.get("/appends", response_model=list[append_import.AppendBatchMeta])
def list_appends(
    app_key: str = WORKSPACE_APP_KEY,
    db: Session = Depends(get_db),
) -> list[append_import.AppendBatchMeta] | JSONResponse:
    try:
        return append_import.list_batches(db, app_key)
    except ValueError as exc:
        return _error(str(exc), status.HTTP_422_UNPROCESSABLE_CONTENT)


__all__ = ["router"]
