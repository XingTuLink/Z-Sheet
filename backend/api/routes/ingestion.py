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
from backend.storage.db import get_db
from backend.understanding import confirmation
from backend.understanding.profiling import STAGE_SHEETS, add_profiles
from backend.understanding.schemas import ProfiledParsedWorkbook

router = APIRouter(prefix="/api/v1/ingestion", tags=["ingestion"])

# Thread -> generator hand-off items. The understanding pipeline is a
# synchronous CPU-bound call, so it runs on a worker thread while this
# generator forwards stage frames the moment each stage finishes.
_WorkerItem = tuple[str, object]


def _sse(event: str, data: str) -> str:
    """Frame one Server-Sent Event; data must be a single-line JSON string."""
    return f"event: {event}\ndata: {data}\n\n"


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
) -> ProfiledParsedWorkbook | JSONResponse:
    # Read one byte past the limit to detect oversize uploads deterministically.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content={"detail": f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit"},
        )
    try:
        workbook = parse_workbook(file.filename or "upload", content)
        return add_profiles(workbook)
    except ParseError as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": str(exc)},
        )


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
        return JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content={"detail": f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit"},
        )

    def generate() -> Iterator[str]:
        try:
            workbook = parse_workbook(file.filename or "upload", content)
        except ParseError as exc:
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
                events.put(("result", result.model_dump_json()))
            except Exception as exc:  # surface pipeline failures as SSE errors
                events.put(
                    ("error", json.dumps({"detail": str(exc)}, ensure_ascii=False))
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


def _read_error() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        content={"detail": f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit"},
    )


@router.post("/confirm", response_model=ConfirmResponse)
async def confirm_file(
    file: UploadFile = File(...),
    decisions: str = Form(...),
    db: Session = Depends(get_db),
) -> ConfirmResponse | JSONResponse:
    """Day 15: apply review decisions, persist model + rows, return the app.

    Multipart form:
      file:      the same workbook the user reviewed (pipeline re-runs here);
      decisions: JSON {"acknowledged": [...], "links": {key: {decision,...}}}.
    """
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return _read_error()

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
            db, file.filename or "upload", content, payload
        )
    except confirmation.ConfirmError as exc:
        body: dict[str, object] = {"detail": str(exc)}
        if exc.unresolved:
            body["unresolved"] = exc.unresolved
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=body
        )

    return ConfirmResponse(**summary.model_dump())
