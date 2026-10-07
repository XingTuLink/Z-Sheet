"""Tests for Day 6 profiling: null / duplicate / unique stats and sampling."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.ingestion.schemas import ParsedSheet
from backend.understanding.profiling import add_profiles, profile_sheet, uniform_sample
from backend.understanding.schemas import DEFAULT_SAMPLE_SIZE

client = TestClient(app)


def make_sheet(
    columns: list[str], rows: list[list[object]], name: str = "t"
) -> ParsedSheet:
    normalized = [[None if cell is None else str(cell) for cell in row] for row in rows]
    return ParsedSheet(
        name=name,
        header_row_number=1,
        columns=columns,
        rows=normalized,
        data_row_count=len(normalized),
    )


def by_name(sheet_profile):
    return {column.name: column for column in sheet_profile.columns}


# ---------- column profiles ----------


def test_null_distinct_unique_and_duplicate_counts():
    sheet = make_sheet(
        ["订单号", "区域", "备注"],
        [
            ["SO-1", "华东", None],
            ["SO-2", "华东", None],
            ["SO-3", "华南", "加急"],
            ["SO-4", None, None],
        ],
    )
    profile = profile_sheet(sheet)
    cols = by_name(profile)

    order, region, note = cols["订单号"], cols["区域"], cols["备注"]

    assert profile.row_count == 4
    assert order.non_null_count == 4 and order.null_count == 0
    assert order.is_unique is True
    assert order.unique_ratio == 1.0
    assert order.duplicate_cell_count == 0

    assert region.non_null_count == 3 and region.null_count == 1
    assert region.null_ratio == 0.25
    assert region.is_unique is False
    assert region.distinct_count == 2
    # 华东 appears twice -> 2 duplicate cells; 华南 once.
    assert region.duplicate_cell_count == 2
    assert [(v.value, v.count) for v in region.top_values] == [("华东", 2), ("华南", 1)]

    assert note.non_null_count == 1 and note.null_count == 3
    assert note.null_ratio == 0.75
    # A single non-null value is technically unique but never a key signal.
    assert note.is_unique is True


def test_all_null_column_is_not_a_unique_key():
    sheet = make_sheet(["空列"], [[None], [None], [None]])
    column = profile_sheet(sheet).columns[0]
    assert column.non_null_count == 0
    assert column.is_unique is False
    assert column.unique_ratio == 0.0
    assert column.top_values == []


def test_top_values_capped_and_ties_alphabetical():
    values = ["b", "a", "c", "d", "e", "f", "g"]  # all distinct, 7 values
    sheet = make_sheet(["v"], [[value] for value in values])
    top = profile_sheet(sheet).columns[0].top_values
    assert len(top) == 5
    # Every count is 1: ties resolve by value ascending, so g is cut off.
    assert [item.value for item in top] == ["a", "b", "c", "d", "e"]


# ---------- table-level duplication ----------


def test_duplicate_row_count_counts_extra_copies_only():
    sheet = make_sheet(
        ["a", "b"],
        [
            ["1", "x"],
            ["2", "y"],
            ["1", "x"],  # dup of row 1
            ["1", "x"],  # another dup
            ["3", "z"],
        ],
    )
    assert profile_sheet(sheet).duplicate_row_count == 2


# ---------- empty sheet ----------


def test_empty_sheet_profile_is_zeroed():
    profile = profile_sheet(ParsedSheet(name="empty", is_empty=True))
    assert profile.row_count == 0
    assert profile.columns == []
    assert profile.duplicate_row_count == 0
    assert profile.sample_rows == []
    assert profile.sample_is_full is True


# ---------- sampling ----------


def _rows(count: int) -> list[list[str | None]]:
    return [[str(i)] for i in range(count)]


def test_sample_returns_everything_under_budget():
    rows = _rows(50)
    sample, is_full = uniform_sample(rows, DEFAULT_SAMPLE_SIZE)
    assert is_full is True
    assert sample == rows


def test_sample_is_strided_deterministic_and_covers_head_to_tail():
    rows = _rows(250)
    first, first_full = uniform_sample(rows, 100)
    second, _ = uniform_sample(rows, 100)
    assert first_full is False
    assert first == second  # deterministic, no randomness
    assert len(first) == 100
    flat = [int(row[0]) for row in first if row[0] is not None]
    assert flat[0] == 0 and flat[-1] == 249  # spans the whole sheet
    # Strictly increasing stride, no duplicates.
    assert flat == sorted(set(flat))


def test_profile_sample_exactly_at_boundary_is_full():
    rows = _rows(DEFAULT_SAMPLE_SIZE)
    _, is_full = uniform_sample(rows, DEFAULT_SAMPLE_SIZE)
    assert is_full is True


# ---------- end to end via facade / API ----------


def test_add_profiles_attaches_profile_to_every_sheet():
    workbook_sheets = [
        make_sheet(["id", "region"], [["1", "华东"], ["2", "华南"]], name="订单"),
        ParsedSheet(name="空", is_empty=True),
    ]
    from backend.ingestion.schemas import ParsedWorkbook

    workbook = ParsedWorkbook(file_name="f.xlsx", file_type="xlsx", sheets=workbook_sheets)
    result = add_profiles(workbook)
    assert result.sheets[0].profile.row_count == 2
    assert result.sheets[0].profile.columns[0].is_unique is True
    assert result.sheets[1].profile.row_count == 0
    assert result.sheets[1].is_empty is True


def test_parse_endpoint_returns_profile_and_sample():
    csv_body = "id,region\n1,华东\n2,华东\n3,华南\n".encode()
    response = client.post(
        "/api/v1/ingestion/parse",
        files={"file": ("c.csv", csv_body, "text/csv")},
    )
    assert response.status_code == 200, response.text
    sheet = response.json()["sheets"][0]
    profile = sheet["profile"]

    assert profile["row_count"] == 3
    assert profile["sample_is_full"] is True
    assert len(profile["sample_rows"]) == 3

    columns = {column["name"]: column for column in profile["columns"]}
    assert columns["id"]["is_unique"] is True
    assert columns["region"]["duplicate_cell_count"] == 2
    assert columns["region"]["top_values"][0] == {"value": "华东", "count": 2}
