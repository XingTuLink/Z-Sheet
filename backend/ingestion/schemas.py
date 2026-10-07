"""Structured output of the ingestion layer (Day 5).

Everything is JSON-serializable on purpose: the parse endpoint is stateless
and the same structures feed later profiling/inference stages in-process.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# A single cell value: raw text, or None for an empty cell.
Cell = str | None


class ParsedSheet(BaseModel):
    """One discovered table (an XLSX worksheet or the sole CSV table)."""

    name: str
    # Empty sheets are reported, not silently dropped, so callers can show
    # exactly what the workbook contained.
    is_empty: bool = False
    # 1-based row number of the detected header, None for empty sheets.
    header_row_number: int | None = None
    columns: list[str] = Field(default_factory=list)
    # Data rows below the header, aligned to len(columns).
    rows: list[list[Cell]] = Field(default_factory=list)
    # Number of data rows discovered (equal to len(rows) for Day 5; kept
    # explicit so future sampling/pagination can diverge without a contract
    # change).
    data_row_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class ParsedWorkbook(BaseModel):
    file_name: str
    file_type: Literal["xlsx", "csv"]
    sheets: list[ParsedSheet]
