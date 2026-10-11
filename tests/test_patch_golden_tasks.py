"""Day 27 milestone — fixed NL-patch task acceptance suite.

Fifty-three canned "one sentence -> model change" tasks run the FULL
deterministic pipeline offline (a fake ChatClient stands in for the LLM):

    NL sentence
      -> parse_intents   (grounding: display names -> real keys)
      -> generate_patches (RFC 6902 ops + dry run through BusinessModel)
      -> validate_patch_snapshot (the same core as a real version write)

Every accepted task is additionally checked for renderer health invariants
the domain validators do not cover (one identifier per entity, navigation
targets that exist) and for an exact ops round trip against the untouched
snapshot. Refused tasks never produce ops; ignored tasks are dropped by the
parser with a Chinese note instead of touching the model.

Two more acceptance groups live here:

- a long *sustained editing* session on a real database: nine successive
  sentences, a mid-history rollback, then more edits — the model stays
  healthy after every single version (design: “可持续修改且不破坏模型”);
- a wall of illegal raw JSON Patches against the HTTP API: every one is
  rejected with 422 and never bumps the version.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.ai.provider import ChatClient
from backend.api.main import app
from backend.domain.enums import DetailBlockType, FieldRole
from backend.domain.models import BusinessModel
from backend.patch.generator import PatchGenerationError, PatchPlan, generate_patches
from backend.patch.intent_parser import (
    UnsupportedCategory,
    parse_intents,
)
from backend.patch.validation import validate_patch_snapshot
from backend.storage.db import SessionLocal
from backend.storage.repositories import model_repository as repo

# Canonical offline fixture + canned LLM transport from the Day 23 tests.
from tests.test_intent_parser import LLM_CONFIG, IntentTransport, make_model
from tests.test_patch_generator import (
    model_single_column_list,
    model_without_dashboard,
    model_without_link,
)

client = TestClient(app)
SALES_YAML = (
    Path(__file__).resolve().parents[1] / "examples" / "sales_management.yaml"
).read_text(encoding="utf-8")

Predicate = Callable[[BusinessModel, PatchPlan], bool]

# --- shared helpers -------------------------------------------------------------------


def parse(model: BusinessModel, items: list[dict[str, Any]], text: str):
    chat = ChatClient(LLM_CONFIG, transport=IntentTransport({"intents": items}))
    return parse_intents(model, text, chat)


def entity(model: BusinessModel, key: str):
    return next(e for e in model.entities if e.key == key)


def field(model: BusinessModel, entity_key: str, field_key: str):
    return next(f for f in entity(model, entity_key).fields if f.key == field_key)


def field_named(model: BusinessModel, entity_key: str, name: str):
    return next(f for f in entity(model, entity_key).fields if f.name == name)


def view(model: BusinessModel, key: str) -> Any:
    # Callers address concrete fields (columns / blocks / metrics) that only
    # exist on one view union member; keys already pin the concrete view.
    return next(v for v in model.views if v.key == key)


def op_paths(plan: PatchPlan) -> list[str]:
    return [op["path"] for op in plan.ops]


def check(*predicates: Predicate) -> Callable[[BusinessModel, PatchPlan], None]:
    """Bundle boolean predicates into one (model, plan) assertion callable."""

    def run(model: BusinessModel, plan: PatchPlan) -> None:
        for index, predicate in enumerate(predicates):
            assert predicate(model, plan), f"check #{index} failed"

    return run


def assert_healthy(model: BusinessModel) -> None:
    """Renderer-facing invariants beyond the pydantic reference checks."""
    for ent in model.entities:
        identifiers = [f for f in ent.fields if f.role is FieldRole.IDENTIFIER]
        assert len(identifiers) == 1, (
            f"实体 {ent.key!r} 标识字段数量应为 1，实际 {len(identifiers)}"
        )
        assert ent.key_field == identifiers[0].key
    view_keys = {v.key for v in model.views}
    for nav in model.navigation:
        assert nav.view in view_keys, f"导航指向不存在的视图 {nav.view!r}"


def _variant(transform: Callable[[dict[str, Any]], None]) -> Callable[[], BusinessModel]:
    def build() -> BusinessModel:
        data = make_model().model_dump(mode="json")
        transform(data)
        return BusinessModel.model_validate(data)

    return build


def variant_without_related_block() -> BusinessModel:
    def strip(data: dict[str, Any]) -> None:
        detail = next(v for v in data["views"] if v["key"] == "customer_detail")
        detail["blocks"] = [b for b in detail["blocks"] if b["type"] != "related_list"]

    return _variant(strip)()


def variant_single_block_detail() -> BusinessModel:
    def only_related(data: dict[str, Any]) -> None:
        detail = next(v for v in data["views"] if v["key"] == "customer_detail")
        detail["blocks"] = [
            b for b in detail["blocks"] if b["type"] == "related_list"
        ]

    return _variant(only_related)()


def variant_list_sorted_by_owner() -> BusinessModel:
    def sort_owner(data: dict[str, Any]) -> None:
        data["views"][0]["sorts"] = [{"field": "owner", "dir": "desc"}]

    return _variant(sort_owner)()


# --- expectation DSL ------------------------------------------------------------------


@dataclass
class Apply:
    checks: Callable[[BusinessModel, PatchPlan], None]
    high_risk: bool = False
    unsupported: str | None = None


@dataclass
class Refuse:
    match: str


@dataclass
class Ignored:
    match: str


@dataclass
class Task:
    id: str
    sentence: str
    intents: list[dict[str, Any]]
    expect: Apply | Refuse | Ignored
    variant: Callable[[], BusinessModel] | None = None


def spec(kind: str, **kwargs: Any) -> dict[str, Any]:
    return {"kind": kind, **kwargs}


def field_spec(name: str, **params: Any) -> dict[str, Any]:
    return spec("field_add", entity="客户", params={"name": name, **params})


# --- fixed task table -----------------------------------------------------------------

GOLDEN_TASKS: list[Task] = [
    # ---- rename (3) ------------------------------------------------------------------
    Task("T01", "把系统改名叫客户运营系统", [
        spec("rename", params={"new_name": "客户运营系统"}),
    ], Apply(check(lambda m, _p: m.app.name == "客户运营系统"))),
    Task("T02", "客户这个实体改名叫客户档案", [
        spec("rename", entity="客户", params={"new_name": "客户档案"}),
    ], Apply(check(lambda m, _p: entity(m, "customer").name == "客户档案"))),
    Task("T03", "联系电话改叫手机号", [
        spec("rename", entity="客户", field="联系电话", params={"new_name": "手机号"}),
    ], Apply(check(lambda m, _p: field(m, "customer", "phone").name == "手机号"))),
    # ---- field add (9) ---------------------------------------------------------------
    Task("T04", "客户加一个微信号", [
        field_spec("微信号"),
    ], Apply(check(
        lambda m, _p: field_named(m, "customer", "微信号").type.value == "string",
        lambda m, _p: field_named(m, "customer", "微信号").role is FieldRole.TEXT,
        lambda m, _p: field_named(m, "customer", "微信号").confidence == 1.0,
    ))),
    Task("T05", "订单加一个数值字段折扣率", [
        spec("field_add", entity="订单",
             params={"name": "折扣率", "type": "number", "role": "measure"}),
    ], Apply(check(
        lambda m, _p: field_named(m, "order", "折扣率").type.value == "number",
        lambda m, _p: field_named(m, "order", "折扣率").role is FieldRole.MEASURE,
    ))),
    Task("T06", "客户加一个预计成交额金额字段", [
        spec("field_add", entity="客户",
             params={"name": "预计成交额", "type": "money", "role": "measure"}),
    ], Apply(check(
        lambda m, _p: field_named(m, "customer", "预计成交额").type.value == "money",
    ))),
    Task("T07", "客户加首次接触日期", [
        spec("field_add", entity="客户",
             params={"name": "首次接触日期", "type": "date", "role": "time"}),
    ], Apply(check(
        lambda m, _p: field_named(m, "customer", "首次接触日期").type.value == "date",
        lambda m, _p: field_named(m, "customer", "首次接触日期").role is FieldRole.TIME,
    ))),
    Task("T08", "客户加个备用电话", [
        spec("field_add", entity="客户",
             params={"name": "备用电话", "type": "phone", "role": "text"}),
    ], Apply(check(
        lambda m, _p: field_named(m, "customer", "备用电话").type.value == "phone",
    ))),
    Task("T09", "客户加一个枚举字段客户等级，值是ABC", [
        spec("field_add", entity="客户",
             params={"name": "客户等级", "type": "enum", "role": "enum",
                     "values": ["A级", "B级", "C级"]}),
    ], Apply(check(
        lambda m, _p: field_named(m, "customer", "客户等级").type.value == "enum",
        lambda m, _p: field_named(m, "customer", "客户等级").values == ["A级", "B级", "C级"],
    ))),
    Task("T10", "客户再加一个联系电话字段", [
        field_spec("联系电话", type="string", role="text"),
    ], Refuse("同名字段")),
    Task("T11", "客户加枚举字段信用评级但不给候选值", [
        spec("field_add", entity="客户",
             params={"name": "信用评级", "type": "enum", "role": "enum"}),
    ], Ignored("枚举值")),
    Task("T12", "客户加一个彩虹色字段", [
        spec("field_add", entity="客户",
             params={"name": "彩虹色", "type": "rainbow", "role": "text"}),
    ], Ignored("类型/角色不合法")),
    # ---- field remove (8) ------------------------------------------------------------
    Task("T13", "把客户的联系电话删掉", [
        spec("field_remove", entity="客户", field="联系电话"),
    ], Apply(check(
        lambda m, _p: all(f.key != "phone" for f in entity(m, "customer").fields),
        lambda m, _p: "phone" not in view(m, "customer_list").columns,
        lambda _m, p: "/views/0/columns/3" in op_paths(p),
    ), high_risk=True)),
    Task("T14", "把客户名称这个字段删掉", [
        spec("field_remove", entity="客户", field="客户名称"),
    ], Refuse("唯一标识字段")),
    Task("T15", "删掉订单里的客户名称", [
        spec("field_remove", entity="订单", field="客户名称"),
    ], Refuse("实体关联")),
    Task("T16", "把订单金额字段删了", [
        spec("field_remove", entity="订单", field="金额"),
    ], Apply(check(
        lambda m, _p: all(f.key != "amount" for f in entity(m, "order").fields),
        lambda m, _p: all(met.key != "total_amount" for met in m.metrics),
        lambda m, _p: "total_amount" not in view(m, "dashboard").metrics,
    ), high_risk=True)),
    Task("T17", "删掉客户的所属区域", [
        spec("field_remove", entity="客户", field="所属区域"),
    ], Apply(check(
        lambda m, _p: all(f.key != "region" for f in entity(m, "customer").fields),
        lambda m, _p: view(m, "customer_list").filters == [],
    ), high_risk=True)),
    Task("T18", "删掉跟进状态字段", [
        spec("field_remove", entity="客户", field="跟进状态"),
    ], Apply(check(
        lambda m, _p: all(f.key != "follow_status" for f in entity(m, "customer").fields),
        lambda _m, p: op_paths(p) == ["/entities/0/fields/4"],
    ), high_risk=True)),
    Task("T19", "客户列表只剩一列时还要删联系电话", [
        spec("field_remove", entity="客户", field="联系电话"),
    ], Refuse("展示列"), variant=model_single_column_list),
    Task("T20", "列表按负责人排序时删除负责人字段", [
        spec("field_remove", entity="客户", field="负责人"),
    ], Apply(check(
        lambda m, _p: all(f.key != "owner" for f in entity(m, "customer").fields),
        lambda m, _p: view(m, "customer_list").sorts == [],
        lambda _m, p: "/views/0/sorts/0" in op_paths(p),
    ), high_risk=True), variant=variant_list_sorted_by_owner),
    # ---- field update (5) ------------------------------------------------------------
    Task("T21", "把所属区域从枚举改成普通文本", [
        spec("field_update", entity="客户", field="所属区域",
             params={"type": "string"}),
    ], Apply(check(
        lambda m, _p: field(m, "customer", "region").type.value == "string",
        lambda m, _p: field(m, "customer", "region").values is None,
    ), high_risk=True)),
    Task("T22", "把负责人直接改成枚举类型", [
        spec("field_update", entity="客户", field="负责人",
             params={"type": "enum"}),
    ], Refuse("枚举类型")),
    Task("T23", "把客户名称降级为普通维度字段", [
        spec("field_update", entity="客户", field="客户名称",
             params={"role": "dimension"}),
    ], Refuse("新的唯一标识字段")),
    Task("T24", "客户名称降级，负责人升为唯一标识", [
        spec("field_update", entity="客户", field="客户名称",
             params={"role": "dimension"}),
        spec("field_update", entity="客户", field="负责人",
             params={"role": "identifier"}),
    ], Apply(check(
        lambda m, _p: entity(m, "customer").key_field == "owner",
        lambda m, _p: field(m, "customer", "owner").role is FieldRole.IDENTIFIER,
    ), high_risk=True)),
    Task("T25", "同时把负责人和联系电话都设成唯一标识", [
        spec("field_update", entity="客户", field="负责人",
             params={"role": "identifier"}),
        spec("field_update", entity="客户", field="联系电话",
             params={"role": "identifier"}),
    ], Refuse("多个字段")),
    # ---- list view settings (10) -----------------------------------------------------
    Task("T26", "客户列表增加跟进状态筛选", [
        spec("view_filter_add", entity="客户", field="跟进状态"),
    ], Apply(check(
        lambda m, _p: view(m, "customer_list").filters == ["region", "follow_status"],
    ))),
    Task("T27", "客户列表去掉所属区域筛选", [
        spec("view_filter_remove", entity="客户", field="所属区域"),
    ], Apply(check(
        lambda m, _p: view(m, "customer_list").filters == [],
    ))),
    Task("T28", "客户列表已经有区域筛选还要再加", [
        spec("view_filter_add", entity="客户", field="所属区域"),
    ], Ignored("已存在")),
    Task("T29", "客户列表默认按负责人从新到旧排序", [
        spec("view_sort_set", entity="客户", field="负责人", params={"dir": "desc"}),
    ], Apply(check(
        lambda m, _p: view(m, "customer_list").sorts[0].field == "owner",
        lambda m, _p: view(m, "customer_list").sorts[0].dir.value == "desc",
    ))),
    Task("T30", "客户列表多展示跟进状态这一列", [
        spec("view_column_add", entity="客户", field="跟进状态"),
    ], Apply(check(
        lambda m, _p: "follow_status" in view(m, "customer_list").columns,
    ))),
    Task("T31", "客户列表不展示联系电话了", [
        spec("view_column_remove", entity="客户", field="联系电话"),
    ], Apply(check(
        lambda m, _p: "phone" not in view(m, "customer_list").columns,
    ))),
    Task("T32", "列表只剩一列时还要移除该列", [
        spec("view_column_remove", entity="客户", field="联系电话"),
    ], Refuse("至少需要保留一列"), variant=model_single_column_list),
    Task("T33", "客户列表支持按联系电话搜索", [
        spec("view_search_add", entity="客户", field="联系电话"),
    ], Apply(check(
        lambda m, _p: "phone" in view(m, "customer_list").search,
    ))),
    Task("T34", "订单列表取消金额搜索（本来就没有）", [
        spec("view_search_remove", entity="订单", field="金额"),
    ], Ignored("本就不存在")),
    Task("T35", "客户列表加一个信用评级筛选（字段不存在）", [
        spec("view_filter_add", entity="客户", field="信用评级"),
    ], Ignored("不存在")),
    # ---- related lists (7) -----------------------------------------------------------
    Task("T36", "客户详情里再放一个最近20笔订单关联列表", [
        spec("related_list_add", entity="客户", child_entity="订单",
             params={"limit": 20}),
    ], Ignored("已在详情页")),
    Task("T37", "客户详情加关联订单列表，最近20笔", [
        spec("related_list_add", entity="客户", child_entity="订单",
             params={"limit": 20}),
    ], Apply(check(
        lambda m, _p: any(
            b.type is DetailBlockType.RELATED_LIST and b.link == "order_customer"
            for b in view(m, "customer_detail").blocks
        ),
        lambda _m, p: any("默认条数" in note for note in p.notes),
    )), variant=variant_without_related_block),
    Task("T38", "没有关联时还要加客户的订单关联列表", [
        spec("related_list_add", entity="客户", child_entity="订单"),
    ], Refuse("还没有关联"), variant=model_without_link),
    Task("T39", "在订单详情加客户关联列表（订单没有详情页）", [
        spec("related_list_add", entity="订单", child_entity="客户"),
    ], Refuse("详情视图")),
    Task("T40", "客户详情移除关联订单列表", [
        spec("related_list_remove", entity="客户", child_entity="订单"),
    ], Apply(check(
        lambda m, _p: all(
            b.type is not DetailBlockType.RELATED_LIST
            for b in view(m, "customer_detail").blocks
        ),
    ))),
    Task("T41", "详情页只剩关联区块时还要移除它", [
        spec("related_list_remove", entity="客户", child_entity="订单"),
    ], Refuse("区块"), variant=variant_single_block_detail),
    Task("T42", "没有关联时移除客户的订单关联列表", [
        spec("related_list_remove", entity="客户", child_entity="订单"),
    ], Ignored("不存在关联"), variant=model_without_link),
    # ---- metrics (6) -----------------------------------------------------------------
    Task("T43", "首页再加一个客户总数指标", [
        spec("metric_add", entity="客户", params={"op": "count"}),
    ], Ignored("已存在")),
    Task("T44", "没有首页时新增订单总数指标", [
        spec("metric_add", entity="订单", params={"op": "count"}),
    ], Apply(check(
        lambda m, _p: any(met.key == "order_count" for met in m.metrics),
        lambda m, _p: view(m, "home_dashboard").metrics == ["order_count"],
        lambda m, _p: m.navigation[0].view == "home_dashboard",
    )), variant=model_without_dashboard),
    Task("T45", "给联系电话做一个合计指标", [
        spec("metric_add", entity="客户",
             params={"op": "sum", "field": "联系电话"}),
    ], Ignored("数值/金额")),
    Task("T46", "首页加一个订单平均金额指标", [
        spec("metric_add", entity="订单",
             params={"op": "avg", "field": "金额"}),
    ], Ignored("仅支持")),
    Task("T47", "首页移除订单金额合计指标", [
        spec("metric_remove", entity="订单",
             params={"metric_key": "total_amount"}),
    ], Apply(check(
        lambda m, _p: all(met.key != "total_amount" for met in m.metrics),
        lambda m, _p: "total_amount" not in view(m, "dashboard").metrics,
    ), high_risk=True)),
    Task("T48", "删掉客户总数指标后再重新加回来", [
        spec("metric_remove", entity="客户",
             params={"metric_key": "customer_count"}),
        spec("metric_add", entity="客户", params={"op": "count"}),
    ], Apply(check(
        lambda m, _p: "customer_count" in view(m, "dashboard").metrics,
        lambda m, _p: len(m.metrics) == 3,
    ), high_risk=True)),
    # ---- links + robustness (3) ------------------------------------------------------
    Task("T49", "删除客户和订单之间的关联", [
        spec("link_remove", entity="客户", child_entity="订单"),
    ], Apply(check(
        lambda m, _p: m.links == [],
        lambda _m, p: op_paths(p) == ["/views/2/blocks/1", "/links/0"],
        lambda m, _p: all(
            b.type is not DetailBlockType.RELATED_LIST
            for b in view(m, "customer_detail").blocks
        ),
    ), high_risk=True)),
    Task("T50", "删除客户和发票之间的关联", [
        spec("link_remove", entity="客户", child_entity="发票"),
    ], Ignored("不存在或名称有歧义")),
    Task("T51", "LLM 返回了未知的意图类型", [
        spec("auto_layout", entity="客户"),
    ], Ignored("未知的修改意图类型")),
    # ---- multi-intent + mixed unsupported (2) ----------------------------------------
    Task("T52", "客户列表加跟进状态筛选，并按负责人倒序", [
        spec("view_filter_add", entity="客户", field="跟进状态"),
        spec("view_sort_set", entity="客户", field="负责人", params={"dir": "desc"}),
    ], Apply(check(
        lambda m, _p: "follow_status" in view(m, "customer_list").filters,
        lambda m, _p: view(m, "customer_list").sorts[0].field == "owner",
        lambda m, _p: view(m, "customer_list").sorts[0].dir.value == "desc",
    ))),
    Task("T53", "把张三电话改成12345，顺便把系统改名销售管家", [
        spec("unsupported", category="data_edit",
             reason="这是修改业务数据，不是修改系统结构"),
        spec("rename", params={"new_name": "销售管家"}),
    ], Apply(
        check(lambda m, _p: m.app.name == "销售管家"),
        unsupported="data_edit",
    )),
]


# --- runner ---------------------------------------------------------------------------


@pytest.mark.parametrize("task", GOLDEN_TASKS, ids=lambda t: t.id)
def test_golden_task(task: Task) -> None:
    base_model = task.variant() if task.variant is not None else make_model()
    base_snapshot = base_model.model_dump(mode="json")
    result = parse(base_model, task.intents, task.sentence)

    if isinstance(task.expect, Refuse):
        with pytest.raises(PatchGenerationError, match=task.expect.match):
            generate_patches(base_model, result)
        return

    plan = generate_patches(base_model, result)

    if isinstance(task.expect, Ignored):
        assert plan.is_empty, f"{task.id} 应当不产生任何修改: {plan.notes}"
        notes = [*result.notes, *plan.notes]
        assert any(task.expect.match in note for note in notes), (task.id, notes)
        return

    expected = task.expect
    if expected.unsupported:
        assert [item.category.value for item in result.unsupported] == [
            expected.unsupported
        ]
    else:
        assert result.unsupported == []
    assert plan.requires_confirmation is expected.high_risk, task.id

    # The authoritative run: exact ops against the untouched snapshot.
    assert validate_patch_snapshot(base_snapshot, plan.ops) == plan.resulting_snapshot
    resulting = BusinessModel.model_validate(plan.resulting_snapshot)
    assert_healthy(resulting)
    expected.checks(resulting, plan)


# --- unsupported category coverage ----------------------------------------------------


@pytest.mark.parametrize(
    "category",
    [
        "data_query",
        "data_edit",
        "conditional_format",
        "time_window_metric",
        "entity_change",
        "many_to_many",
        "layout",
        "permission",
        "workflow",
        "bi",
        "excel_sync",
        "unknown",
    ],
)
def test_every_unsupported_category_is_grounded(category: str) -> None:
    result = parse(
        make_model(),
        [spec("unsupported", category=category, reason="超出 V0.1 能力范围")],
        "一句 V0.1 做不到的话",
    )
    assert len(result.unsupported) == 1
    assert result.unsupported[0].category is UnsupportedCategory(category)
    assert result.intents == []


# --- sustained editing session --------------------------------------------------------


def test_sustained_editing_keeps_model_healthy_across_versions_and_rollback() -> None:
    """Nine live-grounded sentences, one mid-history rollback, never broken."""
    app_key = "golden_session"
    db = SessionLocal()
    try:
        repo.import_model(db, app_key, SALES_YAML)
        v1_snapshot = repo.get_current(db, app_key).snapshot

        # (sentence, canned LLM intent items). Each is parsed against the
        # LIVE current model, exactly like the product flow.
        script: list[tuple[str, list[dict[str, Any]]]] = [
            ("客户加微信号", [field_spec("微信号")]),
            ("客户列表支持微信号搜索", [
                spec("view_search_add", entity="客户", field="微信号")]),
            ("系统改名叫客户运营系统", [
                spec("rename", params={"new_name": "客户运营系统"})]),
            ("客户加枚举客户等级", [
                spec("field_add", entity="客户", params={
                    "name": "客户等级", "type": "enum", "role": "enum",
                    "values": ["A级", "B级", "C级"]})]),
            ("客户列表加客户等级筛选", [
                spec("view_filter_add", entity="客户", field="客户等级")]),
            ("客户列表默认按客户名称倒序", [
                spec("view_sort_set", entity="客户", field="客户名称",
                     params={"dir": "desc"})]),
            ("删掉客户的联系电话", [
                spec("field_remove", entity="客户", field="联系电话")]),
            ("订单加运费金额字段", [
                spec("field_add", entity="订单", params={
                    "name": "运费", "type": "money", "role": "measure"})]),
            ("首页加运费合计指标", [
                spec("metric_add", entity="订单",
                     params={"op": "sum", "field": "运费"})]),
        ]

        for sentence, items in script:
            current = repo.snapshot_to_model(repo.get_current(db, app_key))
            result = parse(current, items, sentence)
            assert result.unsupported == [], (sentence, result.unsupported)
            plan = generate_patches(current, result)
            assert not plan.is_empty, (sentence, plan.notes)
            record = repo.apply_patch_as_new_version(
                db, app_key, plan.ops, source_request=sentence
            )
            assert record.source_request == sentence
            assert_healthy(repo.snapshot_to_model(record))

        assert repo.get_current(db, app_key).version == 10

        # Undo the freight experiment: restore the post-phone-removal v8.
        restored = repo.restore_version(db, app_key, 8)
        assert restored.version == 11
        assert restored.snapshot == repo.get_version(db, app_key, 8).snapshot
        assert_healthy(repo.snapshot_to_model(restored))

        # Keep editing the restored model (rename the entity first).
        current = repo.snapshot_to_model(repo.get_current(db, app_key))
        result = parse(
            current,
            [spec("rename", entity="客户", params={"new_name": "客户档案"})],
            "客户改名叫客户档案",
        )
        plan = generate_patches(current, result)
        record = repo.apply_patch_as_new_version(
            db, app_key, plan.ops, source_request="客户改名叫客户档案"
        )
        assert record.version == 12
        assert_healthy(repo.snapshot_to_model(record))

        # Grounding follows the rename: address the entity by its new name.
        current = repo.snapshot_to_model(repo.get_current(db, app_key))
        result = parse(
            current,
            [spec("field_remove", entity="客户档案", field="客户等级")],
            "删掉客户等级字段",
        )
        plan = generate_patches(current, result)
        record = repo.apply_patch_as_new_version(
            db, app_key, plan.ops, source_request="删掉客户等级字段"
        )
        assert record.version == 13
        final_model = repo.snapshot_to_model(record)
        assert_healthy(final_model)

        # Rollback undid the freight work; the cascade removed the level.
        assert all(f.name != "运费" for f in next(
            e for e in final_model.entities if e.key == "order"
        ).fields)
        assert all(f.name != "客户等级" for f in next(
            e for e in final_model.entities if e.key == "customer"
        ).fields)
        assert len(final_model.metrics) == 3

        # The chain stayed append-only; v1 and every intermediate row survive.
        versions = repo.list_versions(db, app_key)
        assert [row.version for row in versions] == list(range(1, 14))
        assert versions[0].snapshot == v1_snapshot
        v5 = repo.snapshot_to_model(repo.get_version(db, app_key, 5))
        assert any(
            f.name == "客户等级"
            for f in next(e for e in v5.entities if e.key == "customer").fields
        )
    finally:
        db.close()


# --- illegal raw patch wall (HTTP) ----------------------------------------------------


ILLEGAL_OPS: list[tuple[str, list[dict[str, Any]]]] = [
    ("remove identifier field", [
        {"op": "remove", "path": "/entities/0/fields/0"},
    ]),
    ("key field points at ghost", [
        {"op": "replace", "path": "/entities/0/key_field", "value": "ghost"},
    ]),
    ("remove all entities", [
        {"op": "remove", "path": "/entities"},
    ]),
    ("field with bogus type", [
        {"op": "add", "path": "/entities/0/fields/-", "value": {
            "key": "x", "name": "X", "type": "rainbow", "role": "text",
            "confidence": 1.0}},
    ]),
    ("out of range index", [
        {"op": "remove", "path": "/entities/9/fields/0"},
    ]),
    ("unknown op", [
        {"op": "frobnicate", "path": "/app/name", "value": "x"},
    ]),
    ("app name wrong type", [
        {"op": "replace", "path": "/app/name", "value": {"weird": True}},
    ]),
    ("list column references ghost field", [
        {"op": "add", "path": "/views/0/columns/-", "value": "ghost"},
    ]),
    ("dashboard card references ghost metric", [
        {"op": "add", "path": "/views/5/metrics/-", "value": "ghost"},
    ]),
    ("detail block references ghost link", [
        {"op": "add", "path": "/views/1/blocks/-", "value": {
            "type": "related_list", "link": "ghost", "title": "幽灵"}},
    ]),
    ("empty op list", []),
    ("op missing path", [{"op": "remove"}]),
]


@pytest.mark.parametrize("name,ops", ILLEGAL_OPS, ids=[n for n, _ in ILLEGAL_OPS])
def test_illegal_raw_patches_are_rejected_and_never_persisted(
    name: str, ops: list[dict[str, Any]]
) -> None:
    app_key = f"wall_{name.replace(' ', '_')}"
    response = client.post(
        "/api/v1/models/import",
        json={"app_key": app_key, "yaml_content": SALES_YAML},
    )
    assert response.status_code == 201, response.text

    response = client.post(f"/api/v1/models/{app_key}/patches", json={"ops": ops})
    assert response.status_code == 422, (name, response.text)

    current = client.get(f"/api/v1/models/{app_key}").json()
    assert current["version"] == 1, name
    assert len(client.get(f"/api/v1/models/{app_key}/versions").json()) == 1
