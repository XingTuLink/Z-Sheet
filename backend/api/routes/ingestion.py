"""File ingestion endpoints (Day 5).

Stateless parse: the uploaded workbook is parsed and returned immediately,
nothing is persisted. Later understanding stages will consume this same
output in a pipeline before any storage decision is needed.

Day 12 adds ``/parse/stream``: the same understanding pipeline exposed as
Server-Sent Events so the UI shows real stage progress (parsed sheets,
recognized fields/entities/relations, assembling) instead of a spinner.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator
from typing import cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from backend.api.schemas import ConfirmResponse
from backend.ingestion.parser import MAX_UPLOAD_BYTES, ParseError, parse_workbook
from backend.ingestion.template import TEMPLATE_FILENAME, build_template_workbook
from backend.logging_config import get_logger
from backend.storage.db import SessionLocal, get_db
from backend.storage.repositories import model_repository, understanding_repository
from backend.understanding import confirmation, multi_confirmation, multi_import
from backend.understanding.multi_import import (
    MAX_FILES,
    MultiImportResult,
    MultiPreview,
)
from backend.understanding.profiling import STAGE_SHEETS, add_profiles
from backend.understanding.schemas import ProfiledParsedWorkbook

router = APIRouter(prefix="/api/v1/ingestion", tags=["ingestion"])

logger = get_logger("ingestion")

MAX_UPLOAD_MB = MAX_UPLOAD_BYTES // (1024 * 1024)
MAX_MULTI_UPLOAD_BYTES = MAX_UPLOAD_BYTES * 3

# Thread -> generator hand-off items. The understanding pipeline is a
# synchronous CPU-bound call, so it runs on a worker thread while this
# generator forwards stage frames the moment each stage finishes.
_WorkerItem = tuple[str, object]


def _sse(event: str, data: str) -> str:
    """Frame one Server-Sent Event; data must be a single-line JSON string."""
    return f"event: {event}\ndata: {data}\n\n"


def _too_large() -> JSONResponse:
    """Oversize upload: the message is user-facing, so keep it in Chinese."""
    return JSONResponse(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        content={"detail": f"文件超过 {MAX_UPLOAD_MB} MB 上限，请拆分或精简后再上传"},
    )


def _multi_too_large() -> JSONResponse:
    total_mb = MAX_MULTI_UPLOAD_BYTES // (1024 * 1024)
    return JSONResponse(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        content={"detail": f"多个文件合计超过 {total_mb} MB 上限，请分批导入"},
    )


def _app_exists(app_key: str | None) -> JSONResponse:
    """New-system generation collided with an existing app identifier."""
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": (
                f"系统标识「{app_key}」已存在；要给已有系统补充数据，请使用「追加数据」"
            )
        },
    )


async def _read_uploads(
    files: list[UploadFile],
) -> tuple[list[tuple[str, bytes]], JSONResponse | None]:
    """Read every upload, enforcing per-file and total size limits."""
    if not files:
        return [], JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": "请至少选择一个文件"},
        )
    if len(files) > MAX_FILES:
        return [], JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": f"一次最多上传 {MAX_FILES} 个文件，请分批导入"},
        )
    uploads: list[tuple[str, bytes]] = []
    total = 0
    for index, upload in enumerate(files):
        content = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            return [], _too_large()
        total += len(content)
        if total > MAX_MULTI_UPLOAD_BYTES:
            return [], _multi_too_large()
        uploads.append((upload.filename or f"upload-{index + 1}", content))
    return uploads, None


def _entity_count(result: ProfiledParsedWorkbook) -> int:
    """Entities in the assembled model (0 when nothing could be assembled)."""
    entities = result.business_model.get("entities") if result.business_model else None
    return len(entities) if isinstance(entities, list) else 0


@router.get("/template")
def download_template() -> Response:
    """Serve the standard .xlsx template offered when no entity is recognized."""
    buffer = build_template_workbook()
    encoded = quote(TEMPLATE_FILENAME)
    return Response(
        content=buffer.getvalue(),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            # ASCII fallback plus RFC 5987 UTF-8 filename for Chinese names.
            "Content-Disposition": (
                f"attachment; filename=\"template.xlsx\"; filename*=UTF-8''{encoded}"
            )
        },
    )


@router.post("/parse", response_model=ProfiledParsedWorkbook)
async def parse_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> ProfiledParsedWorkbook | JSONResponse:
    # Read one byte past the limit to detect oversize uploads deterministically.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return _too_large()
    try:
        workbook = parse_workbook(file.filename or "upload", content)
        result = add_profiles(workbook)
    except ParseError as exc:
        logger.info("解析失败（%s）：%s", file.filename, exc)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": str(exc)},
        )
    # Cache the reviewed understanding server-side so Confirm consumes exactly
    # this result (a second, non-deterministic LLM call could disagree).
    understanding_repository.save_understanding(
        db, understanding_repository.session_key(content), result
    )
    logger.info(
        "解析成功（%s）：工作表 %d、实体 %d",
        file.filename,
        len(result.sheets),
        _entity_count(result),
    )
    return result


@router.post("/parse/stream", response_model=None)
async def parse_file_stream(
    file: UploadFile = File(...),
) -> StreamingResponse | JSONResponse:
    """Run the understanding pipeline and stream real stage events.

    Event protocol (text/event-stream, one JSON payload per frame):
      event: stage   data: {"key": "...", "count": N}
      event: result  data: <full ProfiledParsedWorkbook JSON>
      event: error   data: {"detail": "..."}
    """
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return _too_large()

    def generate() -> Iterator[str]:
        try:
            workbook = parse_workbook(file.filename or "upload", content)
        except ParseError as exc:
            logger.info("解析失败（%s）：%s", file.filename, exc)
            yield _sse("error", json.dumps({"detail": str(exc)}, ensure_ascii=False))
            return

        events: queue.Queue[_WorkerItem] = queue.Queue()

        def worker() -> None:
            try:
                result = add_profiles(
                    workbook,
                    on_stage=lambda key, count: events.put(
                        (
                            "stage",
                            json.dumps({"key": key, "count": count}, ensure_ascii=False),
                        )
                    ),
                )
                # Persist with a worker-owned session (request session is bound
                # to the event-loop thread); cache failure just means Confirm
                # re-runs inference, it never invalidates this parse result.
                try:
                    session_db = SessionLocal()
                    try:
                        understanding_repository.save_understanding(
                            session_db,
                            understanding_repository.session_key(content),
                            result,
                        )
                    finally:
                        session_db.close()
                except Exception:
                    # Non-fatal: Confirm re-runs inference when the cache is
                    # missing, but a persistently failing cache must be visible
                    # in the logs or nobody notices Confirm got slower.
                    logger.warning(
                        "理解结果缓存写入失败，Confirm 将回退为重新推理",
                        exc_info=True,
                    )
                logger.info(
                    "解析成功（%s）：工作表 %d、实体 %d",
                    file.filename,
                    len(workbook.sheets),
                    _entity_count(result),
                )
                events.put(("result", result.model_dump_json()))
            except Exception:
                # surface pipeline failures as SSE errors
                logger.exception("理解流水线在 SSE worker 中失败")
                events.put(
                    ("error", json.dumps(
                        {"detail": "理解流水线执行失败，请稍后重试"},
                        ensure_ascii=False,
                    ))
                )

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        # The parse stage already completed above; emit it first.
        yield _sse(
            "stage",
            json.dumps({"key": STAGE_SHEETS, "count": len(workbook.sheets)}, ensure_ascii=False),
        )

        while True:
            kind, payload = events.get()
            if kind == "result":
                yield _sse("result", cast(str, payload))
                break
            yield _sse(kind, cast(str, payload))
            if kind == "error":
                break

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/confirm", response_model=ConfirmResponse)
async def confirm_file(
    file: UploadFile = File(...),
    decisions: str = Form(...),
    app_key: str | None = Form(None),
    db: Session = Depends(get_db),
) -> ConfirmResponse | JSONResponse:
    """Day 15: apply review decisions, persist model + rows, return the app.

    Multipart form:
      file:      the same workbook the user reviewed (pipeline re-runs here);
      decisions: JSON {"acknowledged": [...], "links": {key: {decision,...}}};
      app_key:   optional identifier of a new system; omitted means the legacy
                 workspace app, which keeps appending versions on re-upload.
    """
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return _too_large()

    try:
        payload = json.loads(decisions)
    except json.JSONDecodeError as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": f"decisions 不是合法 JSON：{exc}"},
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "decisions 必须是 JSON 对象"},
        )

    try:
        summary = confirmation.confirm_workbook(
            db, file.filename or "upload", content, payload, app_key=app_key
        )
    except model_repository.AppExistsError:
        return _app_exists(app_key)
    except confirmation.ConfirmError as exc:
        body: dict[str, object] = {"detail": str(exc)}
        if exc.unresolved:
            body["unresolved"] = exc.unresolved
        logger.warning(
            "确认生成被拒（%s）：%s（未决 %d 项）",
            file.filename,
            exc,
            len(exc.unresolved),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=body
        )
    except model_repository.VersionConflictError as exc:
        # Another confirm landed first: nothing was written here, so ask the
        # user to retry against the version that actually persisted.
        logger.warning(
            "确认生成时版本冲突（app=%s）：%s",
            app_key or confirmation.WORKSPACE_APP_KEY,
            exc,
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)}
        )

    logger.info(
        "确认生成成功（app=%s 文件=%s 版本=%d 记录=%s）",
        summary.app_key,
        file.filename,
        summary.version,
        summary.record_counts,
    )
    return ConfirmResponse(**summary.model_dump())


def _json_object(raw: str, name: str) -> dict[str, object] | JSONResponse:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": f"{name} 不是合法 JSON：{exc}"},
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": f"{name} 必须是 JSON 对象"},
        )
    return payload


def _cache_understanding(
    db: Session, uploads: list[tuple[str, bytes]], result: ProfiledParsedWorkbook
) -> None:
    """Store the reviewed understanding; a miss only makes Confirm re-run it."""
    try:
        understanding_repository.save_understanding(
            db, multi_import.session_key(uploads), result
        )
    except Exception:
        logger.warning("多文件理解结果缓存写入失败，Confirm 将回退为重新推理", exc_info=True)


@router.post("/parse-multi", response_model=MultiImportResult)
async def parse_multiple(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> MultiImportResult | JSONResponse:
    """Parse several uploads as one import and return the default merge plan."""
    uploads, error = await _read_uploads(files)
    if error is not None:
        return error
    try:
        understanding = multi_import.understand(uploads)
    except multi_import.ImportError as exc:
        logger.info("多文件解析失败：%s", exc)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": str(exc)},
        )
    _cache_understanding(db, uploads, understanding)
    result = multi_import.build_result(understanding)
    entities = (
        result.business_model.get("entities") if result.business_model else None
    )
    logger.info(
        "多文件解析成功：文件 %d、工作表 %d、实体 %d、冲突 %d",
        len(uploads),
        len(result.sheets),
        len(entities) if isinstance(entities, list) else 0,
        len(result.conflicts),
    )
    return result


@router.post("/parse-multi/preview", response_model=MultiPreview)
async def preview_multiple(
    files: list[UploadFile] = File(...),
    options: str = Form("{}"),
    db: Session = Depends(get_db),
) -> MultiPreview | JSONResponse:
    """Recompute merging/links after the user changes sheets or merge groups."""
    uploads, error = await _read_uploads(files)
    if error is not None:
        return error
    payload = _json_object(options, "options")
    if isinstance(payload, JSONResponse):
        return payload
    try:
        parsed = multi_import.MultiPreviewOptions.model_validate(payload)
        understanding = multi_confirmation.understand_files(db, uploads)
        return multi_import.preview(
            understanding,
            sheets=parsed.sheets,
            groups=parsed.groups,
            choices=parsed.conflicts,
        )
    except ValueError as exc:
        # ImportError and pydantic's ValidationError both inherit ValueError.
        logger.info("多文件预览失败：%s", exc)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc)},
        )


@router.post("/confirm-multi", response_model=ConfirmResponse)
async def confirm_multiple(
    files: list[UploadFile] = File(...),
    decisions: str = Form(...),
    app_key: str | None = Form(None),
    db: Session = Depends(get_db),
) -> ConfirmResponse | JSONResponse:
    """Apply sheet selection, merge groups and conflict choices, then persist."""
    uploads, error = await _read_uploads(files)
    if error is not None:
        return error
    payload = _json_object(decisions, "decisions")
    if isinstance(payload, JSONResponse):
        return payload

    try:
        summary = multi_confirmation.confirm_multi(
            db, uploads, payload, app_key=app_key
        )
    except model_repository.AppExistsError:
        return _app_exists(app_key)
    except confirmation.ConfirmError as exc:
        body: dict[str, object] = {"detail": str(exc)}
        if exc.unresolved:
            body["unresolved"] = exc.unresolved
        logger.warning(
            "多文件确认被拒（%d 个文件）：%s（未决 %d 项）",
            len(uploads),
            exc,
            len(exc.unresolved),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=body
        )
    except model_repository.VersionConflictError as exc:
        logger.warning(
            "多文件确认时版本冲突（app=%s）：%s",
            app_key or confirmation.WORKSPACE_APP_KEY,
            exc,
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)}
        )

    logger.info(
        "多文件确认成功（app=%s 文件=%d 版本=%d 记录=%s）",
        summary.app_key,
        len(uploads),
        summary.version,
        summary.record_counts,
    )
    return ConfirmResponse(**summary.model_dump())
