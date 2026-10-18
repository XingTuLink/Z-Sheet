"""Day 28: realistic demo datasets acceptance (Internal Alpha).

Four messy, real-world-shaped workbooks (sales ledger, inventory, customer
follow-ups, and a non-sales personnel register) must:

1. survive the parser with the expected real rows (banner/total/punctuation
   artifacts tamed, native and dirty cell formats normalized);
2. never crash the deterministic rule engine — and sales-like books must
   still be *grounded* by rules alone (offline fallback);
3. be understood correctly by the LLM layer: a strict, realistic proposal
   (served by a fake offline transport) assembles into a healthy Business
   Model with exactly the expected entities, enums, links and review gates;
4. survive end-to-end Confirm: every acknowledged proposal persists with
   the correct record counts and with dirty money/date cells coerced.

The dataset bytes are imported from the single source of truth at
``examples/demo_data/build_demo_datasets.py``; running that script rewrites
the four .xlsx files used for manual alpha demos.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.ai.provider import ChatClient, LLMConfig
from backend.api.main import app
from backend.domain.enums import FieldRole, FieldType, MetricOp
from backend.domain.models import BusinessModel
from backend.ingestion.parser import parse_workbook
from backend.runtime import record_io
from backend.storage.db import SessionLocal
from backend.storage.repositories import understanding_repository
from backend.understanding.confirmation import (
    WORKSPACE_APP_KEY,
    ConfirmDecisions,
    confirm_workbook,
    unresolved_review_items,
)
from backend.understanding.model_assembly import EntityPlan, build_entity_plans
from backend.understanding.profiling import STAGE_FIELDS, STAGE_SHEETS, add_profiles

_DEMO_DIR = Path(__file__).resolve().parents[1] / "examples" / "demo_data"
_SPEC = importlib.util.spec_from_file_location(
    "demo_datasets", _DEMO_DIR / "build_demo_datasets.py"
)
assert _SPEC is not None and _SPEC.loader is not None
demo = importlib.util.module_from_spec(_SPEC)
# Register before exec: dataclass resolution looks the module up in sys.modules.
sys.modules["demo_datasets"] = demo
_SPEC.loader.exec_module(demo)

client = TestClient(app)

LLM_CONFIG = LLMConfig(
    base_url="https://api.deepseek.com",
    api_key="sk-test-1234567890",
    model="deepseek-flash",
)


class ProposalTransport:
    def __init__(self, proposal: dict[str, Any]) -> None:
        self.proposal = proposal

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        return {
            "choices": [
                {"message": {"content": json.dumps(self.proposal, ensure_ascii=False)}}
            ]
        }


# --- expectations ---------------------------------------------------------------------


@dataclass
class SheetExpectation:
    rows: int
    header_row: int | None = 1
    empty: bool = False
    warnings: tuple[str, ...] = ()


@dataclass
class FieldExpectation:
    entity: str
    column: str
    type: str
    role: str
    enum: list[str] | None = None
    needs_review: bool = False


@dataclass
class LinkExpectation:
    from_entity: str
    to_entity: str
    on_from_column: str
    on_to_column: str


@dataclass
class DatasetExpectation:
    spec: Any
    sheets: dict[str, SheetExpectation]
    proposal: dict[str, Any]
    entities: dict[str, tuple[str, str]]  # key -> (中文名, 主键列名)
    fields: list[FieldExpectation] = field(default_factory=list)
    links: list[LinkExpectation] = field(default_factory=list)
    sum_metrics: dict[str, str] = field(default_factory=dict)  # entity -> 列名
    record_counts: dict[str, int] = field(default_factory=dict)
    # rule-engine grounding (offline fallback) expectations
    rule_plan_keys: set[str] = field(default_factory=set)


def _f(entity: str, column: str, ftype: str, role: str, **kw: Any) -> FieldExpectation:
    return FieldExpectation(entity=entity, column=column, type=ftype, role=role, **kw)


SALES_PROPOSAL = {
    "entities": [
        {
            "source_sheet": "客户", "key": "customer", "name": "客户",
            "key_field": "客户名称", "confidence": 0.95,
            "fields": [
                {"column": "客户名称", "type": "string", "role": "identifier",
                 "confidence": 0.95},
                {"column": "所属区域", "type": "enum", "role": "enum",
                 "enum_values": ["华东", "华南", "华北", "西南"], "confidence": 0.9},
                {"column": "负责人", "type": "string", "role": "dimension",
                 "confidence": 0.9},
                {"column": "联系电话", "type": "phone", "role": "text",
                 "confidence": 0.95},
                {"column": "客户等级", "type": "enum", "role": "enum",
                 "enum_values": ["A级", "B级", "C级"], "confidence": 0.7,
                 "needs_review": True},
            ],
        },
        {
            "source_sheet": "订单", "key": "order", "name": "订单",
            "key_field": "订单编号", "confidence": 0.95,
            "fields": [
                {"column": "订单编号", "type": "string", "role": "identifier",
                 "confidence": 0.95},
                {"column": "客户名称", "type": "string", "role": "dimension",
                 "confidence": 0.9},
                {"column": "金额", "type": "money", "role": "measure",
                 "confidence": 0.95},
                {"column": "下单日期", "type": "date", "role": "time",
                 "confidence": 0.95},
                {"column": "状态", "type": "enum", "role": "enum",
                 "enum_values": ["已完成", "进行中", "已取消"], "confidence": 0.9},
            ],
        },
    ],
    "links": [
        {"from_entity": "customer", "to_entity": "order",
         "on_from": "客户名称", "on_to": "客户名称", "confidence": 0.8,
         "reason": "一个客户多张订单"},
    ],
}

INVENTORY_PROPOSAL = {
    "entities": [
        {
            "source_sheet": "商品", "key": "product", "name": "商品",
            "key_field": "商品编码", "confidence": 0.95,
            "fields": [
                {"column": "商品编码", "type": "string", "role": "identifier",
                 "confidence": 0.95},
                {"column": "商品名称", "type": "string", "role": "dimension",
                 "confidence": 0.9},
                {"column": "分类", "type": "enum", "role": "enum",
                 "enum_values": ["耗材", "设备"], "confidence": 0.9},
                {"column": "规格", "type": "string", "role": "dimension",
                 "confidence": 0.85},
                {"column": "单位", "type": "string", "role": "dimension",
                 "confidence": 0.9},
                {"column": "单价", "type": "money", "role": "measure",
                 "confidence": 0.9},
                {"column": "安全库存", "type": "number", "role": "measure",
                 "confidence": 0.85},
            ],
        },
        {
            "source_sheet": "入库记录", "key": "inbound_record", "name": "入库记录",
            "key_field": "入库单号", "confidence": 0.95,
            "fields": [
                {"column": "入库单号", "type": "string", "role": "identifier",
                 "confidence": 0.95},
                {"column": "商品名称", "type": "string", "role": "dimension",
                 "confidence": 0.9},
                {"column": "入库数量", "type": "number", "role": "measure",
                 "confidence": 0.9},
                {"column": "入库单价", "type": "money", "role": "measure",
                 "confidence": 0.85},
                {"column": "入库金额", "type": "money", "role": "measure",
                 "confidence": 0.95},
                {"column": "入库日期", "type": "date", "role": "time",
                 "confidence": 0.95},
                {"column": "供应商", "type": "string", "role": "dimension",
                 "confidence": 0.7, "needs_review": True},
            ],
        },
    ],
    "links": [
        {"from_entity": "product", "to_entity": "inbound_record",
         "on_from": "商品名称", "on_to": "商品名称", "confidence": 0.8,
         "reason": "一种商品多条入库记录"},
    ],
}

FOLLOWUP_PROPOSAL = {
    "entities": [
        {
            "source_sheet": "客户档案", "key": "customer", "name": "客户",
            "key_field": "客户名称", "confidence": 0.95,
            "fields": [
                {"column": "客户名称", "type": "string", "role": "identifier",
                 "confidence": 0.95},
                {"column": "客户等级", "type": "enum", "role": "enum",
                 "enum_values": ["A级", "B级", "C级"], "confidence": 0.7,
                 "needs_review": True},
                {"column": "行业", "type": "string", "role": "dimension",
                 "confidence": 0.85},
                {"column": "联系电话", "type": "phone", "role": "text",
                 "confidence": 0.9},
                {"column": "负责人", "type": "string", "role": "dimension",
                 "confidence": 0.9},
            ],
        },
        {
            "source_sheet": "跟进记录", "key": "followup", "name": "跟进记录",
            "key_field": "记录编号", "confidence": 0.95,
            "fields": [
                {"column": "记录编号", "type": "string", "role": "identifier",
                 "confidence": 0.95},
                {"column": "客户名称", "type": "string", "role": "dimension",
                 "confidence": 0.9},
                {"column": "跟进方式", "type": "enum", "role": "enum",
                 "enum_values": ["电话", "微信", "上门拜访"], "confidence": 0.9},
                {"column": "跟进内容", "type": "string", "role": "text",
                 "confidence": 0.9},
                {"column": "跟进日期", "type": "date", "role": "time",
                 "confidence": 0.95},
                {"column": "下次跟进日期", "type": "date", "role": "time",
                 "confidence": 0.7, "needs_review": True},
            ],
        },
    ],
    "links": [
        {"from_entity": "customer", "to_entity": "followup",
         "on_from": "客户名称", "on_to": "客户名称", "confidence": 0.8,
         "reason": "一个客户多条跟进记录"},
    ],
}

REVIEWER_PROPOSAL = {
    "entities": [
        {
            "source_sheet": "外审人员登记", "key": "reviewer", "name": "外审人员",
            "key_field": "姓名", "confidence": 0.95,
            "fields": [
                {"column": "序号", "type": "number", "role": "dimension",
                 "confidence": 0.85},
                {"column": "姓名", "type": "string", "role": "identifier",
                 "confidence": 0.95},
                {"column": "性别", "type": "enum", "role": "enum",
                 "enum_values": ["男", "女"], "confidence": 0.95},
                {"column": "所属机构", "type": "string", "role": "dimension",
                 "confidence": 0.9},
                {"column": "职务", "type": "string", "role": "dimension",
                 "confidence": 0.7, "needs_review": True},
                {"column": "联系电话", "type": "phone", "role": "text",
                 "confidence": 0.9},
                {"column": "邮箱", "type": "string", "role": "text",
                 "confidence": 0.9},
                {"column": "登记日期", "type": "date", "role": "time",
                 "confidence": 0.9},
            ],
        }
    ],
    "links": [],
}

EXPECTATIONS: list[DatasetExpectation] = [
    DatasetExpectation(
        spec=demo.SALES_LEDGER,
        sheets={
            "客户": SheetExpectation(rows=6, header_row=3),
            "订单": SheetExpectation(rows=5, warnings=("aggregate",)),
            "说明": SheetExpectation(rows=0, header_row=None, empty=True),
        },
        proposal=SALES_PROPOSAL,
        entities={"customer": ("客户", "客户名称"), "order": ("订单", "订单编号")},
        fields=[
            _f("customer", "所属区域", "enum", "enum",
               enum=["华东", "华南", "华北", "西南"]),
            _f("customer", "客户等级", "enum", "enum",
               enum=["A级", "B级", "C级"], needs_review=True),
            _f("customer", "联系电话", "phone", "text"),
            _f("order", "金额", "money", "measure"),
            _f("order", "下单日期", "date", "time"),
            _f("order", "状态", "enum", "enum",
               enum=["已完成", "进行中", "已取消"]),
            _f("order", "客户名称", "string", "dimension", needs_review=True),
        ],
        links=[LinkExpectation("customer", "order", "客户名称", "客户名称")],
        sum_metrics={"order": "金额"},
        record_counts={"customer": 6, "order": 5},
        rule_plan_keys={"customer", "order"},
    ),
    DatasetExpectation(
        spec=demo.INVENTORY,
        sheets={
            "商品": SheetExpectation(rows=5),
            "入库记录": SheetExpectation(rows=4, warnings=("aggregate",)),
        },
        proposal=INVENTORY_PROPOSAL,
        entities={
            "product": ("商品", "商品编码"),
            "inbound_record": ("入库记录", "入库单号"),
        },
        fields=[
            _f("product", "分类", "enum", "enum", enum=["耗材", "设备"]),
            _f("product", "单价", "money", "measure"),
            _f("product", "安全库存", "number", "measure"),
            _f("inbound_record", "入库数量", "number", "measure"),
            _f("inbound_record", "入库单价", "money", "measure"),
            _f("inbound_record", "入库金额", "money", "measure"),
            _f("inbound_record", "入库日期", "date", "time"),
            _f("inbound_record", "供应商", "string", "dimension",
               needs_review=True),
            _f("inbound_record", "商品名称", "string", "dimension",
               needs_review=True),
        ],
        links=[LinkExpectation(
            "product", "inbound_record", "商品名称", "商品名称")],
        sum_metrics={"inbound_record": "入库金额"},
        record_counts={"product": 5, "inbound_record": 4},
        rule_plan_keys={"product", "order"},
    ),
    DatasetExpectation(
        spec=demo.CUSTOMER_FOLLOWUP,
        sheets={
            "客户档案": SheetExpectation(rows=5),
            "跟进记录": SheetExpectation(rows=5),
        },
        proposal=FOLLOWUP_PROPOSAL,
        entities={
            "customer": ("客户", "客户名称"),
            "followup": ("跟进记录", "记录编号"),
        },
        fields=[
            _f("customer", "客户等级", "enum", "enum",
               enum=["A级", "B级", "C级"], needs_review=True),
            _f("customer", "联系电话", "phone", "text"),
            _f("followup", "跟进方式", "enum", "enum",
               enum=["电话", "微信", "上门拜访"]),
            _f("followup", "跟进内容", "string", "text"),
            _f("followup", "跟进日期", "date", "time"),
            _f("followup", "下次跟进日期", "date", "time", needs_review=True),
            _f("followup", "客户名称", "string", "dimension", needs_review=True),
        ],
        links=[LinkExpectation(
            "customer", "followup", "客户名称", "客户名称")],
        record_counts={"customer": 5, "followup": 5},
        rule_plan_keys={"customer"},
    ),
    DatasetExpectation(
        spec=demo.REVIEW_STAFF,
        sheets={"外审人员登记": SheetExpectation(rows=5)},
        proposal=REVIEWER_PROPOSAL,
        entities={"reviewer": ("外审人员", "姓名")},
        fields=[
            _f("reviewer", "序号", "number", "dimension"),
            _f("reviewer", "姓名", "string", "identifier"),
            _f("reviewer", "性别", "enum", "enum", enum=["男", "女"]),
            _f("reviewer", "所属机构", "string", "dimension"),
            _f("reviewer", "职务", "string", "dimension", needs_review=True),
            _f("reviewer", "联系电话", "phone", "text"),
            _f("reviewer", "邮箱", "string", "text"),
            _f("reviewer", "登记日期", "date", "time"),
        ],
        record_counts={"reviewer": 5},
    ),
]


# --- helpers --------------------------------------------------------------------------


def _bytes(exp: DatasetExpectation) -> bytes:
    return demo.dataset_bytes(exp.spec)


def _llm_result(exp: DatasetExpectation):
    workbook = parse_workbook(exp.spec.file_name, _bytes(exp))
    return add_profiles(
        workbook,
        llm_client=ChatClient(LLM_CONFIG, transport=ProposalTransport(exp.proposal)),
    )


def _plan_by_key(result, key: str) -> EntityPlan:
    plans, _ = build_entity_plans(result)
    return next(plan for plan in plans if plan.key == key)


def _model(result) -> BusinessModel:
    assert result.business_model is not None
    return BusinessModel.model_validate(result.business_model)


def _assert_healthy(model: BusinessModel) -> None:
    for ent in model.entities:
        identifiers = [f for f in ent.fields if f.role is FieldRole.IDENTIFIER]
        assert len(identifiers) == 1, (ent.key, len(identifiers))
        assert ent.key_field == identifiers[0].key
    view_keys = {v.key for v in model.views}
    for nav in model.navigation:
        assert nav.view in view_keys


def _review_decisions(result) -> dict[str, Any]:
    plans, _ = build_entity_plans(result)
    model = _model(result)
    prefixed = unresolved_review_items(
        plans, model, ConfirmDecisions.model_validate({})
    )
    acknowledged = [
        item if item.startswith("metric:") else item.split(":", 1)[1]
        for item in prefixed
    ]
    links = {
        link.key: {"decision": "accepted",
                   "on_from": link.on_from, "on_to": link.on_to}
        for link in result.inferred_links
    }
    return {"acknowledged": acknowledged, "links": links}


# --- 1. parser: real-world flaws are tamed --------------------------------------------


@pytest.mark.parametrize("exp", EXPECTATIONS, ids=lambda e: e.spec.file_name)
def test_parser_tames_real_world_flaws(exp: DatasetExpectation) -> None:
    workbook = parse_workbook(exp.spec.file_name, _bytes(exp))
    assert [sheet.name for sheet in workbook.sheets] == list(exp.sheets)
    for sheet in workbook.sheets:
        expected = exp.sheets[sheet.name]
        assert sheet.data_row_count == expected.rows, sheet.name
        assert sheet.is_empty is expected.empty, sheet.name
        assert sheet.header_row_number == expected.header_row, sheet.name
        for fragment in expected.warnings:
            assert any(fragment in warning for warning in sheet.warnings), (
                sheet.name,
                fragment,
                sheet.warnings,
            )
    # No punctuation/total artifact survives as a business row.
    artifacts = {".", "-", "/", "合计", "小计"}
    for sheet in workbook.sheets:
        for row in sheet.rows:
            texts = {cell for cell in row if cell}
            assert not (texts & artifacts), (sheet.name, row)
    # The whitespace-padded enum value is trimmed back to its canonical form.
    customer = next(s for s in workbook.sheets if s.name == "客户") if any(
        s.name == "客户" for s in workbook.sheets
    ) else None
    if customer is not None:
        region_pos = customer.columns.index("所属区域")
        assert " 华南 " not in {row[region_pos] for row in customer.rows}


def test_sales_ledger_streams_real_stage_counts() -> None:
    response = client.post(
        "/api/v1/ingestion/parse/stream",
        files={"file": (demo.SALES_LEDGER.file_name, _bytes(EXPECTATIONS[0]),
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet")},
    )
    assert response.status_code == 200
    parsed: list[tuple[str, str]] = []
    for block in response.text.strip().split("\n\n"):
        event = data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        parsed.append((event, data))
    stages = {
        json.loads(data)["key"]: json.loads(data)["count"]
        for event, data in parsed[:-1]
        if event == "stage"
    }
    assert stages[STAGE_SHEETS] == 3
    assert stages[STAGE_FIELDS] == 10  # 5 customer + 5 order columns
    final = json.loads(parsed[-1][1])
    assert final["business_model"] is not None


# --- 2. rules engine: offline fallback never crashes, sales-like books ground ----------


@pytest.mark.parametrize("spec", demo.DATASETS, ids=lambda s: s.file_name)
def test_rules_engine_survives_every_dataset(spec: Any) -> None:
    workbook = parse_workbook(spec.file_name, demo.dataset_bytes(spec))
    result = add_profiles(workbook, llm_client=None)
    assert result.understanding_engine == "rules"
    if result.business_model is not None:
        # pydantic reference integrity must pass; the stricter
        # exactly-one-identifier renderer invariant only applies to LLM-reviewed
        # models (out-of-vocabulary rule candidates can carry several
        # identifier-role columns into review — see the personnel case).
        BusinessModel.model_validate(result.business_model)


def test_rules_engine_grounds_sales_like_books() -> None:
    for exp, link_count in (
        (EXPECTATIONS[0], 1),  # sales ledger: customer -> order link
        (EXPECTATIONS[1], 0),  # inbound children are unique -> no hard 1:N
        (EXPECTATIONS[2], 1),  # 示例地产A has two follow-ups -> 1:N fires
    ):
        workbook = parse_workbook(exp.spec.file_name, _bytes(exp))
        result = add_profiles(workbook, llm_client=None)
        plans, _ = build_entity_plans(result)
        assert exp.rule_plan_keys <= {plan.key for plan in plans}, exp.spec.file_name
        assert len(result.inferred_links) == link_count, exp.spec.file_name


def test_rules_engine_flags_personnel_register_for_human_review() -> None:
    # The non-sales sheet is outside the frozen customer/order/product
    # vocabulary: rules may propose a weak candidate, but it MUST be
    # review-gated rather than silently shipped as a confident entity.
    exp = EXPECTATIONS[3]
    workbook = parse_workbook(exp.spec.file_name, _bytes(exp))
    result = add_profiles(workbook, llm_client=None)
    sheet = result.sheets[0]
    assert sheet.inferred_entity is not None
    assert sheet.inferred_entity.needs_review is True
    assert sheet.inferred_entity.confidence < 0.85


# --- 3. LLM layer: every dataset is understood into the expected model -----------------


@pytest.mark.parametrize("exp", EXPECTATIONS, ids=lambda e: e.spec.file_name)
def test_llm_understands_each_dataset_into_healthy_model(
    exp: DatasetExpectation,
) -> None:
    result = _llm_result(exp)
    assert result.understanding_engine == "llm", result.understanding_notes
    model = _model(result)
    _assert_healthy(model)

    plans = {plan.key: plan for plan in build_entity_plans(result)[0]}
    assert set(plans) == set(exp.entities)
    for key, (name, key_column) in exp.entities.items():
        ent = next(e for e in model.entities if e.key == key)
        assert ent.name == name
        assert ent.key_field == plans[key].field_keys[key_column]

    for expected_field in exp.fields:
        plan = plans[expected_field.entity]
        entity = next(e for e in model.entities if e.key == expected_field.entity)
        field = next(
            f for f in entity.fields
            if f.key == plan.field_keys[expected_field.column]
        )
        assert field.type is FieldType(expected_field.type), (
            expected_field.entity, expected_field.column, field.type
        )
        assert field.role is FieldRole(expected_field.role), (
            expected_field.entity, expected_field.column, field.role
        )
        assert field.needs_review is expected_field.needs_review, (
            expected_field.entity, expected_field.column
        )
        if expected_field.enum is not None:
            assert field.values == expected_field.enum

    assert len(model.links) == len(exp.links)
    for expected_link in exp.links:
        link = next(
            cand for cand in model.links
            if cand.from_ == expected_link.from_entity
            and cand.to == expected_link.to_entity
        )
        parent_plan = plans[expected_link.from_entity]
        child_plan = plans[expected_link.to_entity]
        # Links always land on the parent's business key field, not on the
        # same-named display column the user described.
        assert link.on.from_ == parent_plan.key_field_key
        assert link.on.to == child_plan.field_keys[expected_link.on_to_column]
        # Spreadsheet guesses are always review-gated (no FK in the source).
        inferred = next(
            cand for cand in result.inferred_links
            if cand.from_entity == expected_link.from_entity
            and cand.to_entity == expected_link.to_entity
        )
        assert inferred.needs_review is True

    for entity_key, column in exp.sum_metrics.items():
        field_key = plans[entity_key].field_keys[column]
        assert any(
            metric.entity == entity_key
            and metric.formula.op is MetricOp.SUM
            and metric.formula.field == field_key
            for metric in model.metrics
        )


# --- 4. end-to-end Confirm: real rows persist with dirty values coerced ----------------


@pytest.mark.parametrize("exp", EXPECTATIONS, ids=lambda e: e.spec.file_name)
def test_confirm_persists_each_dataset_with_its_real_rows(
    exp: DatasetExpectation,
) -> None:
    content = _bytes(exp)
    result = _llm_result(exp)
    db = SessionLocal()
    try:
        understanding_repository.save_understanding(
            db, understanding_repository.session_key(content), result
        )
        summary = confirm_workbook(
            db, exp.spec.file_name, content, _review_decisions(result)
        )
        assert summary.app_key == WORKSPACE_APP_KEY
        assert summary.record_counts == exp.record_counts
        assert summary.links_accepted == [
            f"{link.from_entity}_to_{link.to_entity}"
            for link in result.inferred_links
        ]

        data = record_io.load_runtime_data(db, WORKSPACE_APP_KEY)
        for entity_key, count in exp.record_counts.items():
            assert len(data.records[entity_key]) == count
    finally:
        db.close()


def test_dirty_money_is_coerced_after_confirm() -> None:
    exp = EXPECTATIONS[0]
    content = _bytes(exp)
    result = _llm_result(exp)
    db = SessionLocal()
    try:
        understanding_repository.save_understanding(
            db, understanding_repository.session_key(content), result
        )
        confirm_workbook(db, exp.spec.file_name, content, _review_decisions(result))
        data = record_io.load_runtime_data(db, WORKSPACE_APP_KEY)
        amount_key = _plan_by_key(result, "order").field_keys["金额"]
        order_no_key = _plan_by_key(result, "order").field_keys["订单编号"]
        row = next(
            row for row in data.records["order"]
            if row[order_no_key] == "SO-2026-001"
        )
        assert row[amount_key] == 12800
    finally:
        db.close()


def test_chinese_style_date_is_normalized_after_confirm() -> None:
    exp = EXPECTATIONS[2]
    content = _bytes(exp)
    result = _llm_result(exp)
    db = SessionLocal()
    try:
        understanding_repository.save_understanding(
            db, understanding_repository.session_key(content), result
        )
        confirm_workbook(db, exp.spec.file_name, content, _review_decisions(result))
        data = record_io.load_runtime_data(db, WORKSPACE_APP_KEY)
        plan = _plan_by_key(result, "followup")
        date_key = plan.field_keys["跟进日期"]
        id_key = plan.field_keys["记录编号"]
        row = next(
            row for row in data.records["followup"] if row[id_key] == "GJ-003"
        )
        assert row[date_key] == "2026-09-12"
    finally:
        db.close()
