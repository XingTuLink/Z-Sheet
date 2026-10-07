"""File ingestion endpoints (Day 5).

Stateless parse: the uploaded workbook is parsed and returned immediately,
nothing is persisted. Later understanding stages will consume this same
output in a pipeline before any storage decision is needed.
"""

from __future__ import annotations

from fastapi import APIRouter, File, UploadFile, status
from fastapi.responses import JSONResponse

from backend.ingestion.parser import MAX_UPLOAD_BYTES, ParseError, parse_workbook
from backend.ingestion.schemas import ParsedWorkbook

router = APIRouter(prefix="/api/v1/ingestion", tags=["ingestion"])


@router.post("/parse", response_model=ParsedWorkbook)
async def parse_file(file: UploadFile = File(...)) -> ParsedWorkbook | JSONResponse:
    # Read one byte past the limit to detect oversize uploads deterministically.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content={"detail": f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit"},
        )
    try:
        return parse_workbook(file.filename or "upload", content)
    except ParseError as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": str(exc)},
        )
