"""Deterministic Chinese change explanations for the NL patch preview (Day 25).

Produces one human-readable line per grounded intent. It is a pure function of
the current model and the parser output — no second LLM call. Cascading effects
and safety downgrades reach the preview separately through the generator's
``notes``.
"""

from __future__ import annotations

from pydantic import BaseModel

from backend.domain.enums import FieldRole, FieldType, MetricOp, SortDir
from backend.domain.models import BusinessModel, ListView
from backend.patch.intent_parser import (
    IntentKind,
    IntentParseResult,
    ParsedIntent,
    RiskLevel,
)

_FIELD_TYPE_LABELS: dict[FieldType, str] = {
    FieldType.STRING: "文本",
    FieldType.NUMBER: "数值",
    FieldType.MONEY: "金额",
    FieldType.DATE: "日期",
    FieldType.ENUM: "枚举",
    FieldType.PHONE: "电话",
}

_FIELD_ROLE_LABELS: dict[FieldRole, str] = {
    FieldRole.IDENTIFIER: "名称字段",
    FieldRole.DIMENSION: "维度字段",
    FieldRole.MEASURE: "数值字段",
    FieldRole.TIME: "日期字段",
    FieldRole.ENUM: "枚举字段",
    FieldRole.TEXT: "文本字段",
}


class PatchChangeItem(BaseModel):
    """One preview row: what changes and whether it is destructive."""

    kind: str
    risk: str
    summary: str


def _entity_name(model: BusinessModel, key: str | None) -> str:
    if key is None:
        return ""
    entity = next((item for item in model.entities if item.key == key), None)
    return entity.name if entity is not None else key


def _field_name(model: BusinessModel, entity_key: str | None, field_key: str | None) -> str:
    if field_key is None or entity_key is None:
        return ""
    entity = next((item for item in model.entities if item.key == entity_key), None)
    if entity is None:
        return field_key
    field = next((f for f in entity.fields if f.key == field_key), None)
    return field.name if field is not None else field_key


def _list_view(model: BusinessModel, entity_key: str | None) -> ListView | None:
    if entity_key is None:
        return None
    return next(
        (
            view
            for view in model.views
            if isinstance(view, ListView) and view.entity == entity_key
        ),
        None,
    )


def _summarize(model: BusinessModel, intent: ParsedIntent) -> str:
    kind = intent.kind
    entity = _entity_name(model, intent.entity_key)
    field = _field_name(model, intent.entity_key, intent.field_key)
    child = _entity_name(model, intent.child_entity_key)
    view = _list_view(model, intent.entity_key)
    view_title = view.title if view is not None else f"{entity}列表"

    if kind is IntentKind.RENAME:
        if intent.entity_key is None:
            return f"将系统名称改为「{intent.new_name}」"
        if intent.field_key is None:
            return f"将「{entity}」改名为「{intent.new_name}」"
        return f"将「{entity}」的字段「{field}」改名为「{intent.new_name}」"

    if kind is IntentKind.ENTITY_ADD:
        module_name = intent.new_entity_name or "新模块"
        field_names = "、".join(field.name for field in intent.new_fields or [])
        text = (
            f"新增「{module_name}」模块（字段：{field_names}），"
            "并生成列表、详情、表单视图"
        )
        if intent.new_links:
            parents = "、".join(
                _entity_name(model, link.entity_key) for link in intent.new_links
            )
            text += f"，关联到「{parents}」"
        if intent.metric_op is MetricOp.SUM and intent.metric_field_key is not None:
            metric_field = next(
                (
                    field.name
                    for field in intent.new_fields or []
                    if field.key == intent.metric_field_key
                ),
                intent.metric_field_key,
            )
            text += f"，首页添加「{metric_field}」合计指标"
        else:
            text += "，首页添加计数指标"
        return text

    if kind is IntentKind.FIELD_ADD:
        type_label = _FIELD_TYPE_LABELS.get(intent.field_type, "文本")  # type: ignore[arg-type]
        text = f"在「{entity}」中新增{type_label}字段「{intent.new_name}」"
        if intent.field_type is FieldType.ENUM and intent.enum_values:
            options = "、".join(intent.enum_values)
            text += f"，可选值：{options}"
        return text

    if kind is IntentKind.FIELD_REMOVE:
        return f"删除「{entity}」的字段「{field}」"

    if kind is IntentKind.FIELD_UPDATE:
        parts: list[str] = []
        if intent.field_type is not None:
            parts.append(f"类型改为{_FIELD_TYPE_LABELS[intent.field_type]}")
        if intent.field_role is not None:
            role_label = _FIELD_ROLE_LABELS[intent.field_role]
            if intent.changes_identifier:
                parts.append(f"提升为{role_label}（用于识别每行数据）")
            else:
                parts.append(f"业务角色设为{role_label}")
        detail = "，".join(parts) if parts else "调整字段设置"
        return f"修改「{entity}」的字段「{field}」：{detail}"

    if kind is IntentKind.VIEW_FILTER_ADD:
        return f"为「{view_title}」添加按「{field}」筛选"
    if kind is IntentKind.VIEW_FILTER_REMOVE:
        return f"移除「{view_title}」中按「{field}」的筛选"

    if kind is IntentKind.VIEW_SORT_SET:
        direction = "从高到低" if intent.sort_dir is SortDir.DESC else "从低到高"
        return f"将「{view_title}」设置为按「{field}」{direction}排序"

    if kind is IntentKind.VIEW_COLUMN_ADD:
        return f"在「{view_title}」中显示「{field}」列"
    if kind is IntentKind.VIEW_COLUMN_REMOVE:
        return f"在「{view_title}」中隐藏「{field}」列"

    if kind is IntentKind.VIEW_SEARCH_ADD:
        return f"为「{view_title}」开启按「{field}」搜索"
    if kind is IntentKind.VIEW_SEARCH_REMOVE:
        return f"关闭「{view_title}」中按「{field}」的搜索"

    if kind is IntentKind.RELATED_LIST_ADD:
        limit = intent.limit or 10
        return f"在「{entity}」详情页添加「{child}」关联列表（最多显示 {limit} 条）"
    if kind is IntentKind.RELATED_LIST_REMOVE:
        return f"从「{entity}」详情页移除「{child}」关联列表"

    if kind is IntentKind.METRIC_ADD:
        if intent.metric_op is MetricOp.SUM:
            metric_field = _field_name(
                model, intent.entity_key, intent.metric_field_key
            )
            return f"在首页添加「{entity} · {metric_field}」的合计指标"
        return f"在首页添加「{entity}」的计数指标"

    if kind is IntentKind.METRIC_REMOVE:
        metric = next(
            (m for m in model.metrics if m.key == intent.target_metric_key), None
        )
        metric_name = metric.name if metric is not None else (intent.target_metric_key or "")
        return f"删除首页指标「{metric_name}」"

    if kind is IntentKind.LINK_REMOVE:
        return f"删除「{entity}」与「{child}」之间的关联"

    return intent.reason or kind.value


def explain_intents(
    model: BusinessModel, result: IntentParseResult
) -> list[PatchChangeItem]:
    """Return the ordered preview rows for all grounded intents."""
    return [
        PatchChangeItem(
            kind=intent.kind.value,
            risk=(
                "high" if intent.risk is RiskLevel.HIGH else "low"
            ),
            summary=_summarize(model, intent),
        )
        for intent in result.intents
    ]


__all__ = ["PatchChangeItem", "explain_intents"]
