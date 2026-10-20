"""Day 29: abnormal / dirty input handling and recovery (Internal Alpha).

Acceptance: pathological user uploads must never 500 or hang the stream.
Unparseable files return a clear 422 (or an SSE ``error`` frame with a
non-empty Chinese/English reason); parseable but unusable workbooks parse
fine and are rejected at Confirm with an actionable 422 explaining what to
fix. Every case here is exercised through the HTTP layer plus the
understanding pipeline directly.

Scenarios:
- undecodable CSV bytes (not UTF-8/GB18030/UTF-16)
- valid ZIP container that is not a workbook (.docx renamed .xlsx)
- extensionless / unknown-extension text upload
- workbook whose sheets are all empty (banner-only, header-only, blank)
- header + punctuation-only rows (no real records)
- single real data row (weak-sample review, not a crash)
- oversized upload through the SSE endpoint (413 JSON, not a broken stream)
- Confirm on the above: ParseError/“没有实体” must be 422, never 500
"""

from __future__ import annotations

import io
import json
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.ai.provider import ChatClient, LLMConfig
from backend.api.main import app
from backend.api.routes import ingestion as ingestion_route
from backend.domain.enums import FieldRole, FieldType
from backend.domain.models import BusinessField
from backend.ingestion.parser import ParseError, parse_workbook
from backend.storage.db import SessionLocal
from backend.storage.repositories import understanding_repository
from backend.understanding.confirmation import (
    ConfirmDecisions,
    confirm_workbook,
    unresolved_review_items,
)
from backend.understanding.model_assembly import (
    assemble_model,
    build_entity_plans,
)
from backend.understanding.profiling import add_profiles

client = TestClient(app)

XLSX_MEDIA = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _xlsx(sheets: dict[str, list[list[object]]]) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.worksheets[0])
    for name, rows in sheets.items():
        worksheet = workbook.create_sheet(name)
        for row in rows:
            worksheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _frames(text: str) -> list[tuple[str, str]]:
    frames: list[tuple[str, str]] = []
    for block in text.strip().split("\n\n"):
        event = data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        frames.append((event, data))
    return frames


def _post_parse(file_name: str, content: bytes, media: str = "text/csv"):
    return client.post(
        "/api/v1/ingestion/parse",
        files={"file": (file_name, content, media)},
    )


# --- 1. bytes that cannot be parsed ----------------------------------------------------


def test_undecodable_csv_returns_422_with_guidance() -> None:
    # No UTF-16 BOM, and a lead byte 0x81 followed by invalid trail 0xff
    # makes the stream undecodable by both utf-8-sig and gb18030.
    content = b"\x81\xff\xfe\x80"
    response = _post_parse("台账.csv", content)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, str) and "UTF-8" in detail


def test_undecodable_csv_arrives_as_sse_error_frame() -> None:
    response = client.post(
        "/api/v1/ingestion/parse/stream",
        files={"file": ("台账.csv", b"\x81\xff\xfe\x80", "text/csv")},
    )
    assert response.status_code == 200
    frames = _frames(response.text)
    assert [event for event, _ in frames] == ["error"]
    assert json.loads(frames[0][1])["detail"]


def test_docx_renamed_to_xlsx_is_rejected_not_crashed() -> None:
    # A .docx IS a valid ZIP container — the ZIP magic check passes, then
    # openpyxl must reject it cleanly as a non-workbook.
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<not-a-workbook/>")
        archive.writestr("word/document.xml", "<docx/>")
    response = _post_parse("报价.xlsx", buffer.getvalue(), XLSX_MEDIA)
    assert response.status_code == 422
    assert "XLSX" in response.json()["detail"]


def test_unknown_extension_text_never_500s() -> None:
    # Frontend blocks this, but the API itself must stay robust: extensionless
    # garbage falls through to CSV parsing and yields an empty/odd workbook.
    response = _post_parse("notes.txt", b"\x00\x01\x02 binary-ish",
                           "application/octet-stream")
    assert response.status_code in (200, 422)
    if response.status_code == 422:
        assert response.json()["detail"]


def test_missing_filename_is_treated_as_csv() -> None:
    response = _post_parse("name-less", "客户名称,区域\n张三,华东\n".encode("gbk"))
    assert response.status_code == 200
    assert response.json()["sheets"][0]["rows"] == [["张三", "华东"]]


# --- 2. parseable but unusable workbooks -----------------------------------------------


def test_completely_empty_xlsx_is_rejected() -> None:
    with pytest.raises(ParseError, match="empty"):
        parse_workbook("空.xlsx", b"")
    response = _post_parse("空.xlsx", b"", XLSX_MEDIA)
    assert response.status_code == 422


def test_all_empty_sheets_parse_but_assemble_nothing() -> None:
    content = _xlsx(
        {
            "封面": [["某公司内部资料（封面页，无业务数据）"]],
            "空白": [],
            "仅表头": [["客户名称", "区域"]],
        }
    )
    response = _post_parse("空数据.xlsx", content, XLSX_MEDIA)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["business_model"] is None
    # 空白 is genuinely empty; the banner and the header-only sheet carry
    # zero data rows and must explain themselves via warnings.
    by_name = {sheet["name"]: sheet for sheet in body["sheets"]}
    assert by_name["空白"]["is_empty"] is True
    for name in ("封面", "仅表头"):
        assert by_name[name]["data_row_count"] == 0
    assert any("没有数据行" in warning for warning in by_name["仅表头"]["warnings"])


def test_all_empty_sheets_stream_result_without_error_frame() -> None:
    content = _xlsx({"说明": [["只有标题"]], "空白": []})
    response = client.post(
        "/api/v1/ingestion/parse/stream",
        files={"file": ("空数据.xlsx", content, XLSX_MEDIA)},
    )
    assert response.status_code == 200
    frames = _frames(response.text)
    assert "error" not in [event for event, _ in frames]
    assert frames[-1][0] == "result"
    assert json.loads(frames[-1][1])["business_model"] is None


def test_header_plus_punctuation_rows_has_no_entities() -> None:
    content = _xlsx(
        {"客户": [
            ["客户名称", "区域"],
            [".", None],
            [None, "-"],
            ["/", "/"],
        ]}
    )
    workbook = parse_workbook("伪行.xlsx", content)
    sheet = workbook.sheets[0]
    assert sheet.data_row_count == 0
    result = add_profiles(workbook, llm_client=None)
    assert result.business_model is None


def test_single_real_row_does_not_crash_and_is_review_gated() -> None:
    # One populated row is the Day 21 weak-sample case: inference runs, but
    # statistics built on a single sample must be honest (review, not crash).
    content = _xlsx(
        {"客户": [["客户名称", "区域", "联系电话"], ["张三", "华东", "13800000001"]]}
    )
    workbook = parse_workbook("单行.xlsx", content)
    result = add_profiles(workbook, llm_client=None)
    assert result.sheets[0].data_row_count == 1
    # Either a review-gated candidate model or no model — never an exception.
    model = result.business_model
    if model is not None:
        entities: list[dict[str, Any]] = cast(
            dict[str, Any], model
        )["entities"]
        assert any(
            field["needs_review"]
            for entity in entities
            for field in entity["fields"]
        ) or any(entity.get("needs_review") for entity in entities)


def test_oversize_sse_upload_returns_413_json(monkeypatch) -> None:
    monkeypatch.setattr(ingestion_route, "MAX_UPLOAD_BYTES", 10)
    response = client.post(
        "/api/v1/ingestion/parse/stream",
        files={"file": ("big.csv", b"a" * 100, "text/csv")},
    )
    assert response.status_code == 413
    # The detail is user-facing, so it must stay Chinese (never raw English).
    detail = response.json()["detail"]
    assert "上限" in detail and "MB" in detail


# --- 3. Confirm must turn unusable input into an actionable 422 -------------------------


def _confirm(file_name: str, content: bytes, decisions: str = "{}"):
    return client.post(
        "/api/v1/ingestion/confirm",
        files={"file": (file_name, content, XLSX_MEDIA)},
        data={"decisions": decisions},
    )


def test_confirm_empty_sheets_422_with_actionable_message() -> None:
    content = _xlsx({"空白": [["标题"]]})
    response = _confirm("空数据.xlsx", content)
    assert response.status_code == 422
    assert "实体" in response.json()["detail"]


def test_confirm_punctuation_only_sheet_422() -> None:
    content = _xlsx({"客户": [["客户名称", "区域"], [".", "-"], ["/", None]]})
    response = _confirm("伪行.xlsx", content)
    assert response.status_code == 422
    assert "实体" in response.json()["detail"]


def test_confirm_broken_file_is_confirm_error_not_500() -> None:
    response = _confirm("坏.xlsx", b"not a workbook at all")
    assert response.status_code == 422
    assert response.json()["detail"]


def test_confirm_oversize_file_returns_413(monkeypatch) -> None:
    monkeypatch.setattr(ingestion_route, "MAX_UPLOAD_BYTES", 10)
    response = _confirm("大.xlsx", b"a" * 100)
    assert response.status_code == 413


@pytest.mark.parametrize(
    "decisions",
    [
        "not-json",
        "[]",
        '{"acknowledged": "should-be-a-list"}',
    ],
)
def test_confirm_bad_decisions_payload_422(decisions: str) -> None:
    content = _xlsx({"客户": [["客户名称"], ["张三"]]})
    response = _confirm("t.xlsx", content, decisions=decisions)
    assert response.status_code == 422
    assert response.json()["detail"]


# --- 4. notes / contact blocks pasted under the table -----------------------------------
#
# Regression from a real file ("业务清单"): 34 numbered records followed by
# "💡 特别说明" / "✅ …" banner lines and a 联系我们/公司/电话/地址 footer.
# Those rows put non-numeric text into the 序号 column and used to make
# Confirm raise "...无法识别为数字" (422), blocking generation entirely.


def test_sequence_footer_rows_are_dropped_as_non_records() -> None:
    content = _xlsx(
        {
            "业务清单": [
                ["序号", "板块", "服务项目"],
                [1, "AI 开发", "智能客服"],
                [2, None, "门店管理系统"],
                [3, "AI 开发", "工单系统"],
                [None, None, None],
                ["💡 特别说明", None, None],
                ["✅ 所有业务都自带 AI 能力", None, None],
                ["联系我们", None, None],
                ["公司", "栈上月明软件科技", None],
                ["电话", "17629020227", None],
            ]
        }
    )
    sheet = parse_workbook("业务清单.xlsx", content).sheets[0]
    assert sheet.data_row_count == 3
    assert [row[0] for row in sheet.rows] == ["1", "2", "3"]
    warning = next(w for w in sheet.warnings if "非记录行" in w)
    assert "序号" in warning
    assert "5" in warning


def test_blank_sequence_cell_keeps_continuation_row() -> None:
    # Merged-cell layouts leave 序号 blank on continuation rows; the other
    # columns carry the data, so the row stays.
    content = _xlsx(
        {
            "订单": [
                ["序号", "客户", "备注"],
                [1, "张三", "首单"],
                [None, None, "补充说明行"],
                [2, "李四", "次日单"],
            ]
        }
    )
    sheet = parse_workbook("订单.xlsx", content).sheets[0]
    assert sheet.data_row_count == 3


def test_business_code_column_is_not_a_sequence_filter() -> None:
    # A 编号 column with A001-style codes must NOT enable the rule: the
    # sequence probe is gated on a predominantly-integer column.
    content = _xlsx(
        {
            "商品": [
                ["编号", "名称"],
                ["A001", "机械键盘"],
                ["A002", "无线鼠标"],
                ["公司", "栈上月明"],
            ]
        }
    )
    sheet = parse_workbook("商品.xlsx", content).sheets[0]
    assert sheet.data_row_count == 3
    assert not any("非记录行" in w for w in sheet.warnings)


def test_emoji_note_rows_dropped_without_sequence_column() -> None:
    content = _xlsx(
        {
            "项目": [
                ["项目名称", "说明"],
                ["智能客服", "接待访客"],
                ["💡 特别说明：以上项目均可定制", None],
                ["备注：报价均含税", None],
            ]
        }
    )
    sheet = parse_workbook("项目.xlsx", content).sheets[0]
    assert sheet.data_row_count == 1
    assert sheet.rows[0][0] == "智能客服"
    assert any("注释或横幅" in w for w in sheet.warnings)


def test_multicolumn_emoji_row_is_kept_without_sequence_column() -> None:
    # An emoji-prefixed but fully populated row is data, not a banner.
    content = _xlsx(
        {
            "项目": [
                ["项目名称", "说明"],
                ["✅ 套餐 A", "含全部 AI 能力"],
            ]
        }
    )
    sheet = parse_workbook("项目.xlsx", content).sheets[0]
    assert sheet.data_row_count == 1


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


def test_footer_notes_no_longer_block_generation() -> None:
    """End-to-end: emoji/contact footer rows must not 422 at Confirm."""
    content = _xlsx(
        {
            "业务清单": [
                ["序号", "板块", "服务项目"],
                [1, "AI 开发", "智能客服"],
                [2, None, "门店管理系统"],
                [3, "AI 开发", "工单系统"],
                [None, None, None],
                ["💡 特别说明", None, None],
                ["✅ 所有业务都自带 AI 能力", None, None],
                ["联系我们", None, None],
                ["公司", "栈上月明软件科技", None],
                ["电话", "17629020227", None],
            ]
        }
    )
    file_name = "业务清单.xlsx"
    workbook = parse_workbook(file_name, content)
    proposal: dict[str, Any] = {
        "entities": [
            {
                "source_sheet": "业务清单",
                "key": "service_item",
                "name": "业务清单",
                "key_field": "序号",
                "confidence": 0.95,
                "fields": [
                    {"column": "序号", "type": "number", "role": "identifier",
                     "confidence": 0.95},
                    {"column": "板块", "type": "string", "role": "dimension",
                     "confidence": 0.9},
                    {"column": "服务项目", "type": "string", "role": "name",
                     "confidence": 0.95},
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
    finally:
        db.close()

    assert dict(summary.record_counts) == {"service_item": 3}


# --- 5. enum cells with embedded layout whitespace --------------------------------------
#
# Regression from a real file: the 业务板块 cell was "AI 智能体\n开发"
# (wrapped inside the cell), but the LLM proposed the clean enum label
# "AI 智能体开发". Records validation compared them literally and 422'd.


def _enum_field() -> BusinessField:
    return BusinessField(
        key="field_2",
        name="业务板块",
        type=FieldType.ENUM,
        role=FieldRole.ENUM,
        confidence=0.95,
        values=[
            "AI 智能体开发",
            "GEO 生成式引擎优化",
            "企业官网建设",
            "小程序开发",
            "APP 开发",
        ],
    )


def test_enum_coercion_accepts_wrapped_and_aligned_labels() -> None:
    from backend.understanding.confirmation import _coerce_cell

    field = _enum_field()
    cases = {
        "AI 智能体\n开发": "AI 智能体开发",  # line break inside a wrapped cell
        "AI 智能体\t开发": "AI 智能体开发",  # tab
        "AI 智能体\u3000开发": "AI 智能体开发",  # ideographic space
        "  APP 开发 ": "APP 开发",  # surrounding whitespace, exact after strip
        "企业官网建设": "企业官网建设",  # exact value unchanged
    }
    for raw, expected in cases.items():
        assert _coerce_cell(field, raw, "where") == expected, raw


def test_enum_coercion_keeps_unknown_value_for_canonical_422() -> None:
    from backend.understanding.confirmation import _coerce_cell

    # Genuinely unknown values are returned raw so validate_records keeps
    # raising its standard "is not one of: …" error listing every option.
    assert _coerce_cell(_enum_field(), "区块链外包", "where") == "区块链外包"


def test_wrapped_enum_cells_no_longer_block_generation() -> None:
    content = _xlsx(
        {
            "业务清单": [
                ["序号", "业务板块", "服务项目"],
                [1, "AI 智能体\n开发", "智能客服"],
                [2, "小程序开发", "门店小程序"],
                [3, "企业官网建设", "品牌官网"],
            ]
        }
    )
    file_name = "业务清单.xlsx"
    workbook = parse_workbook(file_name, content)
    proposal: dict[str, Any] = {
        "entities": [
            {
                "source_sheet": "业务清单",
                "key": "business_service",
                "name": "业务清单",
                "key_field": "序号",
                "confidence": 0.95,
                "fields": [
                    {"column": "序号", "type": "number", "role": "identifier",
                     "confidence": 0.95},
                    {"column": "业务板块", "type": "enum", "role": "enum",
                     "enum_values": [
                         "AI 智能体开发",
                         "小程序开发",
                         "企业官网建设",
                     ],
                     "confidence": 0.95},
                    {"column": "服务项目", "type": "string", "role": "name",
                     "confidence": 0.95},
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

    # Extracted rows carry the canonical enum label, not the wrapped text.
    from backend.understanding.confirmation import extract_records

    records = extract_records(plans, assembled)
    block_field = next(
        f.key for f in assembled.entities[0].fields if f.name == "业务板块"
    )
    assert records["business_service"][0][block_field] == "AI 智能体开发"

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
    finally:
        db.close()

    assert dict(summary.record_counts) == {"business_service": 3}


def test_enum_proposal_missing_a_real_category_is_extended() -> None:
    """A category past the LLM sample window must extend, not block.

    Mirrors the real 业务清单 file: the LLM saw the first 20 rows and never
    proposed 管理系统定制 (a merged-cell category starting on row 24).
    """
    from backend.understanding.confirmation import extract_records

    rows: list[list[object]] = [["序号", "业务板块", "服务项目"]]
    # 20 rows inside the LLM sample, all from the 5 proposed categories.
    for index in range(1, 21):
        rows.append([index, "APP 开发", f"服务 {index}"])
    # Then a wrapped category the proposal never saw.
    rows.append([21, "管理系统\n定制", "门店管理系统"])
    content = _xlsx({"业务清单": rows})

    workbook = parse_workbook("业务清单.xlsx", content)
    proposal: dict[str, Any] = {
        "entities": [
            {
                "source_sheet": "业务清单",
                "key": "business_service",
                "name": "业务清单",
                "key_field": "序号",
                "confidence": 0.95,
                "fields": [
                    {"column": "序号", "type": "number", "role": "identifier",
                     "confidence": 0.95},
                    {"column": "业务板块", "type": "enum", "role": "enum",
                     "enum_values": [
                         "AI 智能体开发",
                         "GEO 生成式引擎优化",
                         "企业官网建设",
                         "小程序开发",
                         "APP 开发",
                     ],
                     "confidence": 0.95},
                    {"column": "服务项目", "type": "string", "role": "name",
                     "confidence": 0.95},
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

    records = extract_records(plans, assembled)
    block_field = next(
        f for f in assembled.entities[0].fields if f.name == "业务板块"
    )
    # The model's enum set was extended with the normalised missing label.
    assert "管理系统定制" in (block_field.values or [])
    assert records["business_service"][20][block_field.key] == "管理系统定制"
    assert block_field.type == FieldType.ENUM


def test_high_cardinality_enum_proposal_is_downgraded_to_text() -> None:
    """Over the enum cap, the column is free text — still no generation block."""
    from backend.understanding.confirmation import extract_records

    rows: list[list[object]] = [["序号", "板块", "服务项目"]]
    # 25 one-off categories: proposal claims enum with 18 of them.
    for index in range(1, 26):
        rows.append([index, f"板块{index}", f"服务 {index}"])
    content = _xlsx({"业务清单": rows})

    workbook = parse_workbook("业务清单.xlsx", content)
    proposal: dict[str, Any] = {
        "entities": [
            {
                "source_sheet": "业务清单",
                "key": "business_service",
                "name": "业务清单",
                "key_field": "序号",
                "confidence": 0.95,
                "fields": [
                    {"column": "序号", "type": "number", "role": "identifier",
                     "confidence": 0.95},
                    {"column": "板块", "type": "enum", "role": "enum",
                     "enum_values": [f"板块{i}" for i in range(1, 19)],
                     "confidence": 0.9},
                    {"column": "服务项目", "type": "string", "role": "name",
                     "confidence": 0.95},
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

    records = extract_records(plans, assembled)
    block_field = next(
        f for f in assembled.entities[0].fields if f.name == "板块"
    )
    assert block_field.type == FieldType.STRING
    assert block_field.values is None
    assert block_field.role == FieldRole.DIMENSION
    assert len(records["business_service"]) == 25
