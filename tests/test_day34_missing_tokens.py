"""Day 34: conventional missing-value markers must mean "no data".

Scientific metadata exports (NCBI/SRA) and many business templates mark
absent cells with a controlled vocabulary — "not provided", "Not found",
"not collected", "N/A", "无" — instead of leaving them blank. These tokens
are missing data, not literal values:

* typed columns (date/number/money/enum/phone) coerce them to null, so a
  "not provided" cell can no longer block Confirm as an invalid date;
* type inference ignores them when computing type ratios, cardinality and
  enum candidate sets ("Not found" must not become an enum option);
* free-text columns keep the literal text, because such words can be
  genuine remarks.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from openpyxl import Workbook

from backend.ai.provider import ChatClient, LLMConfig
from backend.domain.enums import FieldRole, FieldType
from backend.domain.models import BusinessField
from backend.ingestion.parser import parse_workbook
from backend.storage.db import SessionLocal
from backend.storage.record_store import get_record_store
from backend.storage.repositories import understanding_repository
from backend.understanding.confirmation import (
    ConfirmDecisions,
    confirm_workbook,
    unresolved_review_items,
)
from backend.understanding.missing_values import is_missing_token
from backend.understanding.model_assembly import (
    assemble_model,
    build_entity_plans,
)
from backend.understanding.profiling import add_profiles, profile_sheet
from backend.understanding.type_inference import infer_sheet


def _xlsx(rows: list[list[object]], sheet_name: str = "Samples") -> bytes:
    workbook = Workbook()
    worksheet = workbook.worksheets[0]
    worksheet.title = sheet_name
    for row in rows:
        worksheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# --- 1. token recognition --------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "not provided",
        "Not Provided",
        "  NOT FOUND ",
        "not collected",
        "not available",
        "N/A",
        "n.a.",
        "null",
        "None",
        "unknown",
        "/",
        "--",
        "—",
        ".",
        "无",
        "暂无",
        "未提供",
        "不详",
        "",
        # Marker phrases: marker + colon explanation (INSDC controlled vocab).
        "missing: lab stock",
        "MISSING: CONTROL SAMPLE",
        "missing: replicate advance notice required",
        "missing :lab stock",
        "missing：样品损坏",
        "not available: sample degraded",
        "unknown: not recorded by submitter",
        "N/A: not measured",
        "未提供：样品已损耗",
        # Marker phrases: marker + paired-parenthesis explanation.
        "Not applicable (egg1-3/Male1-3/Female1-3/Nymph1-5 - 1-3)",
        "N/A (sample lost in transit)",
        "not applicable（虫卵批次）",
        "缺失（仪器故障未测）",
    ],
)
def test_missing_token_recognised(text: str) -> None:
    assert is_missing_token(text)
    assert is_missing_token(None)


@pytest.mark.parametrize(
    "text",
    [
        "2022",
        "male",
        "暂无库存（下月补采）",  # starts with a marker word but is real text
        "无数据权限",
        "not provided by submitter",
        "none of the above",
        "备注：未知",
        # The delimiter must touch the marker; a plain sentence does not.
        "missing lab stock",
        "not applicable to this species",
        # Real geographic values use "Country: place" — never a marker.
        "Brazil: Rio de Janeiro",
        "USA: North Carolina",
        "China:Guangdong Foshan Shunde",
        # Parenthesis must close the whole cell; trailing text keeps it real.
        "unknown (see notes) then resampled",
    ],
)
def test_real_text_is_not_a_missing_token(text: str) -> None:
    assert not is_missing_token(text)


# --- 2. type inference -----------------------------------------------------
#
# Note: single-column sheets confuse header auto-detection (a marker cell can
# look like the most text-like row), so every probe carries a realistic key
# column next to the column under test.


def _infer(columns: list[object], rows: list[list[object]]):
    content = _xlsx([columns, *rows])
    workbook = parse_workbook("samples.xlsx", content)
    sheet = workbook.sheets[0]
    return {
        field.column: field for field in infer_sheet(sheet, profile_sheet(sheet))
    }


def test_date_column_with_markers_infers_as_date() -> None:
    inferred = _infer(
        ["Run", "Collection_Date"],
        [
            ["S1", 2022],
            ["S2", "2023-06-01"],
            ["S3", "not provided"],
            ["S4", "Not found"],
            ["S5", None],
            ["S6", "not collected"],
            ["S7", 2014],
        ],
    )
    field = inferred["Collection_Date"]
    assert field.type == "date"
    assert field.confidence >= 0.95


def test_year_only_and_partial_dates_recognised() -> None:
    inferred = _infer(
        ["Run", "年份"],
        [
            ["S1", 1997],
            ["S2", 2006],
            ["S3", "2022-03"],
            ["S4", 2024],
        ],
    )
    assert inferred["年份"].type == "date"


def test_four_digit_codes_below_1900_stay_numbers() -> None:
    inferred = _infer(
        ["Run", "编码"],
        [
            ["S1", 1001],
            ["S2", 1002],
            ["S3", 1099],
            ["S4", 1500],
        ],
    )
    assert inferred["编码"].type == "number"


def test_enum_candidates_exclude_markers() -> None:
    inferred = _infer(
        ["Run", "sex"],
        [
            ["S1", "male"],
            ["S2", "female"],
            ["S3", "male"],
            ["S4", "female"],
            ["S5", "male"],
            ["S6", "Not found"],
            ["S7", "not provided"],
            ["S8", None],
        ],
    )
    field = inferred["sex"]
    assert field.type == "enum"
    assert sorted(field.enum_values) == ["female", "male"]


def test_number_column_with_markers_stays_number() -> None:
    inferred = _infer(
        ["Run", "AvgSpotLen"],
        [
            ["S1", 100],
            ["S2", 150],
            ["S3", "Not found"],
            ["S4", 200],
            ["S5", "/"],
            ["S6", 180],
        ],
    )
    assert inferred["AvgSpotLen"].type == "number"


def test_date_column_with_phrase_markers_infers_as_date() -> None:
    # The reported Day-35 failure: INSDC "missing: <reason>" in a date column.
    inferred = _infer(
        ["Run", "Collection_Date"],
        [
            ["S1", 2022],
            ["S2", "2023-06-01"],
            ["S3", "missing: lab stock"],
            ["S4", "MISSING: CONTROL SAMPLE"],
            ["S5", 2014],
            ["S6", 2019],
        ],
    )
    field = inferred["Collection_Date"]
    assert field.type == "date"
    assert field.confidence >= 0.95


def test_enum_candidates_exclude_parenthesised_marker_phrases() -> None:
    # age column in the real SRA workbook: 24 "Not applicable (...)" variants
    # must not blow the enum candidate set; the life-stage labels stay.
    inferred = _infer(
        ["Run", "age"],
        [
            ["S1", "Adult"],
            ["S2", "adult"],
            ["S3", "Not applicable (egg1-3/Male1-3 - 1-3)"],
            ["S4", "Not applicable (egg1-3/Male1-3 - 1-4)"],
            ["S5", "adult"],
            ["S6", "1st instar"],
            ["S7", "Adult"],
            ["S8", "adult"],
            ["S9", "Adult"],
            ["S10", "adult"],
        ],
    )
    field = inferred["age"]
    assert field.type == "enum"
    assert "1st instar" in field.enum_values
    assert not any(
        value.lower().startswith("not applicable") for value in field.enum_values
    )


def test_geographic_colon_values_remain_free_text() -> None:
    # "Brazil: Rio de Janeiro" must never be swallowed as a marker phrase.
    inferred = _infer(
        ["Run", "geo_loc_name"],
        [
            ["S1", "Brazil: Rio de Janeiro"],
            ["S2", "USA: North Carolina"],
            ["S3", "China: Guangdong"],
            ["S4", "Japan:Centennial Forest Park, Hokkaido"],
        ],
    )
    field = inferred["geo_loc_name"]
    assert field.type == "string"
    assert sorted(field.enum_values) == []


# --- 3. typed coercion -----------------------------------------------------


def _field(ftype: FieldType, values: list[str] | None = None) -> BusinessField:
    role = (
        FieldRole.ENUM
        if ftype is FieldType.ENUM
        else FieldRole.MEASURE
        if ftype in (FieldType.NUMBER, FieldType.MONEY)
        else FieldRole.TIME
        if ftype is FieldType.DATE
        else FieldRole.TEXT
    )
    return BusinessField(
        key="f",
        name="字段",
        type=ftype,
        role=role,
        values=values,
        needs_review=False,
        confidence=0.9,
    )


def test_typed_coercion_turns_markers_into_none() -> None:
    from backend.understanding.confirmation import _coerce_cell

    assert _coerce_cell(_field(FieldType.DATE), "not provided", "w") is None
    assert _coerce_cell(_field(FieldType.NUMBER), "Not found", "w") is None
    assert _coerce_cell(_field(FieldType.MONEY), "/", "w") is None
    assert _coerce_cell(_field(FieldType.PHONE), "无", "w") is None
    assert (
        _coerce_cell(_field(FieldType.ENUM, ["male", "female"]), "N/A", "w")
        is None
    )


def test_free_text_keeps_marker_literals() -> None:
    from backend.understanding.confirmation import _coerce_cell

    assert _coerce_cell(_field(FieldType.STRING), "not provided", "w") == (
        "not provided"
    )
    assert _coerce_cell(_field(FieldType.STRING), "无", "w") == "无"


def test_genuinely_bad_date_still_raises() -> None:
    from backend.understanding.confirmation import ConfirmError, _coerce_cell

    with pytest.raises(ConfirmError):
        _coerce_cell(_field(FieldType.DATE), "昨天", "w")


def test_typed_coercion_turns_marker_phrases_into_none() -> None:
    from backend.understanding.confirmation import _coerce_cell

    assert (
        _coerce_cell(_field(FieldType.DATE), "missing: lab stock", "w") is None
    )
    assert (
        _coerce_cell(_field(FieldType.DATE), "Not applicable (egg batch)", "w")
        is None
    )
    assert _coerce_cell(_field(FieldType.NUMBER), "N/A: not measured", "w") is None
    assert (
        _coerce_cell(
            _field(FieldType.ENUM, ["adult", "egg"]),
            "Not applicable (egg1-3/Male1-3 - 1-3)",
            "w",
        )
        is None
    )


def test_free_text_keeps_marker_phrase_literals() -> None:
    from backend.understanding.confirmation import _coerce_cell

    assert (
        _coerce_cell(_field(FieldType.STRING), "missing: lab stock", "w")
        == "missing: lab stock"
    )


# --- 4. full SRA-style Confirm (the reported failure) ----------------------


class _ProposalTransport:
    def __init__(self, proposal: dict[str, Any]) -> None:
        self.proposal = proposal

    def __call__(self, url, headers, payload, timeout) -> dict[str, Any]:
        return {
            "choices": [
                {"message": {"content": json.dumps(self.proposal, ensure_ascii=False)}}
            ]
        }


_LLM_CONFIG = LLMConfig(
    base_url="https://api.deepseek.com",
    api_key="sk-test-1234567890",
    model="deepseek-flash",
)


def test_sra_workbook_confirms_with_markers_as_null() -> None:
    rows = [
        ["Run", "Collection_Date", "AvgSpotLen", "sex"],
        ["S1", 2022, 100, "male"],
        ["S2", "2023-06-01", 150, "Not found"],
        ["S3", "not provided", "Not found", "female"],
        ["S4", 2014, "/", "male"],
        ["S5", "not collected", 120, "not provided"],
        # Marker phrases (the Day-35 SRA report): colon form in a date cell,
        # parenthesised form in an enum cell.
        ["S6", "missing: lab stock", 130, "Not applicable (pooled eggs)"],
    ]
    file_name = "sra.xlsx"
    content = _xlsx(rows)
    workbook = parse_workbook(file_name, content)
    proposal: dict[str, Any] = {
        "entities": [
            {
                "source_sheet": "Samples",
                "key": "sample",
                "name": "样本",
                "key_field": "Run",
                "confidence": 0.95,
                "fields": [
                    {"column": "Run", "type": "string", "role": "identifier",
                     "confidence": 0.95},
                    {"column": "Collection_Date", "type": "date",
                     "role": "time", "confidence": 0.95},
                    {"column": "AvgSpotLen", "type": "number",
                     "role": "measure", "confidence": 0.95},
                    {"column": "sex", "type": "enum", "role": "enum",
                     "enum_values": ["male", "female"], "confidence": 0.95},
                ],
            }
        ],
        "links": [],
    }
    result = add_profiles(
        workbook,
        llm_client=ChatClient(_LLM_CONFIG, transport=_ProposalTransport(proposal)),
    )
    plans, _ = build_entity_plans(result)
    assembled = assemble_model(result, plans, [])
    assert assembled is not None

    # Direct extraction: marker cells are null, enum set stays clean.
    from backend.understanding.confirmation import extract_records

    key_of = {f.name: f.key for f in assembled.entities[0].fields}
    extracted = extract_records(plans, assembled)["sample"]
    by_run = {row[key_of["Run"]]: row for row in extracted}
    assert by_run["S3"][key_of["Collection_Date"]] is None
    assert by_run["S3"][key_of["AvgSpotLen"]] is None
    assert by_run["S2"][key_of["sex"]] is None
    assert by_run["S6"][key_of["Collection_Date"]] is None
    assert by_run["S6"][key_of["sex"]] is None
    assert by_run["S1"][key_of["Collection_Date"]] == "2022-01-01"
    sex_field = next(f for f in assembled.entities[0].fields if f.name == "sex")
    assert sorted(sex_field.values or []) == ["female", "male"]

    pending = unresolved_review_items(
        plans, assembled, ConfirmDecisions.model_validate({})
    )
    decisions = {
        "acknowledged": [
            item if item.startswith("metric:") else item.split(":", 1)[1]
            for item in pending
        ],
        "links": {},
    }
    db = SessionLocal()
    try:
        understanding_repository.save_understanding(
            db, understanding_repository.session_key(content), result
        )
        db.commit()
        summary = confirm_workbook(db, file_name, content, decisions)
        records = get_record_store(db).load(summary.app_key)
    finally:
        db.close()

    assert dict(summary.record_counts) == {"sample": 6}
    assert records is not None
    stored = records["sample"]
    run_key = key_of["Run"]
    date_key = key_of["Collection_Date"]
    null_dates = {row[run_key]: row[date_key] for row in stored}
    assert null_dates["S3"] is None
    assert null_dates["S5"] is None
    assert null_dates["S6"] is None
