"""Server-side queries behind the runtime views.

List views page, search, filter and sort here instead of receiving every row
in the bootstrap payload. Detail/form pages read one row, related lists read
the child rows of one link, and the dashboard is aggregated over live rows.

Everything is deterministic and reads the Business Model only for field
types, key fields and default view sorts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session

from backend.domain.models import BusinessModel, Entity
from backend.runtime import record_io
from backend.storage.record_store import (
    EMPTY_FILTER_VALUE,
    FieldSort,
    RowQuery,
    get_record_store,
)

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 200
MAX_RECENT = 5
MAX_TREND_POINTS = 6
MAX_DISTRIBUTION_ITEMS = 6


class QueryError(ValueError):
    """A malformed or model-inconsistent query (422)."""


def split_fields(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def parse_filters(raw: str | None) -> dict[str, tuple[str, ...]]:
    """``{"region": ["华东"]}`` -> per-field accepted values."""
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise QueryError("筛选条件不是合法的 JSON") from exc
    if not isinstance(payload, dict):
        raise QueryError("筛选条件必须是字段到取值的对象")
    filters: dict[str, tuple[str, ...]] = {}
    for field_key, values in payload.items():
        if not isinstance(values, list):
            raise QueryError(f"筛选条件 {field_key} 的取值必须是数组")
        filters[str(field_key)] = tuple(str(value) for value in values)
    return filters


def parse_sorts(raw: str | None) -> list[dict[str, str]]:
    """``[{"field": "amount", "dir": "desc"}]``."""
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise QueryError("排序条件不是合法的 JSON") from exc
    if not isinstance(payload, list):
        raise QueryError("排序条件必须是数组")
    sorts: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict) or not item.get("field"):
            raise QueryError("每条排序都需要 field")
        direction = str(item.get("dir", "asc"))
        if direction not in {"asc", "desc"}:
            raise QueryError("排序方向只能是 asc 或 desc")
        sorts.append({"field": str(item["field"]), "dir": direction})
    return sorts


def _entity(model: BusinessModel, entity_key: str) -> Entity:
    return record_io.get_entity(model, entity_key)


def _field_types(entity: Entity) -> dict[str, str]:
    return {field.key: field.type for field in entity.fields}


def _check_fields(entity: Entity, keys: Sequence[str], what: str) -> None:
    known = set(_field_types(entity))
    unknown = [key for key in keys if key not in known]
    if unknown:
        raise QueryError(f"{what}引用了实体中不存在的字段：{', '.join(unknown)}")


def _page_window(page: int, page_size: int, total: int) -> tuple[int, int]:
    size = min(max(1, page_size), MAX_PAGE_SIZE)
    page_count = max(1, (total + size - 1) // size)
    return min(max(1, page), page_count), page_count


def query_rows(
    db: Session,
    model: BusinessModel,
    app_key: str,
    entity_key: str,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    search: str = "",
    search_fields: str = "",
    filters: dict[str, tuple[str, ...]] | None = None,
    sorts: list[dict[str, str]] | None = None,
    facets: str = "",
) -> dict[str, Any]:
    """One page of an entity plus the totals its list header shows."""
    entity = _entity(model, entity_key)
    types = _field_types(entity)
    search_keys = split_fields(search_fields)
    facet_keys = split_fields(facets)
    _check_fields(entity, search_keys, "搜索")
    _check_fields(entity, facet_keys, "分面")
    active_filters = filters or {}
    _check_fields(entity, list(active_filters), "筛选")

    field_sorts = tuple(
        FieldSort(
            field=sort["field"],
            direction=sort["dir"],
            value_type=types.get(sort["field"], "string"),
        )
        for sort in (sorts or [])
    )
    _check_fields(entity, [sort.field for sort in field_sorts], "排序")

    store = get_record_store(db)
    entity_total = store.count(app_key, entity.key)
    size = min(max(1, page_size), MAX_PAGE_SIZE)
    request = RowQuery(
        offset=(max(1, page) - 1) * size,
        limit=size,
        search=search,
        search_fields=search_keys,
        filters=active_filters,
        sorts=field_sorts,
    )
    result = store.query(app_key, entity.key, request)
    resolved_page, page_count = _page_window(page, size, result.total)
    if request.offset != (resolved_page - 1) * size:
        # The filtered result shrank below the requested page: re-read it.
        result = store.query(
            app_key,
            entity.key,
            RowQuery(
                offset=(resolved_page - 1) * size,
                limit=size,
                search=search,
                search_fields=search_keys,
                filters=active_filters,
                sorts=field_sorts,
            ),
        )
    return {
        "entity": entity.key,
        "rows": result.rows,
        "total": result.total,
        "entity_total": entity_total,
        "page": resolved_page,
        "page_count": page_count,
        "page_size": size,
        "facets": (
            _facet_payload(db, app_key, entity, facet_keys) if facet_keys else {}
        ),
    }


def _facet_payload(
    db: Session, app_key: str, entity: Entity, fields: Sequence[str]
) -> dict[str, list[dict[str, Any]]]:
    types = _field_types(entity)
    raw = get_record_store(db).facets(app_key, entity.key, fields)
    payload: dict[str, list[dict[str, Any]]] = {}
    for field_key, counts in raw.items():
        value_type = types.get(field_key, "string")
        payload[field_key] = [
            {"value": value, "count": count}
            for value, count in sorted(
                counts, key=lambda item: _facet_key(item[0], value_type)
            )
        ]
    return payload


def _facet_key(value: str, value_type: str) -> tuple[int, float, str]:
    if value == EMPTY_FILTER_VALUE:
        return (1, 0.0, "")
    if value_type in {"number", "money"}:
        try:
            return (0, float(value), value)
        except ValueError:
            return (0, 0.0, value)
    return (0, 0.0, value)


def fetch_row(
    db: Session, model: BusinessModel, app_key: str, entity_key: str, row_key: str
) -> dict[str, Any]:
    entity = _entity(model, entity_key)
    row = get_record_store(db).get(app_key, entity.key, row_key)
    if row is None:
        raise record_io.RecordNotFound(f"记录不存在：{row_key}")
    return row


def related_rows(
    db: Session,
    model: BusinessModel,
    app_key: str,
    *,
    parent_entity: str,
    link_key: str,
    parent_row_key: str,
    limit: int = 5,
    offset: int = 0,
) -> dict[str, Any]:
    """Child rows of one link, sorted by the child's list view."""
    parent = _entity(model, parent_entity)
    link = next((item for item in model.links if item.key == link_key), None)
    if link is None:
        raise QueryError(f"关联 {link_key!r} 不存在")
    if link.from_ == parent.key:
        child_key, parent_field, child_field = link.to, link.on.from_, link.on.to
    elif link.to == parent.key:
        child_key, parent_field, child_field = link.from_, link.on.to, link.on.from_
    else:
        raise QueryError(f"关联 {link_key!r} 与实体 {parent.key!r} 无关")
    child = _entity(model, child_key)
    parent_row = get_record_store(db).get(app_key, parent.key, parent_row_key)
    if parent_row is None:
        raise record_io.RecordNotFound(f"记录不存在：{parent_row_key}")

    parent_value = parent_row.get(parent_field)
    if parent_value is None or parent_value == "":
        return {"rows": [], "total": 0}

    list_view = next(
        (
            view
            for view in model.views
            if view.kind == "list" and view.entity == child.key
        ),
        None,
    )
    types = _field_types(child)
    sorts = tuple(
        FieldSort(
            field=sort.field,
            direction=sort.dir,
            value_type=types.get(sort.field, "string"),
        )
        for sort in (list_view.sorts if list_view is not None else [])
    )
    result = get_record_store(db).query(
        app_key,
        child.key,
        RowQuery(
            offset=max(0, offset),
            limit=min(max(1, limit), MAX_PAGE_SIZE),
            filters={child_field: (str(parent_value),)},
            sorts=sorts,
        ),
    )
    return {"rows": result.rows, "total": result.total}


def dashboard(db: Session, model: BusinessModel, app_key: str) -> dict[str, Any]:
    """Everything the dashboard panels render, computed over live rows.

    Uses the caller's model (current or pinned) so a published version keeps
    its own metrics and fields even after the app moves on.
    """
    stored = get_record_store(db).load(app_key)
    records = {
        entity.key: list(stored.get(entity.key, []) or [])
        for entity in model.entities
    }
    return {
        "metrics": _metrics(model, records),
        "recent": _recent(model, records),
        "trend": _trend(model, records),
        "distribution": _distribution(model, records),
    }


def _metrics(
    model: BusinessModel, records: Mapping[str, list[dict[str, Any]]]
) -> dict[str, float]:
    values: dict[str, float] = {}
    for metric in model.metrics:
        rows = records.get(metric.entity, [])
        if metric.formula.op == "count":
            values[metric.key] = float(len(rows))
            continue
        numbers = [
            row[metric.formula.field]
            for row in rows
            if metric.formula.field
            and isinstance(row.get(metric.formula.field), (int, float))
            and not isinstance(row.get(metric.formula.field), bool)
        ]
        if not numbers:
            values[metric.key] = 0.0
            continue
        total = float(sum(numbers))
        if metric.formula.op == "sum":
            values[metric.key] = total
        elif metric.formula.op == "avg":
            values[metric.key] = total / len(numbers)
        elif metric.formula.op == "min":
            values[metric.key] = float(min(numbers))
        else:
            values[metric.key] = float(max(numbers))
    return values


def _time_field(entity: Entity) -> str | None:
    for field in entity.fields:
        if field.role == "time":
            return field.key
    for field in entity.fields:
        if field.type == "date":
            return field.key
    return None


def _non_empty(rows: list[dict[str, Any]], field_key: str) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get(field_key) is not None and row.get(field_key) != ""
    ]


def _list_view_key(model: BusinessModel, entity_key: str) -> str | None:
    return next(
        (
            view.key
            for view in model.views
            if view.kind == "list" and view.entity == entity_key
        ),
        None,
    )


def _detail_view_key(model: BusinessModel, entity_key: str) -> str | None:
    return next(
        (
            view.key
            for view in model.views
            if view.kind == "detail" and view.entity == entity_key
        ),
        None,
    )


def _recent(
    model: BusinessModel, records: Mapping[str, list[dict[str, Any]]]
) -> dict[str, Any] | None:
    candidates = [
        (entity, _time_field(entity), records.get(entity.key, []))
        for entity in model.entities
        if records.get(entity.key)
    ]
    if not candidates:
        return None

    dated = [
        (entity, field, _non_empty(rows, field))
        for entity, field, rows in candidates
        if field is not None
    ]
    dated = [item for item in dated if item[2]]
    if dated:
        dated.sort(key=lambda item: len(item[2]), reverse=True)
        entity, field, rows = dated[0]
        visible = sorted(
            [row for row in rows if row.get(field) is not None],
            key=lambda row: str(row.get(field, "")),
            reverse=True,
        )[:MAX_RECENT]
    else:
        entity, _, rows = max(candidates, key=lambda item: len(item[2]))
        visible = rows[:MAX_RECENT]

    return {
        "entity": entity.key,
        "list_view": _list_view_key(model, entity.key),
        "detail_view": _detail_view_key(model, entity.key),
        "rows": visible,
    }


def _trend(
    model: BusinessModel, records: Mapping[str, list[dict[str, Any]]]
) -> dict[str, Any] | None:
    candidates = []
    for entity in model.entities:
        field = _time_field(entity)
        if field is None:
            continue
        rows = _non_empty(records.get(entity.key, []), field)
        if rows:
            candidates.append((entity, field, rows))
    if not candidates:
        return None
    entity, date_field, rows = max(candidates, key=lambda item: len(item[2]))

    measure_field = next(
        (
            field.key
            for field in entity.fields
            if field.type == "money" and field.role == "measure"
        ),
        None,
    )
    buckets: dict[str, float] = {}
    for row in rows:
        label = str(row.get(date_field, ""))[:7]
        if len(label) != 7 or label[4] != "-" or not (
            label[:4].isdigit() and label[5:].isdigit()
        ):
            continue
        if measure_field is not None:
            raw = row.get(measure_field)
            contribution = (
                float(raw)
                if isinstance(raw, (int, float)) and not isinstance(raw, bool)
                else 1.0
            )
        else:
            contribution = 1.0
        buckets[label] = buckets.get(label, 0.0) + contribution
    if not buckets:
        return None
    points = [
        {"label": label, "value": value}
        for label, value in sorted(buckets.items())[-MAX_TREND_POINTS:]
    ]
    return {
        "entity": entity.key,
        "date_field": date_field,
        "measure_field": measure_field,
        "points": points,
    }


def _distribution(
    model: BusinessModel, records: Mapping[str, list[dict[str, Any]]]
) -> dict[str, Any] | None:
    options: list[dict[str, Any]] = []
    for entity in model.entities:
        for field in entity.fields:
            if field.type != "enum":
                continue
            counts: dict[str, int] = {}
            for row in records.get(entity.key, []):
                value = row.get(field.key)
                if value is None or value == "":
                    continue
                label = str(value)
                counts[label] = counts.get(label, 0) + 1
            if len(counts) < 2:
                continue
            items = [
                {"label": label, "count": count}
                for label, count in sorted(
                    counts.items(), key=lambda item: (-item[1], item[0])
                )[:MAX_DISTRIBUTION_ITEMS]
            ]
            options.append(
                {
                    "entity": entity.key,
                    "field": field.key,
                    "items": items,
                    "total": sum(counts.values()),
                }
            )
    visible = [
        option
        for option in options
        if len(option["items"]) <= MAX_DISTRIBUTION_ITEMS
    ]
    if not visible:
        return None
    return max(visible, key=lambda option: option["total"])


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "QueryError",
    "dashboard",
    "fetch_row",
    "parse_filters",
    "parse_sorts",
    "query_rows",
    "related_rows",
    "split_fields",
]
