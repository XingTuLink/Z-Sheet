"""Offline tests for LLM-powered understanding (Day 21).

A fake ChatClient returns canned proposals; no network. Deterministic rule
results act as fallback and column-level defaults.
"""

from __future__ import annotations

import json
from typing import Any

from backend.ai.provider import ChatClient, LLMCallError, LLMConfig
from backend.ingestion.schemas import ParsedSheet, ParsedWorkbook
from backend.understanding.profiling import add_profiles

LLM_CONFIG = LLMConfig(
    base_url="https://api.deepseek.com",
    api_key="sk-test-1234567890",
    model="deepseek-flash",
)


class ProposalTransport:
    """Transport returning one chat completion whose content is `proposal`."""

    def __init__(self, proposal: dict[str, Any]) -> None:
        self.proposal = proposal
        self.requests: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        self.requests.append(payload)
        return {
            "choices": [
                {"message": {"content": json.dumps(self.proposal, ensure_ascii=False)}}
            ]
        }


class FailingTransport:
    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        raise LLMCallError("boom")


def _person_sheet() -> ParsedSheet:
    return ParsedSheet(
        name="校对总表",
        header_row_number=1,
        columns=["序号", "姓名", "性别", "联系电话", "备注"],
        rows=[
            ["1", "郭婉莹", "女", "13759943324", None],
            ["2", "李明", "男", "13800000000", "资深"],
        ],
        data_row_count=2,
    )


def _workbook(sheets: list[ParsedSheet]) -> ParsedWorkbook:
    return ParsedWorkbook(file_name="t.xlsx", file_type="xlsx", sheets=sheets)


def _person_proposal() -> dict[str, Any]:
    return {
        "entities": [
            {
                "source_sheet": "校对总表",
                "key": "proofreader",
                "name": "外审人员",
                "key_field": "姓名",
                "confidence": 0.92,
                "needs_review": False,
                "reason": "按人登记的外审人员",
                "fields": [
                    {"column": "姓名", "type": "string", "role": "identifier",
                     "confidence": 0.95},
                    {"column": "性别", "type": "enum", "role": "enum",
                     "confidence": 0.95, "enum_values": ["男", "女"]},
                    {"column": "联系电话", "type": "phone", "role": "text",
                     "confidence": 0.95},
                    {"column": "备注", "type": "string", "role": "text",
                     "confidence": 0.85},
                    # 序号 deliberately omitted: must fall back, not be lost.
                ],
            }
        ],
        "links": [],
    }


def test_llm_records_arbitrary_person_entity_and_assembles_model() -> None:
    client = ChatClient(
        LLM_CONFIG, transport=ProposalTransport(_person_proposal())
    )
    result = add_profiles(_workbook([_person_sheet()]), llm_client=client)

    assert result.understanding_engine == "llm"
    sheet = result.sheets[0]
    entity = sheet.inferred_entity
    assert entity is not None
    assert entity.key == "proofreader"
    assert entity.name == "外审人员"
    assert entity.key_field == "姓名"
    assert entity.needs_review is False

    by_column = {field.column: field for field in sheet.inferred_fields}
    assert by_column["姓名"].role == "identifier"
    assert by_column["性别"].type == "enum"
    assert by_column["性别"].enum_values == ["男", "女"]
    assert by_column["联系电话"].type == "phone"
    # Omitted column survives via deterministic fallback.
    assert "序号" in by_column
    assert "llm:column_fallback" in by_column["序号"].signals

    assert result.business_model is not None
    assert result.business_model["app"]  # assembled end to end
    assert result.assembly_notes == []


def test_llm_engine_falls_back_to_rules_on_call_failure() -> None:
    client = ChatClient(LLM_CONFIG, transport=FailingTransport())
    result = add_profiles(_workbook([_person_sheet()]), llm_client=client)

    assert result.understanding_engine == "rules"
    assert result.understanding_notes
    assert result.understanding_notes[0].startswith("LLM 理解失败")
    # Rule engine keeps producing an entity/unknown honestly; pipeline intact.
    entity = result.sheets[0].inferred_entity
    assert entity is not None
    assert entity.signals  # rule signals, not llm


def test_forced_rules_engine_skips_llm() -> None:
    result = add_profiles(_workbook([_person_sheet()]), llm_client=None)
    assert result.understanding_engine == "rules"


def test_bad_entity_key_is_normalized_and_invalid_sheet_ignored() -> None:
    proposal = {
        "entities": [
            {
                "source_sheet": "校对总表",
                "key": "外审人员!!",  # not snake_ascii -> deterministic repair
                "name": "外审人员",
                "key_field": "不存在的列",
                "confidence": 0.9,
                "fields": [
                    {"column": "姓名", "type": "string", "role": "identifier",
                     "confidence": 0.9},
                    {"column": "性别", "type": "enum", "role": "enum",
                     "confidence": 0.9, "enum_values": ["男", "女"]},
                    {"column": "联系电话", "type": "phone", "role": "text",
                     "confidence": 0.9},
                    {"column": "备注", "type": "string", "role": "text",
                     "confidence": 0.9},
                ],
            },
            {
                "source_sheet": "幽灵表",
                "key": "ghost",
                "name": "幽灵",
                "key_field": "x",
                "confidence": 0.9,
                "fields": [],
            },
        ],
        "links": [],
    }
    client = ChatClient(LLM_CONFIG, transport=ProposalTransport(proposal))
    result = add_profiles(_workbook([_person_sheet()]), llm_client=client)

    entity = result.sheets[0].inferred_entity
    assert entity is not None
    assert entity.key.isascii() and entity.key.islower()
    # Invalid key_field falls back to the identifier column.
    assert entity.key_field == "姓名"
    joined = " ".join(result.understanding_notes)
    assert "找不到工作表" in joined
    assert "主键列" in joined


def test_proposed_key_with_blanks_falls_back_to_unique_column() -> None:
    """Merged-cell columns (blanks) cannot be keys; a unique column is used."""
    rows = [
        ["1", "小程序", "首页", "150"],
        [None, None, "产品列表", "300"],
        ["2", "管理员端", "用户管理", "100"],
        [None, None, "商品管理", "600"],
        ["3", "运维", "打印机管理", "300"],
        ["4", "资源", "域名", "100"],
        ["5", "资源", "服务器", "300"],
        ["6", "资源", "SSL证书", "158"],
        ["7", "资源", "打印机", "330"],
        ["8", "交易", "订单管理", "150"],
    ]
    sheet = ParsedSheet(
        name="报价单",
        header_row_number=1,
        columns=["序号", "模块", "功能", "金额"],
        rows=rows,
        data_row_count=len(rows),
    )
    proposal = {
        "entities": [
            {
                "source_sheet": "报价单",
                "key": "quote_item",
                "name": "报价明细",
                "key_field": "序号",  # blanks + merged cells -> invalid
                "confidence": 0.9,
                "fields": [
                    {"column": "序号", "type": "number", "role": "identifier",
                     "confidence": 0.85},
                    {"column": "模块", "type": "string", "role": "dimension",
                     "confidence": 0.7},
                    {"column": "功能", "type": "string", "role": "text",
                     "confidence": 0.9},
                    {"column": "金额", "type": "money", "role": "measure",
                     "confidence": 0.9},
                ],
            }
        ],
        "links": [],
    }
    client = ChatClient(LLM_CONFIG, transport=ProposalTransport(proposal))
    result = add_profiles(_workbook([sheet]), llm_client=client)

    entity = result.sheets[0].inferred_entity
    assert entity is not None
    assert entity.key_field == "功能"
    by_column = {field.column: field for field in result.sheets[0].inferred_fields}
    assert by_column["功能"].role == "identifier"
    assert any("主键" in note for note in result.understanding_notes)


def test_entity_without_any_nonnull_key_is_excluded_from_model() -> None:
    # Heavy merged-cell layout: no column is filled on 95% of rows.
    rows: list[list[str | None]] = [
        ["A", "1"],
        [None, None],
        ["B", None],
    ]
    sheet = ParsedSheet(
        name="矩阵",
        header_row_number=1,
        columns=["分类", "档位"],
        rows=rows,
        data_row_count=3,
    )
    proposal = {
        "entities": [
            {
                "source_sheet": "矩阵",
                "key": "matrix",
                "name": "矩阵",
                "key_field": "分类",  # 2/3 non-null -> below the hard gate
                "confidence": 0.9,
                "fields": [
                    {"column": "分类", "type": "string", "role": "identifier",
                     "confidence": 0.85},
                    {"column": "档位", "type": "string", "role": "dimension",
                     "confidence": 0.8},
                ],
            }
        ],
        "links": [],
    }
    client = ChatClient(LLM_CONFIG, transport=ProposalTransport(proposal))
    result = add_profiles(_workbook([sheet]), llm_client=client)
    # No usable key -> the sheet cannot become an entity, so no model at all.
    assert result.business_model is None
    assert any("主键" in note for note in result.understanding_notes)


def test_duplicate_key_still_generates_but_forces_review() -> None:
    # Quote-sheet shape: every line is filled, but 功能 repeats ("无" x4).
    rows: list[list[str | None]] = [
        ["11", "域名", "无", "100"],
        ["12", "服务器", "无", "300"],
        ["13", "SSL证书", "无", "158"],
        ["14", "打印机", "无", "330"],
    ]
    sheet = ParsedSheet(
        name="报价单",
        header_row_number=1,
        columns=["序号", "模块", "功能", "金额"],
        rows=rows,
        data_row_count=len(rows),
    )
    proposal = {
        "entities": [
            {
                "source_sheet": "报价单",
                "key": "quote_item",
                "name": "报价明细",
                "key_field": "功能",
                "confidence": 0.95,
                "fields": [
                    {"column": "序号", "type": "string", "role": "dimension",
                     "confidence": 0.95},
                    {"column": "模块", "type": "string", "role": "dimension",
                     "confidence": 0.9},
                    {"column": "功能", "type": "string", "role": "identifier",
                     "confidence": 0.95},
                    {"column": "金额", "type": "money", "role": "measure",
                     "confidence": 0.95},
                ],
            }
        ],
        "links": [],
    }
    client = ChatClient(LLM_CONFIG, transport=ProposalTransport(proposal))
    result = add_profiles(_workbook([sheet]), llm_client=client)
    assert result.business_model is not None
    entity = result.sheets[0].inferred_entity
    assert entity is not None and entity.key_field == "功能"
    assert entity.needs_review is True
    by_column = {f.column: f for f in result.sheets[0].inferred_fields}
    assert by_column["功能"].needs_review is True
    assert any("重复值" in note for note in result.understanding_notes)


def test_enum_without_value_set_downgrades_to_string() -> None:
    proposal = _person_proposal()
    proposal["entities"][0]["fields"] = [
        {"column": "姓名", "type": "string", "role": "identifier",
         "confidence": 0.9},
        {"column": "性别", "type": "enum", "role": "enum",
         "confidence": 0.9, "enum_values": []},
        {"column": "联系电话", "type": "phone", "role": "text",
         "confidence": 0.9},
        {"column": "备注", "type": "string", "role": "text",
         "confidence": 0.9},
    ]
    client = ChatClient(LLM_CONFIG, transport=ProposalTransport(proposal))
    result = add_profiles(_workbook([_person_sheet()]), llm_client=client)
    gender = next(
        field
        for field in result.sheets[0].inferred_fields
        if field.column == "性别"
    )
    assert gender.type == "string"


def test_llm_links_validated_against_plans_and_review_gated() -> None:
    customer_sheet = ParsedSheet(
        name="客户",
        header_row_number=1,
        columns=["客户名称", "区域"],
        rows=[["甲公司", "华东"], ["乙公司", "华北"]],
        data_row_count=2,
    )
    order_sheet = ParsedSheet(
        name="订单",
        header_row_number=1,
        columns=["订单编号", "客户名称", "金额"],
        rows=[
            ["O1", "甲公司", "100"],
            ["O2", "甲公司", "200"],
            ["O3", "乙公司", "50"],
        ],
        data_row_count=3,
    )
    proposal = {
        "entities": [
            {"source_sheet": "客户", "key": "customer", "name": "客户",
             "key_field": "客户名称", "confidence": 0.9,
             "fields": [
                 {"column": "客户名称", "type": "string", "role": "identifier",
                  "confidence": 0.9},
                 {"column": "区域", "type": "string", "role": "dimension",
                  "confidence": 0.9},
             ]},
            {"source_sheet": "订单", "key": "order", "name": "订单",
             "key_field": "订单编号", "confidence": 0.9,
             "fields": [
                 {"column": "订单编号", "type": "string", "role": "identifier",
                  "confidence": 0.9},
                 {"column": "客户名称", "type": "string", "role": "dimension",
                  "confidence": 0.8},
                 {"column": "金额", "type": "money", "role": "measure",
                  "confidence": 0.9},
             ]},
        ],
        "links": [
            {"from_entity": "customer", "to_entity": "order",
             "on_from": "客户名称", "on_to": "客户名称", "confidence": 0.9},
            {"from_entity": "customer", "to_entity": "ghost",
             "on_from": "客户名称", "on_to": "x", "confidence": 0.9},
        ],
    }
    client = ChatClient(LLM_CONFIG, transport=ProposalTransport(proposal))
    result = add_profiles(
        _workbook([customer_sheet, order_sheet]), llm_client=client
    )

    keys = {(link.from_entity, link.to_entity) for link in result.inferred_links}
    assert ("customer", "order") in keys
    link = next(
        link
        for link in result.inferred_links
        if (link.from_entity, link.to_entity) == ("customer", "order")
    )
    assert link.needs_review is True
    assert link.overlap_recall == 1.0
    assert "幽灵" in " ".join(result.assembly_notes) or "ghost" in " ".join(
        result.assembly_notes
    )
