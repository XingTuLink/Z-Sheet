"""Offline tests for the Day 23 NL Patch Intent Parser.

A fake ChatClient returns canned intent JSON; every test asserts grounding
against a fixed sales Business Model (names -> keys, risk levels, rejection
of V0.1-unsupported requests).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.ai.provider import ChatClient, LLMCallError, LLMConfig
from backend.domain.enums import (
    DetailBlockType,
    FieldRole,
    FieldType,
    LinkType,
    MetricOp,
    SortDir,
)
from backend.domain.models import (
    AppInfo,
    BusinessField,
    BusinessModel,
    DashboardView,
    DetailBlock,
    DetailView,
    Entity,
    Link,
    ListView,
    Metric,
    MetricFormula,
    NavigationItem,
    SortSpec,
)
from backend.patch.intent_parser import (
    IntentKind,
    RiskLevel,
    UnsupportedCategory,
    parse_intents,
)

LLM_CONFIG = LLMConfig(
    base_url="https://api.deepseek.com",
    api_key="sk-test-1234567890",
    model="deepseek-flash",
)


class IntentTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
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
                {"message": {"content": json.dumps(self.response, ensure_ascii=False)}}
            ]
        }


class ExplodingTransport:
    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        raise LLMCallError("boom")


def _field(
    key: str,
    name: str,
    ftype: FieldType,
    role: FieldRole,
    *,
    values: list[str] | None = None,
    confidence: float = 0.9,
) -> BusinessField:
    return BusinessField(
        key=key,
        name=name,
        type=ftype,
        role=role,
        confidence=confidence,
        values=values,
    )


def make_model() -> BusinessModel:
    customer = Entity(
        key="customer",
        name="客户",
        key_field="customer_name",
        fields=[
            _field("customer_name", "客户名称", FieldType.STRING, FieldRole.IDENTIFIER,
                   confidence=0.95),
            _field("region", "所属区域", FieldType.ENUM, FieldRole.ENUM,
                   values=["华东", "华南", "华北", "西南"], confidence=0.7),
            _field("owner", "负责人", FieldType.STRING, FieldRole.DIMENSION),
            _field("phone", "联系电话", FieldType.PHONE, FieldRole.TEXT),
            _field("follow_status", "跟进状态", FieldType.ENUM, FieldRole.ENUM,
                   values=["初访", "跟进中", "已成交"]),
        ],
    )
    order = Entity(
        key="order",
        name="订单",
        key_field="order_no",
        fields=[
            _field("order_no", "订单编号", FieldType.STRING, FieldRole.IDENTIFIER,
                   confidence=0.95),
            _field("customer_name", "客户名称", FieldType.STRING, FieldRole.DIMENSION),
            _field("amount", "订单金额", FieldType.MONEY, FieldRole.MEASURE),
            _field("ordered_at", "下单日期", FieldType.DATE, FieldRole.TIME),
        ],
    )
    link = Link.model_validate(
        {
            "key": "order_customer",
            "from": "order",
            "to": "customer",
            "type": LinkType.ONE_TO_MANY,
            "on": {"from": "customer_name", "to": "customer_name"},
            "confidence": 0.9,
        }
    )
    customer_list = ListView(
        kind="list",
        key="customer_list",
        entity="customer",
        title="客户列表",
        columns=["customer_name", "region", "owner", "phone"],
        search=["customer_name"],
        filters=["region"],
        sorts=[SortSpec(field="customer_name", dir=SortDir.ASC)],
    )
    order_list = ListView(
        kind="list",
        key="order_list",
        entity="order",
        title="订单列表",
        columns=["order_no", "customer_name", "amount", "ordered_at"],
        search=["order_no"],
        filters=[],
        sorts=[],
    )
    customer_detail = DetailView(
        kind="detail",
        key="customer_detail",
        entity="customer",
        title="客户详情",
        blocks=[
            DetailBlock(type=DetailBlockType.FIELDS),
            DetailBlock(type=DetailBlockType.RELATED_LIST, link="order_customer"),
        ],
    )
    dashboard = DashboardView(
        kind="dashboard", key="dashboard", title="首页",
        metrics=["customer_count", "order_count", "total_amount"],
    )
    return BusinessModel(
        app=AppInfo(name="销售管理系统"),
        entities=[customer, order],
        links=[link],
        metrics=[
            Metric(key="customer_count", name="客户总数", entity="customer",
                   formula=MetricFormula(op=MetricOp.COUNT),
                   business_definition="客户记录总条数", confidence=0.9),
            Metric(key="order_count", name="订单总数", entity="order",
                   formula=MetricFormula(op=MetricOp.COUNT),
                   business_definition="订单记录总条数", confidence=0.9),
            Metric(key="total_amount", name="订单金额合计", entity="order",
                   formula=MetricFormula(op=MetricOp.SUM, field="amount"),
                   business_definition="订单金额列全部有效数值之和", confidence=0.8),
        ],
        views=[customer_list, order_list, customer_detail, dashboard],
        navigation=[
            NavigationItem(label="首页", view="dashboard"),
            NavigationItem(label="客户", view="customer_list"),
            NavigationItem(label="订单", view="order_list"),
        ],
    )


def parse(model: BusinessModel, response: dict[str, Any], text: str = "改一下"):
    client = ChatClient(LLM_CONFIG, transport=IntentTransport(response))
    return parse_intents(model, text, client)


# --- grounded model intents -----------------------------------------------------------


def test_view_filter_add_canonical_example() -> None:
    result = parse(
        make_model(),
        {"intents": [
            {"kind": "view_filter_add", "entity": "客户", "field": "跟进状态",
             "reason": "列表加筛选", "confidence": 0.92},
        ]},
        text="客户列表增加跟进状态筛选。",
    )
    assert result.unsupported == []
    intent = result.intents[0]
    assert intent.kind is IntentKind.VIEW_FILTER_ADD
    assert intent.entity_key == "customer"
    assert intent.field_key == "follow_status"
    assert intent.risk is RiskLevel.LOW


def test_multiple_intents_one_sentence_and_sort_dir() -> None:
    result = parse(
        make_model(),
        {"intents": [
            {"kind": "view_filter_add", "entity": "客户", "field": "负责人"},
            {"kind": "view_sort_set", "entity": "客户", "field": "客户名称",
             "params": {"dir": "desc"}},
        ]},
        text="客户列表加负责人筛选，默认按客户名称倒序。",
    )
    kinds = {i.kind for i in result.intents}
    assert kinds == {IntentKind.VIEW_FILTER_ADD, IntentKind.VIEW_SORT_SET}
    sort_intent = next(i for i in result.intents if i.kind is IntentKind.VIEW_SORT_SET)
    assert sort_intent.field_key == "customer_name"
    assert sort_intent.sort_dir == "desc"


def test_related_list_add_with_limit_clamped() -> None:
    model = make_model()
    result = parse(
        model,
        {"intents": [
            {"kind": "related_list_add", "entity": "客户", "child_entity": "订单",
             "params": {"limit": 99}, "confidence": 0.8},
        ]},
        text="客户详情里增加最近99笔订单。",
    )
    intent = result.intents[0]
    assert intent.kind is IntentKind.RELATED_LIST_ADD
    assert intent.child_entity_key == "order"
    assert intent.limit == 50  # clamp to supported range


def test_related_list_default_limit() -> None:
    result = parse(
        make_model(),
        {"intents": [
            {"kind": "related_list_add", "entity": "客户", "child_entity": "订单"},
        ]},
        text="客户详情里增加订单。",
    )
    assert result.intents[0].limit == 10


def test_rename_app_entity_field() -> None:
    model = make_model()
    app = parse(
        model,
        {"intents": [{"kind": "rename", "params": {"new_name": "客户管理系统"}}]},
    )
    assert app.intents[0].new_name == "客户管理系统"
    assert app.intents[0].entity_key is None

    field = parse(
        model,
        {"intents": [
            {"kind": "rename", "entity": "客户", "field": "联系电话",
             "params": {"new_name": "手机号"}},
        ]},
    ).intents[0]
    assert field.entity_key == "customer"
    assert field.field_key == "phone"
    assert field.new_name == "手机号"


def test_metric_add_count_and_sum() -> None:
    model = make_model()
    result = parse(
        model,
        {"intents": [
            {"kind": "metric_add", "entity": "订单",
             "params": {"op": "sum", "field": "订单金额"}},
            {"kind": "metric_add", "entity": "客户", "params": {"op": "count"}},
        ]},
        text="首页增加订单金额合计和客户总数。",
    )
    sum_intent, count_intent = result.intents
    assert sum_intent.metric_op is MetricOp.SUM
    assert sum_intent.metric_field_key == "amount"
    assert sum_intent.field_key is None
    assert count_intent.metric_op is MetricOp.COUNT
    assert count_intent.metric_field_key is None


def test_metric_sum_on_text_field_rejected() -> None:
    result = parse(
        make_model(),
        {"intents": [
            {"kind": "metric_add", "entity": "客户",
             "params": {"op": "sum", "field": "客户名称"}},
        ]},
        text="首页加个客户名称合计。",
    )
    assert result.intents == []
    assert any("数值/金额列" in n for n in result.notes)


def test_metric_remove_by_global_key_without_entity() -> None:
    result = parse(
        make_model(),
        {"intents": [
            {"kind": "metric_remove", "params": {"metric_key": "total_amount"}},
        ]},
        text="把订单金额合计从首页去掉。",
    )
    intent = result.intents[0]
    assert intent.kind is IntentKind.METRIC_REMOVE
    assert intent.entity_key == "order"
    assert intent.target_metric_key == "total_amount"
    assert intent.risk is RiskLevel.HIGH


def test_field_remove_and_identifier_change_are_high_risk() -> None:
    model = make_model()
    result = parse(
        model,
        {"intents": [
            {"kind": "field_remove", "entity": "客户", "field": "联系电话"},
            {"kind": "field_update", "entity": "客户", "field": "负责人",
             "params": {"role": "identifier"}},
            {"kind": "field_update", "entity": "客户", "field": "customer_name",
             "params": {"role": "dimension"}},
        ]},
        text="删掉电话，负责人改主键，原主键降为维度。",
    )
    by_kind = {i.kind: i for i in result.intents}
    assert by_kind[IntentKind.FIELD_REMOVE].risk is RiskLevel.HIGH
    assert by_kind[IntentKind.FIELD_UPDATE].risk is RiskLevel.HIGH
    promoted = next(
        i for i in result.intents
        if i.kind is IntentKind.FIELD_UPDATE and i.field_key == "owner"
    )
    assert promoted.changes_identifier is True
    demoted = next(
        i for i in result.intents
        if i.kind is IntentKind.FIELD_UPDATE and i.field_key == "customer_name"
    )
    assert demoted.changes_identifier is True


def test_field_add_enum_requires_values() -> None:
    result = parse(
        make_model(),
        {"intents": [
            {"kind": "field_add", "entity": "客户",
             "params": {"name": "客户等级", "type": "enum", "role": "enum"}},
            {"kind": "field_add", "entity": "客户",
             "params": {"name": "备注", "type": "string", "role": "text"}},
        ]},
        text="加个客户等级枚举和备注字段。",
    )
    assert len(result.intents) == 1
    added = result.intents[0]
    assert added.kind is IntentKind.FIELD_ADD
    assert added.new_name == "备注"
    assert added.field_type is FieldType.STRING
    assert added.field_role is FieldRole.TEXT
    assert any("枚举值" in n for n in result.notes)


def test_link_remove_resolves_unordered_pair() -> None:
    # The link is declared order->customer; removal says customer->order, so the
    # resolver must match the unordered endpoint pair, not the directed names.
    result = parse(
        make_model(),
        {"intents": [
            {"kind": "link_remove", "entity": "客户", "child_entity": "订单"},
        ]},
        text="删掉客户和订单的关联。",
    )
    intent = result.intents[0]
    assert intent.kind is IntentKind.LINK_REMOVE
    assert intent.entity_key == "customer"
    assert intent.child_entity_key == "order"
    assert intent.risk is RiskLevel.HIGH


def test_unknown_entity_and_ambiguous_field_dropped() -> None:
    result = parse(
        make_model(),
        {"intents": [
            {"kind": "view_filter_add", "entity": "供应商", "field": "名称"},
            {"kind": "view_filter_add", "entity": "客户", "field": "不存在字段"},
        ]},
        text="供应商列表加名称筛选，客户列表加不存在字段筛选。",
    )
    assert result.intents == []
    joined = "\n".join(result.notes)
    assert "供应商" in joined
    assert "不存在字段" in joined


# --- unsupported requests -------------------------------------------------------------


def test_conditional_format_and_data_query_unsupported() -> None:
    result = parse(
        make_model(),
        {"intents": [
            {"kind": "unsupported", "category": "conditional_format",
             "reason": "V0.1 渲染器不支持条件标红",
             "suggestion": "可用枚举标签区分状态"},
            {"kind": "unsupported", "category": "data_query",
             "reason": "临时筛选不是模型修改，列表页可直接筛选华东客户"},
        ]},
        text="把库存低于安全库存的产品标红，把华东的客户单独筛出来。",
    )
    assert result.intents == []
    cats = {u.category for u in result.unsupported}
    assert cats == {UnsupportedCategory.CONDITIONAL_FORMAT, UnsupportedCategory.DATA_QUERY}
    assert all(u.reason for u in result.unsupported)


def test_time_window_metric_unsupported() -> None:
    result = parse(
        make_model(),
        {"intents": [
            {"kind": "unsupported", "category": "time_window_metric",
             "reason": "V0.1 指标不支持本月时间窗"},
        ]},
        text="首页增加本月订单金额。",
    )
    assert result.unsupported[0].category is UnsupportedCategory.TIME_WINDOW_METRIC


def test_unknown_category_coerced() -> None:
    result = parse(
        make_model(),
        {"intents": [{"kind": "unsupported", "category": "teleportation",
                      "reason": "做不到"}], "notes": []},
    )
    assert result.unsupported[0].category is UnsupportedCategory.UNKNOWN


# --- protocol robustness --------------------------------------------------------------


def test_chitchat_yields_note_not_intent() -> None:
    result = parse(make_model(), {"intents": [], "notes": []}, text="你好，这个系统怎么用？")
    assert result.is_empty
    assert result.notes


def test_unknown_kind_dropped_with_note() -> None:
    result = parse(
        make_model(),
        {"intents": [{"kind": "time_travel", "entity": "订单"}]},
    )
    assert result.intents == []
    assert "未知的修改意图类型" in result.notes[0]


def test_llm_network_error_propagates() -> None:
    client = ChatClient(LLM_CONFIG, transport=ExplodingTransport())
    with pytest.raises(LLMCallError):
        parse_intents(make_model(), "客户列表加筛选", client)


def test_empty_text_rejected() -> None:
    client = ChatClient(LLM_CONFIG, transport=IntentTransport({"intents": []}))
    with pytest.raises(ValueError):
        parse_intents(make_model(), "   ", client)
