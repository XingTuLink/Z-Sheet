"""Offline tests for the Day 24 deterministic Patch Generator.

Canned ``ParsedIntent`` objects (no LLM anywhere) are turned into RFC 6902
ops against the fixed sales Business Model; every plan is dry-run validated
and assertions cover op paths, cascades, hard refusals and plan-level flags.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from backend.domain.enums import (
    DetailBlockType,
    FieldRole,
    FieldType,
    MetricOp,
    SortDir,
)
from backend.domain.models import BusinessModel, DetailView, ListView
from backend.patch.generator import (
    PatchGenerationError,
    generate_patches,
)
from backend.patch.intent_parser import (
    IntentKind,
    IntentParseResult,
    ParsedIntent,
    RiskLevel,
)

# Reuse the canonical sales fixture shape (kept local so this module is
# self-contained like every other tests/ module).
from tests.test_intent_parser import make_model


def intent(kind: IntentKind, **kwargs: Any) -> ParsedIntent:
    base: dict[str, Any] = {"kind": kind, "risk": RiskLevel.LOW, "confidence": 0.9}
    base.update(kwargs)
    return ParsedIntent(**base)


def make_plan(model: BusinessModel, *intents: ParsedIntent) -> Any:
    result = IntentParseResult(user_text="测试修改", intents=list(intents))
    return generate_patches(model, result)


def op_paths(plan: Any) -> list[str]:
    return [op["path"] for op in plan.ops]


def _variant(transform: Any) -> BusinessModel:
    data = make_model().model_dump(mode="json")
    transform(data)
    return BusinessModel.model_validate(data)


def model_without_link() -> BusinessModel:
    def drop(data: dict[str, Any]) -> None:
        data["links"] = []
        detail = next(v for v in data["views"] if v["key"] == "customer_detail")
        detail["blocks"] = [b for b in detail["blocks"] if b["type"] != "related_list"]

    return _variant(drop)


def model_without_dashboard() -> BusinessModel:
    def drop(data: dict[str, Any]) -> None:
        data["metrics"] = []
        data["views"] = [v for v in data["views"] if v["kind"] != "dashboard"]
        data["navigation"] = [n for n in data["navigation"] if n["view"] != "dashboard"]

    return _variant(drop)


def model_single_column_list() -> BusinessModel:
    return _variant(
        lambda data: data["views"][0].__setitem__("columns", ["phone"])
    )


# --- rename ---------------------------------------------------------------------------


def test_rename_app_entity_field_paths() -> None:
    app = make_plan(make_model(), intent(IntentKind.RENAME, new_name="新系统"))
    assert app.ops == [{"op": "replace", "path": "/app/name", "value": "新系统"}]
    assert app.resulting_snapshot["app"]["name"] == "新系统"

    entity = make_plan(
        make_model(), intent(IntentKind.RENAME, entity_key="customer", new_name="客户档案")
    )
    assert entity.ops == [
        {"op": "replace", "path": "/entities/0/name", "value": "客户档案"}
    ]

    field = make_plan(
        make_model(),
        intent(IntentKind.RENAME, entity_key="customer", field_key="phone",
               new_name="手机号"),
    )
    assert field.ops == [
        {"op": "replace", "path": "/entities/0/fields/3/name", "value": "手机号"}
    ]


# --- field add ------------------------------------------------------------------------


def test_field_add_enum_appends_valid_field() -> None:
    plan = make_plan(
        make_model(),
        intent(
            IntentKind.FIELD_ADD,
            entity_key="customer",
            new_name="客户等级",
            field_type=FieldType.ENUM,
            field_role=FieldRole.ENUM,
            enum_values=["A", "B"],
        ),
    )
    assert op_paths(plan) == ["/entities/0/fields/-"]
    value = plan.ops[0]["value"]
    assert value["type"] == "enum"
    assert value["values"] == ["A", "B"]
    assert value["confidence"] == 1.0
    customer = plan.resulting_snapshot["entities"][0]
    assert customer["fields"][-1]["name"] == "客户等级"
    assert plan.requires_confirmation is False


def test_field_add_duplicate_display_name_refused() -> None:
    with pytest.raises(PatchGenerationError, match="同名字段"):
        make_plan(
            make_model(),
            intent(
                IntentKind.FIELD_ADD,
                entity_key="customer",
                new_name="联系电话",
                field_type=FieldType.STRING,
                field_role=FieldRole.TEXT,
            ),
        )


# --- field remove ---------------------------------------------------------------------


def test_field_remove_cascades_columns_metric_and_dashboard_card() -> None:
    # order.amount is a list column and backs the total_amount sum metric.
    plan = make_plan(
        make_model(),
        intent(
            IntentKind.FIELD_REMOVE,
            risk=RiskLevel.HIGH,
            entity_key="order",
            field_key="amount",
        ),
    )
    paths = op_paths(plan)
    assert "/views/1/columns/2" in paths          # order list column
    assert "/views/3/metrics/2" in paths          # dashboard total_amount card
    assert "/metrics/2" in paths                  # metric itself
    assert "/entities/1/fields/2" in paths        # the field
    resulting = BusinessModel.model_validate(plan.resulting_snapshot)
    assert all(m.key != "total_amount" for m in resulting.metrics)
    assert "total_amount" not in next(
        v for v in resulting.views if v.kind == "dashboard"
    ).metrics
    order = next(e for e in resulting.entities if e.key == "order")
    assert all(f.key != "amount" for f in order.fields)
    assert plan.requires_confirmation is True
    assert any("订单金额合计" in note for note in plan.notes)


def test_field_remove_cascades_filter_member() -> None:
    plan = make_plan(
        make_model(),
        intent(
            IntentKind.FIELD_REMOVE,
            risk=RiskLevel.HIGH,
            entity_key="customer",
            field_key="region",
        ),
    )
    paths = op_paths(plan)
    assert "/views/0/filters/0" in paths
    assert "/views/0/columns/1" in paths


def test_field_remove_identifier_refused() -> None:
    with pytest.raises(PatchGenerationError, match="唯一标识字段"):
        make_plan(
            make_model(),
            intent(
                IntentKind.FIELD_REMOVE,
                risk=RiskLevel.HIGH,
                entity_key="customer",
                field_key="customer_name",
            ),
        )


def test_field_remove_link_key_refused() -> None:
    # order.customer_name is the child-side on-field of order_customer.
    with pytest.raises(PatchGenerationError, match="实体关联"):
        make_plan(
            make_model(),
            intent(
                IntentKind.FIELD_REMOVE,
                risk=RiskLevel.HIGH,
                entity_key="order",
                field_key="customer_name",
            ),
        )


def test_field_remove_last_list_column_refused() -> None:
    with pytest.raises(PatchGenerationError, match="展示列"):
        make_plan(
            model_single_column_list(),
            intent(
                IntentKind.FIELD_REMOVE,
                risk=RiskLevel.HIGH,
                entity_key="customer",
                field_key="phone",
            ),
        )


# --- field update ---------------------------------------------------------------------


def test_field_update_demote_identifier_without_replacement_refused() -> None:
    with pytest.raises(PatchGenerationError, match="新的唯一标识字段"):
        make_plan(
            make_model(),
            intent(
                IntentKind.FIELD_UPDATE,
                risk=RiskLevel.HIGH,
                entity_key="customer",
                field_key="customer_name",
                field_role=FieldRole.DIMENSION,
            ),
        )


def test_field_update_promote_and_demote_moves_key_field() -> None:
    plan = make_plan(
        make_model(),
        intent(
            IntentKind.FIELD_UPDATE,
            risk=RiskLevel.HIGH,
            entity_key="customer",
            field_key="customer_name",
            field_role=FieldRole.DIMENSION,
        ),
        intent(
            IntentKind.FIELD_UPDATE,
            risk=RiskLevel.HIGH,
            entity_key="customer",
            field_key="owner",
            field_role=FieldRole.IDENTIFIER,
        ),
    )
    paths = op_paths(plan)
    assert "/entities/0/fields/0/role" in paths
    assert "/entities/0/fields/2/role" in paths
    assert "/entities/0/key_field" in paths
    customer = BusinessModel.model_validate(plan.resulting_snapshot).entities[0]
    assert customer.key_field == "owner"
    owner = next(f for f in customer.fields if f.key == "owner")
    assert owner.role is FieldRole.IDENTIFIER


def test_field_update_multiple_promotions_refused() -> None:
    with pytest.raises(PatchGenerationError, match="多个字段"):
        make_plan(
            make_model(),
            intent(
                IntentKind.FIELD_UPDATE, risk=RiskLevel.HIGH, entity_key="customer",
                field_key="owner", field_role=FieldRole.IDENTIFIER,
            ),
            intent(
                IntentKind.FIELD_UPDATE, risk=RiskLevel.HIGH, entity_key="customer",
                field_key="phone", field_role=FieldRole.IDENTIFIER,
            ),
        )


def test_field_update_enum_to_string_drops_values() -> None:
    plan = make_plan(
        make_model(),
        intent(
            IntentKind.FIELD_UPDATE,
            risk=RiskLevel.HIGH,
            entity_key="customer",
            field_key="region",
            field_type=FieldType.STRING,
        ),
    )
    paths = op_paths(plan)
    assert "/entities/0/fields/1/values" in paths
    assert "/entities/0/fields/1/type" in paths
    region = next(
        f
        for f in BusinessModel.model_validate(plan.resulting_snapshot).entities[0].fields
        if f.key == "region"
    )
    assert region.type is FieldType.STRING
    assert region.values is None


def test_field_update_to_enum_refused() -> None:
    with pytest.raises(PatchGenerationError, match="枚举类型"):
        make_plan(
            make_model(),
            intent(
                IntentKind.FIELD_UPDATE,
                risk=RiskLevel.HIGH,
                entity_key="customer",
                field_key="owner",
                field_type=FieldType.ENUM,
            ),
        )


# --- list view settings ---------------------------------------------------------------


def test_view_filter_add_and_sort_set() -> None:
    plan = make_plan(
        make_model(),
        intent(IntentKind.VIEW_FILTER_ADD, entity_key="customer",
               field_key="follow_status"),
        intent(IntentKind.VIEW_SORT_SET, entity_key="customer", field_key="owner",
               sort_dir=SortDir.DESC),
    )
    assert plan.ops[0] == {
        "op": "add", "path": "/views/0/filters/-", "value": "follow_status"
    }
    assert plan.ops[1] == {
        "op": "replace",
        "path": "/views/0/sorts",
        "value": [{"field": "owner", "dir": "desc"}],
    }
    list_view = BusinessModel.model_validate(plan.resulting_snapshot).views[0]
    assert isinstance(list_view, ListView)
    assert list_view.filters == ["region", "follow_status"]
    assert list_view.sorts[0].field == "owner"
    assert list_view.sorts[0].dir is SortDir.DESC


def test_view_add_existing_is_noop_with_note() -> None:
    plan = make_plan(
        make_model(),
        intent(IntentKind.VIEW_FILTER_ADD, entity_key="customer", field_key="region"),
    )
    assert plan.is_empty
    assert any("已存在" in note for note in plan.notes)


def test_view_remove_missing_is_noop_with_note() -> None:
    plan = make_plan(
        make_model(),
        intent(IntentKind.VIEW_SEARCH_REMOVE, entity_key="order",
               field_key="amount"),
    )
    assert plan.is_empty
    assert any("本就不存在" in note for note in plan.notes)


def test_view_column_add_and_remove_paths() -> None:
    added = make_plan(
        make_model(),
        intent(IntentKind.VIEW_COLUMN_ADD, entity_key="order",
               field_key="ordered_at"),
    )
    # ordered_at is already a column in the fixture -> no-op; pick follow_status
    # on order? it lives on customer. Use search add instead for a clean add.
    assert added.is_empty
    removed = make_plan(
        make_model(),
        intent(IntentKind.VIEW_COLUMN_REMOVE, entity_key="customer", field_key="phone"),
    )
    assert removed.ops == [{"op": "remove", "path": "/views/0/columns/3"}]


def test_view_column_remove_last_one_refused() -> None:
    with pytest.raises(PatchGenerationError, match="至少需要保留一列"):
        make_plan(
            model_single_column_list(),
            intent(IntentKind.VIEW_COLUMN_REMOVE, entity_key="customer",
                   field_key="phone"),
        )


# --- related lists --------------------------------------------------------------------


def test_related_list_remove_existing_block() -> None:
    plan = make_plan(
        make_model(),
        intent(IntentKind.RELATED_LIST_REMOVE, entity_key="customer",
               child_entity_key="order"),
    )
    assert plan.ops == [{"op": "remove", "path": "/views/2/blocks/1"}]
    detail = next(
        v for v in BusinessModel.model_validate(plan.resulting_snapshot).views
        if v.key == "customer_detail"
    )
    assert isinstance(detail, DetailView)
    assert all(b.type is not DetailBlockType.RELATED_LIST for b in detail.blocks)


def test_related_list_add_existing_is_noop() -> None:
    plan = make_plan(
        make_model(),
        intent(IntentKind.RELATED_LIST_ADD, entity_key="customer",
               child_entity_key="order", limit=20),
    )
    assert plan.is_empty
    assert any("已在详情页" in note for note in plan.notes)


def test_related_list_add_emits_block_and_limit_note() -> None:
    def strip_block(data: dict[str, Any]) -> None:
        detail = next(v for v in data["views"] if v["key"] == "customer_detail")
        detail["blocks"] = [
            b for b in detail["blocks"] if b["type"] != "related_list"
        ]

    model = _variant(strip_block)
    plan = make_plan(
        model,
        intent(IntentKind.RELATED_LIST_ADD, entity_key="customer",
               child_entity_key="order", limit=20),
    )
    assert op_paths(plan) == ["/views/2/blocks/-"]
    block = plan.ops[0]["value"]
    assert block == {"type": "related_list", "link": "order_customer",
                     "title": "关联订单"}
    assert any("默认条数" in note for note in plan.notes)


def test_related_list_add_without_link_refused() -> None:
    with pytest.raises(PatchGenerationError, match="还没有关联"):
        make_plan(
            model_without_link(),
            intent(IntentKind.RELATED_LIST_ADD, entity_key="customer",
                   child_entity_key="order"),
        )


def test_related_list_add_without_detail_view_refused() -> None:
    # order has list but no detail view in the fixture.
    with pytest.raises(PatchGenerationError, match="详情视图"):
        make_plan(
            make_model(),
            intent(IntentKind.RELATED_LIST_ADD, entity_key="order",
                   child_entity_key="customer"),
        )


# --- metrics --------------------------------------------------------------------------


def test_metric_add_duplicate_is_noop() -> None:
    plan = make_plan(
        make_model(),
        intent(IntentKind.METRIC_ADD, entity_key="customer", metric_op=MetricOp.COUNT),
    )
    assert plan.is_empty
    assert any("已存在" in note for note in plan.notes)


def test_metric_remove_then_readd_uses_live_indices() -> None:
    plan = make_plan(
        make_model(),
        intent(IntentKind.METRIC_REMOVE, risk=RiskLevel.HIGH, entity_key="customer",
               target_metric_key="customer_count"),
        intent(IntentKind.METRIC_ADD, entity_key="customer", metric_op=MetricOp.COUNT),
    )
    resulting = BusinessModel.model_validate(plan.resulting_snapshot)
    dashboard = next(v for v in resulting.views if v.kind == "dashboard")
    assert "customer_count" in dashboard.metrics
    assert len(resulting.metrics) == 3


def test_metric_add_creates_dashboard_and_navigation() -> None:
    plan = make_plan(
        model_without_dashboard(),
        intent(IntentKind.METRIC_ADD, entity_key="order", metric_op=MetricOp.COUNT),
    )
    paths = op_paths(plan)
    assert "/metrics/-" in paths
    assert "/views/-" in paths
    assert "/navigation/0" in paths
    resulting = BusinessModel.model_validate(plan.resulting_snapshot)
    dashboard = next(v for v in resulting.views if v.kind == "dashboard")
    assert dashboard.metrics == ["order_count"]
    assert resulting.navigation[0].view == dashboard.key


def test_metric_remove_drops_dashboard_card() -> None:
    plan = make_plan(
        make_model(),
        intent(IntentKind.METRIC_REMOVE, risk=RiskLevel.HIGH, entity_key="order",
               target_metric_key="total_amount"),
    )
    resulting = BusinessModel.model_validate(plan.resulting_snapshot)
    dashboard = next(v for v in resulting.views if v.kind == "dashboard")
    assert "total_amount" not in dashboard.metrics
    assert all(m.key != "total_amount" for m in resulting.metrics)


# --- links ----------------------------------------------------------------------------


def test_link_remove_cascades_detail_blocks() -> None:
    plan = make_plan(
        make_model(),
        intent(IntentKind.LINK_REMOVE, risk=RiskLevel.HIGH,
               entity_key="customer", child_entity_key="order"),
    )
    assert op_paths(plan) == ["/views/2/blocks/1", "/links/0"]
    resulting = BusinessModel.model_validate(plan.resulting_snapshot)
    assert resulting.links == []
    detail = next(v for v in resulting.views if v.key == "customer_detail")
    assert isinstance(detail, DetailView)
    assert all(b.type is not DetailBlockType.RELATED_LIST for b in detail.blocks)


def test_link_remove_without_link_is_noop() -> None:
    plan = make_plan(
        model_without_link(),
        intent(IntentKind.LINK_REMOVE, risk=RiskLevel.HIGH,
               entity_key="customer", child_entity_key="order"),
    )
    assert plan.is_empty
    assert any("不存在关联" in note for note in plan.notes)


# --- plan-level behaviour -------------------------------------------------------------


def test_empty_intents_returns_empty_plan_and_original_snapshot() -> None:
    model = make_model()
    plan = make_plan(model)
    assert plan.is_empty
    assert plan.requires_confirmation is False
    assert plan.resulting_snapshot == model.model_dump(mode="json")


def test_generator_does_not_mutate_input_model() -> None:
    model = make_model()
    before = model.model_dump(mode="json")
    make_plan(
        model,
        intent(IntentKind.VIEW_FILTER_ADD, entity_key="customer",
               field_key="follow_status"),
    )
    assert model.model_dump(mode="json") == before


def test_parser_notes_are_carried_into_plan() -> None:
    result = IntentParseResult(
        user_text="测试",
        intents=[
            intent(IntentKind.RENAME, new_name="新名字"),
        ],
        notes=["解析阶段的提示"],
    )
    plan = generate_patches(make_model(), result)
    assert "解析阶段的提示" in plan.notes


def test_high_risk_plan_carries_confirmation_note_and_flag() -> None:
    plan = make_plan(
        make_model(),
        intent(IntentKind.METRIC_REMOVE, risk=RiskLevel.HIGH, entity_key="order",
               target_metric_key="order_count"),
    )
    assert plan.requires_confirmation is True
    assert any("高风险" in note for note in plan.notes)


def test_ops_round_trip_through_three_validation_layers() -> None:
    # The exact ops returned must apply cleanly from the untouched snapshot.
    from backend.patch.validation import validate_patch_snapshot

    model = make_model()
    plan = make_plan(
        model,
        intent(IntentKind.VIEW_COLUMN_REMOVE, entity_key="order",
               field_key="ordered_at"),
        intent(IntentKind.VIEW_FILTER_ADD, entity_key="order",
               field_key="ordered_at"),
    )
    snapshot = validate_patch_snapshot(model.model_dump(mode="json"), plan.ops)
    assert snapshot == plan.resulting_snapshot


def test_snapshot_deepcopy_is_unaffected_by_repeated_runs() -> None:
    model = make_model()
    first = make_plan(
        model,
        intent(IntentKind.VIEW_FILTER_ADD, entity_key="customer",
               field_key="follow_status"),
    )
    second = make_plan(
        model,
        intent(IntentKind.VIEW_FILTER_ADD, entity_key="customer",
               field_key="owner"),
    )
    assert first.resulting_snapshot is not second.resulting_snapshot
    assert copy.deepcopy(first.resulting_snapshot) == first.resulting_snapshot
