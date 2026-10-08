"""Deterministic semantic role inference (Day 9).

Roles: identifier / dimension / measure / time / enum / text.

Anchored on design example 7.1:
- order_no, customer_name (unique in its own table) -> identifier
- region (enum, text values) -> dimension
- owner, duplicated customer_name on the order side -> dimension
- amount (money) -> measure
- order_date (date) -> time
- phone -> text

Distinction between roles `enum` and `dimension` (both can be type=enum):
code-like value sets (0/1 status, short codes) constrain a field technically
-> enum; human-language category values (区域、类别) are business analysis
axes -> dimension. Cross-sheet foreign-key promotion is explicitly Day 11.
"""

from __future__ import annotations

import re

from backend.ingestion.schemas import Cell

from .keywords import IDENTIFIER_NAME_KEYWORDS, LONG_TEXT_NAME_KEYWORDS, has_keyword
from .schemas import CONFIDENCE_HIGH, ColumnProfile, InferredField, SheetProfile

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CODE_LIKE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_/]{0,5}$")

# A unique column with a handful of empty cells can still be the key; beyond
# this missing ratio it is unsafe to promote.
IDENTIFIER_MAX_NULL_RATIO = 0.05
LONG_TEXT_AVG_LENGTH = 12


def _result(
    field: InferredField,
    role: str,
    confidence: float,
    signals: list[str],
) -> None:
    field.role = role  # type: ignore[assignment]
    field.role_confidence = confidence
    field.role_needs_review = confidence < CONFIDENCE_HIGH
    field.role_signals = signals


def _is_code_like(values: list[str]) -> bool:
    """Numeric/short-code enum sets -> role enum; wordy values -> dimension."""
    if not values:
        return True
    wordy = sum(
        1
        for value in values
        if _CJK_RE.search(value) or (len(value) >= 2 and not _CODE_LIKE_RE.match(value))
    )
    return wordy / len(values) < 0.5


def annotate_role(
    field: InferredField,
    stats: ColumnProfile,
    sample: list[Cell],
) -> None:
    """Fill role / role_confidence / role_signals on one inferred field."""
    column = field.column
    non_null_sample = [value for value in sample if value is not None]

    if stats.non_null_count == 0:
        _result(field, "text", 0.0, ["no_non_null_values"])
        return

    unique_eligible = (
        stats.is_unique and stats.null_ratio <= IDENTIFIER_MAX_NULL_RATIO
    )
    identifier_name = has_keyword(column, IDENTIFIER_NAME_KEYWORDS)

    # 1. Identifier — uniqueness is the hard gate, and only text/code/numeric
    #    columns can be keys. Money is almost always unique but is always a
    #    measure; phone/date are contact/time fields regardless of uniqueness.
    if unique_eligible and field.type in ("string", "number"):
        if identifier_name:
            _result(field, "identifier", 0.95, ["unique_values", "name_keyword:identifier"])
            return
        if field.type == "string":
            # Fully unique free text without a key-ish name is ambiguous:
            # likely a name/title identifier, but ask for confirmation.
            _result(field, "identifier", 0.75, ["unique_values"])
            return
        # Unique numbers without an identifier-style name stay measures
        # (quantities can be all different) but enter the review queue.
        _result(field, "measure", 0.75, ["unique_numeric_without_key_name"])
        return

    # 2. Time.
    if field.type == "date":
        _result(field, "time", field.confidence, ["type:date"])
        return

    # 3. Measure.
    if field.type in ("money", "number"):
        _result(field, "measure", field.confidence, [f"type:{field.type}"])
        return

    # 4. Enum-typed columns: wordy categories are analysis dimensions, code
    #    sets (status flags, level codes) carry role enum.
    if field.type == "enum":
        if _is_code_like(field.enum_values):
            _result(
                field, "enum", 0.75,
                ["type:enum", "code_like_values"],
            )
        else:
            _result(
                field, "dimension", field.confidence,
                ["type:enum", "wordy_category_values"],
            )
        return

    # 5. Phone and other contact fields.
    if field.type == "phone":
        _result(field, "text", field.confidence, ["type:phone"])
        return

    # 6. String: long free text vs short dimensional labels.
    if has_keyword(column, LONG_TEXT_NAME_KEYWORDS):
        _result(field, "text", 0.9, ["name_keyword:long_text"])
        return

    avg_length = (
        sum(len(value) for value in non_null_sample) / len(non_null_sample)
        if non_null_sample
        else 0.0
    )
    if avg_length >= LONG_TEXT_AVG_LENGTH:
        _result(field, "text", 0.7, ["long_avg_length"])
        return

    # Short categorical strings (owner, region names, foreign-key labels).
    _result(field, "dimension", 0.85, ["short_categorical_text"])


def annotate_roles(fields: list[InferredField], profile: SheetProfile) -> None:
    stats_by_column = {column.name: column for column in profile.columns}
    for pos, field in enumerate(fields):
        stats = stats_by_column[field.column]
        sample = [
            row[pos] if pos < len(row) else None for row in profile.sample_rows
        ]
        annotate_role(field, stats, sample)
