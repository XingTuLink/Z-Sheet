"""Deterministic relationship edits for the visual Relationship Editor.

The editor never rewrites the model in place: it produces RFC 6902 ops for a
link change, which then travel through the same preview / confirm / snapshot
path as a natural-language patch. Everything here is pure and testable; no LLM
is involved.

Supported edits:

- add a link (one-to-many, one-to-one, including self links);
- change a link's cardinality and matched fields;
- delete a link and the detail related-list blocks that reference it.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Literal

from backend.domain.enums import DetailBlockType, LinkType
from backend.domain.models import BusinessModel, Link, LinkOn

ACTION_ADD = "add"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"

ACTION_LABELS = {
    ACTION_ADD: "新增关联",
    ACTION_UPDATE: "调整关联",
    ACTION_DELETE: "删除关联",
}

USER_AUTHORED_CONFIDENCE = 1.0


class RelationshipError(ValueError):
    """The requested edit cannot become a legal model change (422)."""


@dataclass(frozen=True)
class RelationshipPlan:
    ops: list[dict[str, Any]]
    notes: list[str]
    summary: str
    requires_confirmation: bool


def _add(path: str, value: Any) -> dict[str, Any]:
    return {"op": "add", "path": path, "value": value}


def _remove(path: str) -> dict[str, Any]:
    return {"op": "remove", "path": path}


def _replace(path: str, value: Any) -> dict[str, Any]:
    return {"op": "replace", "path": path, "value": value}


def _entity(doc: dict[str, Any], key: str) -> dict[str, Any]:
    for entity in doc["entities"]:
        if entity["key"] == key:
            return entity
    raise RelationshipError(f"模型中不存在实体 {key!r}")


def _entity_index(doc: dict[str, Any], key: str) -> int:
    for index, entity in enumerate(doc["entities"]):
        if entity["key"] == key:
            return index
    raise RelationshipError(f"模型中不存在实体 {key!r}")


def _field_name(entity: dict[str, Any], key: str) -> str:
    for field in entity["fields"]:
        if field["key"] == key:
            return field["name"]
    raise RelationshipError(
        f"实体「{entity['name']}」中不存在字段 {key!r}"
    )


def _detail_views(
    doc: dict[str, Any], entity_key: str
) -> list[tuple[int, dict[str, Any]]]:
    return [
        (index, view)
        for index, view in enumerate(doc["views"])
        if view["kind"] == "detail" and view.get("entity") == entity_key
    ]


def _link_label(link: dict[str, Any], doc: dict[str, Any]) -> str:
    source = _entity(doc, link["from_"])["name"]
    target = _entity(doc, link["to"])["name"]
    cardinality = "一对一" if link["type"] == LinkType.ONE_TO_ONE.value else "一对多"
    suffix = "（自关联）" if link["from_"] == link["to"] else ""
    return f"「{source}」→「{target}」{cardinality}{suffix}"


def _claim_link_key(doc: dict[str, Any], from_key: str, to_key: str) -> str:
    used = {link["key"] for link in doc["links"]}
    base = f"{from_key}_self" if from_key == to_key else f"{from_key}_to_{to_key}"
    if base not in used:
        return base
    suffix = 2
    while f"{base}_{suffix}" in used:
        suffix += 1
    return f"{base}_{suffix}"


def _block_for(
    link_key: str, title: str
) -> dict[str, Any]:
    return {
        "type": DetailBlockType.RELATED_LIST.value,
        "link": link_key,
        "title": title,
    }


def _append_detail_block(
    ops: list[dict[str, Any]],
    doc: dict[str, Any],
    entity_key: str,
    link_key: str,
    title: str,
) -> int:
    added = 0
    for view_index, view in _detail_views(doc, entity_key):
        if any(block.get("link") == link_key for block in view["blocks"]):
            continue
        ops.append(_add(f"/views/{view_index}/blocks/-", _block_for(link_key, title)))
        added += 1
    return added


def _remove_detail_blocks(
    ops: list[dict[str, Any]], doc: dict[str, Any], link_key: str
) -> int:
    removed = 0
    for view_index, view in enumerate(doc["views"]):
        if view["kind"] != "detail":
            continue
        matches = [
            block_index
            for block_index, block in enumerate(view["blocks"])
            if block.get("link") == link_key
        ]
        if not matches:
            continue
        if len(matches) == len(view["blocks"]):
            raise RelationshipError(
                f"删除该关联会使详情页「{view['title']}」没有任何区块，操作已拒绝。"
            )
        for block_index in sorted(matches, reverse=True):
            ops.append(_remove(f"/views/{view_index}/blocks/{block_index}"))
            removed += 1
    return removed


def _find_link(
    doc: dict[str, Any], link_key: str
) -> tuple[int, dict[str, Any]]:
    for index, link in enumerate(doc["links"]):
        if link["key"] == link_key:
            return index, link
    raise RelationshipError("关联不存在或已被删除，请刷新后重试。")


def _check_pair(
    doc: dict[str, Any],
    from_key: str,
    to_key: str,
    on_from: str,
    on_to: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = _entity(doc, from_key)
    target = _entity(doc, to_key)
    _field_name(source, on_from)
    _field_name(target, on_to)
    if from_key == to_key and on_from == on_to:
        raise RelationshipError(
            "自关联需要选择两个不同的字段作为匹配依据。"
        )
    return source, target


def _build_add(
    doc: dict[str, Any],
    *,
    from_key: str,
    to_key: str,
    link_type: LinkType,
    on_from: str,
    on_to: str,
) -> RelationshipPlan:
    source, target = _check_pair(doc, from_key, to_key, on_from, on_to)
    signature = (from_key, to_key, on_from, on_to)
    if any(
        (link["from_"], link["to"], link["on"]["from_"], link["on"]["to"])
        == signature
        for link in doc["links"]
    ):
        raise RelationshipError(
            f"「{source['name']}」与「{target['name']}」之间已存在使用相同匹配字段的关联。"
        )

    link_key = _claim_link_key(doc, from_key, to_key)
    link = Link(
        key=link_key,
        **{"from": from_key},
        to=to_key,
        type=link_type,
        on=LinkOn(**{"from": on_from, "to": on_to}),
        confidence=USER_AUTHORED_CONFIDENCE,
        needs_review=False,
    )
    ops: list[dict[str, Any]] = [_add("/links/-", link.model_dump(mode="json"))]

    blocks = _append_detail_block(
        ops, doc, from_key, link_key, f"关联{target['name']}"
    )
    if link_type is LinkType.ONE_TO_ONE and from_key != to_key:
        blocks += _append_detail_block(
            ops, doc, to_key, link_key, f"关联{source['name']}"
        )

    label = _link_label(link.model_dump(mode="json"), doc)
    notes = [f"已新增关联{label}。"]
    if blocks:
        notes.append(f"同时在 {blocks} 个详情页中添加了关联列表区块。")
    elif not _detail_views(doc, from_key):
        notes.append("起点实体没有详情视图，未添加关联列表区块。")
    return RelationshipPlan(
        ops=ops,
        notes=notes,
        summary=f"新增关联{label}",
        requires_confirmation=False,
    )


def _build_update(
    doc: dict[str, Any],
    *,
    link_key: str,
    link_type: LinkType | None,
    on_from: str | None,
    on_to: str | None,
) -> RelationshipPlan:
    link_index, link = _find_link(doc, link_key)
    if link_type is None and on_from is None and on_to is None:
        raise RelationshipError("没有需要调整的内容。")

    new_from = on_from or link["on"]["from_"]
    new_to = on_to or link["on"]["to"]
    _check_pair(doc, link["from_"], link["to"], new_from, new_to)

    ops: list[dict[str, Any]] = []
    if link_type is not None and link_type.value != link["type"]:
        ops.append(_replace(f"/links/{link_index}/type", link_type.value))
    if new_from != link["on"]["from_"]:
        ops.append(_replace(f"/links/{link_index}/on/from_", new_from))
    if new_to != link["on"]["to"]:
        ops.append(_replace(f"/links/{link_index}/on/to", new_to))
    if not ops:
        raise RelationshipError("关联已经是目标状态，无需调整。")

    updated = {
        **link,
        "type": link_type.value if link_type is not None else link["type"],
        "on": {"from_": new_from, "to": new_to},
    }
    label = _link_label(updated, doc)
    return RelationshipPlan(
        ops=ops,
        notes=[f"关联已调整为{label}。"],
        summary=f"调整关联为{label}",
        requires_confirmation=False,
    )


def _build_delete(doc: dict[str, Any], *, link_key: str) -> RelationshipPlan:
    link_index, link = _find_link(doc, link_key)
    label = _link_label(link, doc)
    ops: list[dict[str, Any]] = []
    removed_blocks = _remove_detail_blocks(ops, doc, link_key)
    ops.append(_remove(f"/links/{link_index}"))

    notes = [f"已删除关联{label}。"]
    if removed_blocks:
        notes.append(f"同时移除了 {removed_blocks} 个详情页中的关联列表区块。")
    return RelationshipPlan(
        ops=ops,
        notes=notes,
        summary=f"删除关联{label}",
        requires_confirmation=True,
    )


def build_relationship_plan(
    model: BusinessModel,
    *,
    action: Literal["add", "update", "delete"],
    link_key: str | None = None,
    from_key: str | None = None,
    to_key: str | None = None,
    link_type: LinkType | None = None,
    on_from: str | None = None,
    on_to: str | None = None,
) -> RelationshipPlan:
    """Turn one editor request into a dry-run patch plan."""
    doc = copy.deepcopy(model.model_dump(mode="json"))

    if action == ACTION_ADD:
        if not (from_key and to_key and link_type and on_from and on_to):
            raise RelationshipError(
                "新增关联需要指定起点实体、终点实体、关联类型与两端匹配字段。"
            )
        return _build_add(
            doc,
            from_key=from_key,
            to_key=to_key,
            link_type=link_type,
            on_from=on_from,
            on_to=on_to,
        )
    if action == ACTION_UPDATE:
        if not link_key:
            raise RelationshipError("调整关联需要指定关联标识。")
        return _build_update(
            doc,
            link_key=link_key,
            link_type=link_type,
            on_from=on_from,
            on_to=on_to,
        )
    if action == ACTION_DELETE:
        if not link_key:
            raise RelationshipError("删除关联需要指定关联标识。")
        return _build_delete(doc, link_key=link_key)
    raise RelationshipError(f"不支持的关联操作：{action}")


__all__ = [
    "ACTION_ADD",
    "ACTION_DELETE",
    "ACTION_LABELS",
    "ACTION_UPDATE",
    "RelationshipError",
    "RelationshipPlan",
    "build_relationship_plan",
]
