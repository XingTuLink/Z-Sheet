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

from fastapi import APIRouter, File, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse

from backend.ingestion.parser import MAX_UPLOAD_BYTES, ParseError, parse_workbook
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
