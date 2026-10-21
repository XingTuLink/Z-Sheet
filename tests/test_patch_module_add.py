"""Acceptance tests for adding a module from one natural-language sentence.

The LLM only proposes structure; the deterministic generator owns entity,
views, navigation, dashboard metric and link wiring. Everything runs offline
through the same parse -> generate -> validate pipeline as production.
"""

from __future__ import annotations

from typing import Any

from backend.ai.provider import ChatClient
from backend.domain.enums import FieldRole, FieldType, LinkType, MetricOp
from backend.domain.models import BusinessModel, DetailView
from backend.patch.explain import explain_intents
from backend.patch.generator import generate_patches
from backend.patch.intent_parser import IntentKind, parse_intents
from backend.patch.validation import validate_patch_snapshot
from backend.storage.repositories.patch_session_repository import PatchSessionPayload
from tests.test_intent_parser import LLM_CONFIG, IntentTransport, make_model
from tests.test_patch_generator import model_without_dashboard

SUPPLIER_FIELDS: list[dict[str, Any]] = [
    {"name": "供应商名称", "type": "string", "role": "identifier"},
    {"name": "联系人", "type": "string", "role": "text"},
    {"name": "联系电话", "type": "phone", "role": "text"},
    {"name": "合作状态", "type": "enum", "role": "enum", "values": ["合作中", "暂停"]},
    {"name": "应付金额", "type": "money", "role": "measure"},
    {"name": "客户名称", "type": "string", "role": "dimension"},
]


def supplier_intent(**overrides: Any) -> dict[str, Any]:
    name = overrides.pop("entity", "供应商")
    params: dict[str, Any] = {
        "key": "supplier",
        "fields": SUPPLIER_FIELDS,
        "metric": {"op": "sum", "field": "应付金额"},
        "links": [{"entity": "客户", "on_field": "客户名称"}],
    }
    params.update(overrides)
    return {"kind": "entity_add", "entity": name, "params": params}


def parse(items: list[dict[str, Any]], text: str = "新增一个供应商模块"):
    chat = ChatClient(LLM_CONFIG, transport=IntentTransport({"intents": items}))
    return parse_intents(make_model(), text, chat)


def build(item: dict[str, Any]):
    result = parse([item])
    plan = generate_patches(make_model(), result)
    return result, plan


def supplier_of(model: BusinessModel):
    return next(entity for entity in model.entities if entity.key == "supplier")


def test_entity_add_creates_module_views_metric_and_link() -> None:
    base = make_model()
    base_snapshot = base.model_dump(mode="json")
    result, plan = build(supplier_intent())

    assert result.unsupported == []
    assert len(result.intents) == 1
    intent = result.intents[0]
    assert intent.kind is IntentKind.ENTITY_ADD
    assert intent.new_entity_name == "供应商"
    assert intent.new_entity_key == "supplier"
    assert plan.requires_confirmation is False
    assert plan.ops
    assert validate_patch_snapshot(base_snapshot, plan.ops) == plan.resulting_snapshot

    model = BusinessModel.model_validate(plan.resulting_snapshot)
    supplier = supplier_of(model)
    assert supplier.name == "供应商"
    assert len(supplier.fields) == len(SUPPLIER_FIELDS)
    assert supplier.key_field == "supplier_name"
    assert [field.name for field in supplier.fields if field.role is FieldRole.IDENTIFIER] == [
        "供应商名称"
    ]

    views = [view for view in model.views if getattr(view, "entity", None) == "supplier"]
    assert {view.kind for view in views} == {"list", "detail", "form"}

    navigation = {item.label: item.view for item in model.navigation}
    assert navigation["供应商"] == "supplier_list"

    metric = next(metric for metric in model.metrics if metric.entity == "supplier")
    payable_key = next(
        field.key for field in intent.new_fields or [] if field.name == "应付金额"
    )
    assert metric.formula.op is MetricOp.SUM
    assert metric.formula.field == payable_key
    dashboards = [view for view in model.views if view.kind == "dashboard"]
    assert dashboards
    assert all(metric.key in dashboard.metrics for dashboard in dashboards)

    link = next(link for link in model.links if link.to == "supplier")
    assert link.from_ == "customer"
    assert link.type is LinkType.ONE_TO_MANY
    assert link.on.from_ == "customer_name"
    assert link.on.to == "customer_name"

    customer_detail = next(
        view
        for view in model.views
        if isinstance(view, DetailView) and view.key == "customer_detail"
    )
    assert any(block.link == link.key for block in customer_detail.blocks)

    assert any("新增「供应商」模块" in note for note in plan.notes)
    assert any("关联" in note for note in plan.notes)

    summary = explain_intents(base, result)[0].summary
    assert "新增「供应商」模块" in summary
    assert "合计指标" in summary


def test_entity_add_survives_patch_session_round_trip() -> None:
    result, plan = build(supplier_intent())
    payload = PatchSessionPayload(
        user_text="新增一个供应商模块",
        parse_result=result,
        ops=plan.ops,
        requires_confirmation=plan.requires_confirmation,
        notes=plan.notes,
    )
    restored = PatchSessionPayload.model_validate(payload.model_dump(mode="json"))
    intent = restored.parse_result.intents[0]
    assert intent.kind is IntentKind.ENTITY_ADD
    assert intent.new_entity_key == "supplier"
    assert intent.new_fields is not None
    assert intent.new_fields[0].name == "供应商名称"
    assert intent.new_links is not None
    assert intent.new_links[0].field_key == "customer_name"


def test_entity_add_rejects_existing_module_name() -> None:
    result = parse([supplier_intent(entity="客户", key="customer2")])
    assert result.intents == []
    assert any("已经有「客户」模块" in note for note in result.notes)


def test_entity_add_requires_exactly_one_identifier() -> None:
    fields = [
        {"name": "供应商名称", "type": "string", "role": "identifier"},
        {"name": "供应商编码", "type": "string", "role": "identifier"},
    ]
    result = parse([supplier_intent(fields=fields)])
    assert result.intents == []
    assert any("只能有一个唯一标识字段" in note for note in result.notes)


def test_entity_add_defaults_identifier_and_key() -> None:
    fields = [
        {"name": "联系人", "type": "string", "role": "text"},
        {"name": "备注", "type": "string", "role": "text"},
    ]
    result = parse([supplier_intent(key=None, fields=fields, links=[], metric=None)])
    assert len(result.intents) == 1
    intent = result.intents[0]
    assert intent.new_fields is not None
    assert intent.new_fields[0].role is FieldRole.IDENTIFIER
    assert intent.new_entity_key == "entity_3"
    assert any("已将第一个字段「联系人」作为标识" in note for note in result.notes)


def test_entity_add_skips_invalid_and_duplicate_fields() -> None:
    fields = [
        {"name": "供应商名称", "type": "string", "role": "identifier"},
        {"name": "合作状态", "type": "enum", "role": "enum"},
        {"name": "供应商名称", "type": "string", "role": "text"},
        {"name": "乱填类型", "type": "banana", "role": "text"},
        {"name": "", "type": "string", "role": "text"},
        "not-a-dict",
    ]
    result = parse([supplier_intent(fields=fields, links=[], metric=None)])
    assert len(result.intents) == 1
    intent = result.intents[0]
    assert intent.new_fields is not None
    assert [field.name for field in intent.new_fields] == ["供应商名称"]
    assert any("缺少非空枚举值" in note for note in result.notes)
    assert any("重复出现" in note for note in result.notes)
    assert any("类型/角色不合法" in note for note in result.notes)


def test_entity_add_drops_unknown_link_target() -> None:
    result, plan = build(
        supplier_intent(links=[{"entity": "仓库", "on_field": "客户名称"}])
    )
    model = BusinessModel.model_validate(plan.resulting_snapshot)
    assert supplier_of(model).key == "supplier"
    assert all(link.to != "supplier" for link in model.links)
    assert any("不存在" in note for note in result.notes)
    assert any("不存在" in note for note in plan.notes)


def test_entity_add_drops_link_field_outside_module() -> None:
    result, plan = build(
        supplier_intent(links=[{"entity": "客户", "on_field": "不存在的字段"}])
    )
    model = BusinessModel.model_validate(plan.resulting_snapshot)
    assert all(link.to != "supplier" for link in model.links)
    assert any("不在新模块字段中" in note for note in result.notes)


def test_entity_add_falls_back_to_count_for_text_metric() -> None:
    result, plan = build(
        supplier_intent(metric={"op": "sum", "field": "联系人"})
    )
    intent = result.intents[0]
    assert intent.metric_op is MetricOp.COUNT
    assert intent.metric_field_key is None
    assert any("已改为记录总数指标" in note for note in result.notes)
    model = BusinessModel.model_validate(plan.resulting_snapshot)
    metric = next(metric for metric in model.metrics if metric.entity == "supplier")
    assert metric.formula.op is MetricOp.COUNT


def test_entity_add_without_dashboard_creates_one() -> None:
    result = parse([supplier_intent(links=[], metric=None)])
    plan = generate_patches(model_without_dashboard(), result)
    model = BusinessModel.model_validate(plan.resulting_snapshot)
    dashboards = [view for view in model.views if view.kind == "dashboard"]
    assert len(dashboards) == 1
    assert model.navigation[0].view == dashboards[0].key
    assert model.navigation[0].label == "首页"


def test_entity_add_generates_deterministic_field_keys() -> None:
    fields = [
        {"name": "供应商名称", "type": "string", "role": "identifier"},
        {"name": "自定义字段", "type": "string", "role": "text"},
        {"name": "自定义字段", "type": "string", "role": "text"},
    ]
    result = parse([supplier_intent(fields=fields, links=[], metric=None)])
    intent = result.intents[0]
    assert intent.new_fields is not None
    assert [field.key for field in intent.new_fields] == [
        "supplier_name",
        "field_2",
    ]
    assert intent.new_fields[1].type is FieldType.STRING
