"""Deterministic header-row detection.

Real spreadsheets rarely start at row 1: title banners, blank rows and notes
frequently sit above the column headers. Detection is a pure scoring function
over raw rows (empty rows included), which keeps the rules explicit and the
behaviour testable. No AI, no heuristics trained on data.

Scored features for each candidate row (scanned within HEADER_SCAN_LIMIT):

- coverage:   non-empty cells vs the sheet's used width (banner rows span one
              cell and score poorly);
- uniqueness: distinct values vs non-empty cells (column headers rarely repeat);
- text_ratio: share of values that are not plain numbers (headers are labels);
- fill_below: density of non-empty rows in a window below the candidate
              (headers are followed by contiguous data, not by empty space).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .schemas import Cell

HEADER_SCAN_LIMIT = 10
_FILL_WINDOW = 20
_LOW_CONFIDENCE_SCORE = 0.70

# Plain integers/decimals, optionally grouped with thousands separators.
_NUMERIC_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")


@dataclass(frozen=True)
class HeaderDetection:
    # 0-based index into the supplied rows.
    row_index: int
    score: float
    confidence: str  # "high" | "low"


def _is_blank(row: list[Cell]) -> bool:
    return all(cell is None or cell.strip() == "" for cell in row)


def used_width(rows: list[list[Cell]]) -> int:
    """Farthest position (1-based) that contains a value in any row."""
    width = 0
    for row in rows:
        if _is_blank(row):
            continue
        for pos in range(len(row) - 1, -1, -1):
            cell = row[pos]
            if cell is not None and cell.strip() != "":
                width = max(width, pos + 1)
                break
    return width


def _looks_numeric(value: str) -> bool:
    return bool(_NUMERIC_RE.match(value.strip()))


def detect_header_row(rows: list[list[Cell]]) -> HeaderDetection | None:
    """Pick the most header-like row.

    Returns None when no row contains any value (empty sheet). Blank rows are
    kept in the input so that gaps above/below candidates affect scoring.
    """
    if not any(not _is_blank(row) for row in rows):
        return None

    width = used_width(rows)
    if width == 0:
        return None

    # A header must be followed by data: candidates with nothing but blank
    # rows below them are ineligible. The sole exception is a one-non-blank
    # -row sheet (header-only), handled by the fallback below.
    eligible: list[tuple[int, float]] = []
    scores: list[tuple[int, float]] = []
    scan_end = min(len(rows), HEADER_SCAN_LIMIT)

    for index in range(scan_end):
        row = rows[index]
        values = [cell.strip() for cell in row[:width] if cell is not None and cell.strip() != ""]
        if not values:
            continue

        non_empty = len(values)
        coverage = non_empty / width
        uniqueness = len(set(values)) / non_empty
        text_ratio = sum(0 if _looks_numeric(value) else 1 for value in values) / non_empty

        below = rows[index + 1 : index + 1 + _FILL_WINDOW]
        # No rows below => zero fill, never a perfect score: otherwise the
        # last data row of a text-only table would always outscore the header.
        fill_below = (
            sum(0 if _is_blank(r) else 1 for r in below) / len(below) if below else 0.0
        )

        score = 0.4 * coverage + 0.2 * uniqueness + 0.2 * text_ratio + 0.2 * fill_below
        scores.append((index, score))
        if any(not _is_blank(r) for r in below):
            eligible.append((index, score))

    candidates = eligible if eligible else scores
    if not candidates:
        return None
    # First eligible row wins ties: uppermost position is the common layout.
    row_index, score = candidates[0]
    for candidate_index, candidate_score in candidates[1:]:
        if candidate_score > score:
            row_index, score = candidate_index, candidate_score
    return HeaderDetection(
        row_index=row_index,
        score=round(score, 4),
        confidence="high" if score >= _LOW_CONFIDENCE_SCORE else "low",
    )
