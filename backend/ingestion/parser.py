"""XLSX/CSV parsing into ParsedWorkbook (Day 5).

Stateless by design: bytes in, structured rows out, no database or file
storage. Cells are normalized uniformly to trimmed strings (None when blank)
so later stages never branch on the source format.

Defensive V0.1 limits reject pathological inputs explicitly rather than
silently truncating them, because truncated rows would corrupt the profiling
and type-inference stages that follow.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import zipfile
from pathlib import Path
from typing import Literal

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .header_detection import detect_header_row, used_width
from .schemas import Cell, ParsedSheet, ParsedWorkbook

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_DATA_ROWS = 50_000
MAX_COLUMNS = 200
# Hard guard against worksheets whose declared dimension points at the full
# 1,048,576-row sheet (read_only iteration would otherwise walk blanks).
MAX_RAW_ROWS = 200_000

_ZIP_MAGIC = b"PK\x03\x04"
# NOTE: bare "utf-16" must never sit in a probe chain without a BOM gate —
# without a BOM the codec assumes native byte order and decodes arbitrary
# even-length byte streams (e.g. GBK) into garbage without raising.
_UTF16_BOM = (b"\xff\xfe", b"\xfe\xff")
_CSV_ENCODINGS = ("utf-8-sig", "gb18030")
_DELIMITERS = (",", "\t", ";")


class ParseError(ValueError):
    """A user-facing input problem; mapped to HTTP 422 by the route."""


def parse_workbook(file_name: str, content: bytes) -> ParsedWorkbook:
    if not content:
        raise ParseError("uploaded file is empty")
    file_type = _detect_file_type(file_name, content)
    if file_type == "xlsx":
        sheets = _parse_xlsx(content)
    else:
        sheets = [_parse_csv(file_name, content)]
    return ParsedWorkbook(file_name=file_name, file_type=file_type, sheets=sheets)


def _detect_file_type(file_name: str, content: bytes) -> Literal["xlsx", "csv"]:
    lower = file_name.lower()
    if content.startswith(_ZIP_MAGIC):
        # openpyxl performs the real validation below; a renamed .docx/.zip
        # reaches the same error path.
        return "xlsx"
    if lower.endswith(".xlsx"):
        raise ParseError("file has an .xlsx name but is not an XLSX container")
    if lower.endswith(".xls"):
        raise ParseError("legacy .xls workbooks are not supported; save the file as .xlsx")
    return "csv"


# ---------- XLSX ----------


def _cell_to_text(value: object) -> Cell:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return str(value)


def _parse_xlsx(content: bytes) -> list[ParsedSheet]:
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ParseError(f"not a valid XLSX file: {exc}") from exc

    sheets: list[ParsedSheet] = []
    try:
        for worksheet in workbook.worksheets:
            rows: list[list[Cell]] = []
            for row in worksheet.iter_rows(values_only=True):
                rows.append([_cell_to_text(value) for value in row])
                if len(rows) > MAX_RAW_ROWS:
                    raise ParseError(
                        f"sheet {worksheet.title!r} exceeds {MAX_RAW_ROWS} raw rows"
                    )
            sheets.append(_build_sheet(worksheet.title, rows))
    finally:
        workbook.close()
    return sheets


# ---------- CSV ----------


def _decode_csv(content: bytes) -> str:
    # Excel on Chinese Windows exports GBK/GB18030; "CSV UTF-8" carries a BOM
    # (handled by utf-8-sig); Excel's "Unicode Text" CSV is UTF-16 with BOM.
    if content.startswith(_UTF16_BOM):
        candidates = ("utf-16", "utf-8-sig", "gb18030")
    else:
        candidates = _CSV_ENCODINGS
    last_error: UnicodeDecodeError | None = None
    for encoding in candidates:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ParseError(
        "cannot decode CSV as UTF-8 or GB18030; re-export it as UTF-8 encoded CSV"
    ) from last_error


def _detect_delimiter(text: str) -> str:
    counts = {delimiter: 0 for delimiter in _DELIMITERS}
    checked = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for delimiter in _DELIMITERS:
            counts[delimiter] += stripped.count(delimiter)
        checked += 1
        if checked >= 10:
            break
    return max(_DELIMITERS, key=lambda delimiter: counts[delimiter])


def _parse_csv(file_name: str, content: bytes) -> ParsedSheet:
    text = _decode_csv(content)
    delimiter = _detect_delimiter(text)
    rows: list[list[Cell]] = []
    for record in csv.reader(io.StringIO(text), delimiter=delimiter):
        if not record:
            rows.append([])
            continue
        rows.append([(cell.strip() or None) for cell in record])
        if len(rows) > MAX_RAW_ROWS:
            raise ParseError(f"CSV exceeds {MAX_RAW_ROWS} raw rows")
    sheet_name = Path(file_name).stem or "sheet1"
    return _build_sheet(sheet_name, rows)


# ---------- Shared sheet assembly ----------


# A row is blank when it carries no "content character": no letter, digit or
# CJK ideograph. Rows made only of ".", "-", "/", spaces or punctuation are
# template artifacts (a stray "." in the remark column otherwise doubles the
# row count and destroys uniqueness statistics on small sheets).
_CONTENT_CHAR_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")

# Aggregate/footer rows ("合计 3138") are spreadsheet totals, not business
# records. Their per-row key column is empty, so they otherwise fail key
# validation at Confirm time.
_TOTAL_MARKERS = frozenset(
    {"合计", "共计", "总计", "小计", "累计", "total", "sum", "grand total"}
)


def _is_blank(row: list[Cell]) -> bool:
    return not any(
        cell is not None and _CONTENT_CHAR_RE.search(cell) for cell in row
    )


def _is_summary_row(row: list[Cell]) -> bool:
    for cell in row:
        if cell is None:
            continue
        text = cell.strip().strip(":：").strip().lower()
        if text in _TOTAL_MARKERS:
            return True
    return False


def _build_sheet(name: str, rows: list[list[Cell]]) -> ParsedSheet:
    detection = detect_header_row(rows)
    if detection is None:
        return ParsedSheet(name=name, is_empty=True)

    width = used_width(rows)
    if width > MAX_COLUMNS:
        raise ParseError(f"sheet {name!r} has {width} columns; limit is {MAX_COLUMNS}")

    warnings: list[str] = []
    if detection.confidence == "low":
        warnings.append(
            f"header row {detection.row_index + 1} was detected with low confidence; "
            "please verify it"
        )

    raw_header = rows[detection.row_index]
    columns: list[str] = []
    unnamed = 0
    for pos in range(width):
        value = raw_header[pos] if pos < len(raw_header) else None
        if value is None:
            unnamed += 1
            columns.append(f"column_{pos + 1}")
        else:
            columns.append(value)
    if unnamed:
        warnings.append(f"{unnamed} unnamed header column(s) generated as column_<n>")

    duplicates = sorted({col for col in columns if columns.count(col) > 1})
    if duplicates:
        warnings.append(f"duplicate header values: {', '.join(duplicates)}")

    data_rows: list[list[Cell]] = []
    summary_rows_dropped = 0
    for raw in rows[detection.row_index + 1 :]:
        if _is_blank(raw):
            continue
        if _is_summary_row(raw):
            summary_rows_dropped += 1
            continue
        aligned = [raw[pos] if pos < len(raw) else None for pos in range(width)]
        data_rows.append(aligned)
        if len(data_rows) > MAX_DATA_ROWS:
            raise ParseError(
                f"sheet {name!r} exceeds {MAX_DATA_ROWS} data rows; V0.1 limit"
            )

    if summary_rows_dropped:
        warnings.append(
            f"ignored {summary_rows_dropped} aggregate/total row(s) "
            "(合计/总计/小计 …)"
        )

    if not data_rows:
        warnings.append("sheet has a header row but no data rows")

    return ParsedSheet(
        name=name,
        header_row_number=detection.row_index + 1,
        columns=columns,
        rows=data_rows,
        data_row_count=len(data_rows),
        warnings=warnings,
    )
