"""Profile structures produced over a parsed sheet (Day 6).

Counts and ratios only. No type inference yet (Days 7-8 consume these
profiles; enum inference in particular reads top_values directly).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.ingestion.schemas import Cell, ParsedSheet

DEFAULT_SAMPLE_SIZE = 100
TOP_VALUES_LIMIT = 5


class ValueCount(BaseModel):
    value: str
    count: int


class ColumnProfile(BaseModel):
    name: str
    total_count: int = 0
    null_count: int = 0
    non_null_count: int = 0
    null_ratio: float = 0.0
    # Number of distinct non-null values.
    distinct_count: int = 0
    # Non-null values that occur exactly once.
    unique_value_count: int = 0
    # Cells whose value occurs more than once (non-null - unique cells).
    duplicate_cell_count: int = 0
    # True only when every non-null cell differs: the key/identifier candidate
    # signal later entity/relation inference builds on. All-null/empty => False.
    is_unique: bool = False
    unique_ratio: float = 0.0
    # Most frequent non-null values, frequency-descending then value for ties.
    top_values: list[ValueCount] = Field(default_factory=list)


class SheetProfile(BaseModel):
    row_count: int = 0
    # Rows beyond the first occurrence of every fully-duplicate row.
    duplicate_row_count: int = 0
    columns: list[ColumnProfile] = Field(default_factory=list)
    # Deterministic uniform-stride sample for preview/inference.
    sample_rows: list[list[Cell]] = Field(default_factory=list)
    sample_size_requested: int = DEFAULT_SAMPLE_SIZE
    sample_is_full: bool = True
    sample_strategy: str = "uniform-stride"


class ProfiledParsedSheet(ParsedSheet):
    profile: SheetProfile


class ProfiledParsedWorkbook(BaseModel):
    file_name: str
    file_type: str
    sheets: list[ProfiledParsedSheet]
