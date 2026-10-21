"""Day 24 — Deterministic Patch Generator.

Turns the grounded ``ParsedIntent`` list from Day 23 into an RFC 6902 JSON
Patch against the current Business Model snapshot. The LLM never touches this
stage: every op path and value is computed here, and the whole batch is
rejected unless it passes the same three validation layers as a real write.

Rules (design doc chapters 6, 8, 9):

- modifications only ever produce patch ops — the model is never rewritten;
- destructive removals cascade their now-dangling references (list columns,
  dashboard metric cards, detail related-list blocks) inside the same patch,
  and are listed as effect notes for preview;
- genuinely unsafe intents (deleting the identifier, deleting a link-bearing
  field, demoting an identifier without a replacement…) raise
  ``PatchGenerationError`` with a Chinese message: nothing is applied;
- the result is a dry run. Day 25 adds preview/confirm and only then persists
  a new model version.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from backend.domain.enums import (
    DetailBlockType,
    FieldRole,
    FieldType,
    LinkType,
    MetricOp,
    SortDir,
)
from backend.domain.models import (
    BusinessField,
    BusinessModel,
    DashboardView,
    DetailBlock,
    DetailView,
    Entity,
    FormView,
    Link,
    LinkOn,
    ListView,
    Metric,
    MetricFormula,
    NavigationItem,
    SortSpec,
)
from backend.patch.apply import PatchError, apply_patch
from backend.patch.intent_parser import (
    DEFAULT_RELATED_LIST_LIMIT,
    IntentKind,
    IntentParseResult,
    ParsedIntent,
    RiskLevel,
)
from backend.patch.json_patch import PatchDocument
from backend.patch.validation import validate_patch_snapshot
from backend.understanding.naming import field_key as glossary_field_key

# Additions requested in a sentence are user-authored, so they carry the top
# confidence tier and never enter the understanding review queue.
USER_AUTHORED_CONFIDENCE = 1.0
DASHBOARD_KEY = "home_dashboard"
MAX_MODULE_LIST_COLUMNS = 6
MAX_MODULE_FILTERS = 4

_FIELD_TYPE_LABELS: dict[FieldType, str] = {
    FieldType.STRING: "文本",
    FieldType.NUMBER: "数值",
    FieldType.MONEY: "金额",
    FieldType.DATE: "日期",
    FieldType.ENUM: "枚举",
    FieldType.PHONE: "电话",
}


class PatchGenerationError(Exception):
    """An intent cannot become a legal patch; the whole batch is refused."""


class PatchPlan(BaseModel):
    """A fully validated, not-yet-persisted patch plus preview information."""

    model_config = ConfigDict(extra="forbid")

    ops: list[dict[str, Any]]
    requires_confirmation: bool
    notes: list[str]
    resulting_snapshot: dict[str, Any]

    @property
    def is_empty(self) -> bool:
        return not self.ops


# --- raw op helpers -------------------------------------------------------------------


def _add(path: str, value: Any) -> dict[str, Any]:
    return {"op": "add", "path": path, "value": value}


def _remove(path: str) -> dict[str, Any]:
    return {"op": "remove", "path": path}


def _replace(path: str, value: Any) -> dict[str, Any]:
    return {"op": "replace", "path": path, "value": value}


# --- snapshot indexing ----------------------------------------------------------------


def _entity(doc: dict[str, Any], key: str) -> dict[str, Any]:
    for entity in doc["entities"]:
        if entity["key"] == key:
            return entity
    raise PatchGenerationError(f"内部错误：模型中找不到实体 {key!r}")


def _entity_index(doc: dict[str, Any], key: str) -> int:
    for index, entity in enumerate(doc["entities"]):
        if entity["key"] == key:
            return index
    raise PatchGenerationError(f"内部错误：模型中找不到实体 {key!r}")


def _field_index(entity_doc: dict[str, Any], key: str) -> int:
    for index, field in enumerate(entity_doc["fields"]):
        if field["key"] == key:
            return index
    raise PatchGenerationError(
        f"内部错误：实体 {entity_doc['key']!r} 中找不到字段 {key!r}"
    )


def _views_of(
    doc: dict[str, Any], *, kind: str, entity_key: str | None = None
) -> list[tuple[int, dict[str, Any]]]:
    found: list[tuple[int, dict[str, Any]]] = []
    for index, view in enumerate(doc["views"]):
        if view["kind"] != kind:
            continue
        if entity_key is not None and view.get("entity") != entity_key:
            continue
        found.append((index, view))
    return found


def _find_link(
    doc: dict[str, Any], endpoint_a: str, endpoint_b: str
) -> tuple[int, dict[str, Any]] | None:
    """Locate a link by its unordered endpoint pair."""
    for index, link in enumerate(doc["links"]):
        if {link["from_"], link["to"]} == {endpoint_a, endpoint_b}:
            return index, link
    return None


def _dedupe_key(used: set[str], first: str, fallback: str) -> str:
    for candidate in (first, fallback):
        if candidate not in used:
            return candidate
    suffix = 2
    while f"{fallback}_{suffix}" in used:
        suffix += 1
    return f"{fallback}_{suffix}"


def _unique_field_key(entity_doc: dict[str, Any], display_name: str) -> str:
    base, _ = glossary_field_key(display_name, len(entity_doc["fields"]))
    used = {field["key"] for field in entity_doc["fields"]}
    if base not in used:
        return base
    suffix = 2
    while f"{base}_{suffix}" in used:
        suffix += 1
    return f"{base}_{suffix}"


# --- batch-level guards ---------------------------------------------------------------


def _identifier_rewrites(
    doc: dict[str, Any], intents: list[ParsedIntent]
) -> dict[str, str]:
    """Pre-scan field_update intents for coherent identifier changes.

    Returns the ``entity_key -> new key_field`` map; refuses a batch that
    promotes several fields or demotes the current identifier with no
    replacement (an intermediate/end model with no identifier is unsafe).
    """
    rewrites: dict[str, str] = {}
    updates = [
        intent
        for intent in intents
        if intent.kind is IntentKind.FIELD_UPDATE and intent.field_role is not None
    ]
    entity_keys = {intent.entity_key for intent in updates}
    for entity_key in entity_keys:
        assert entity_key is not None
        entity = _entity(doc, entity_key)
        current_key_field = entity["key_field"]
        current_name = next(
            field["name"]
            for field in entity["fields"]
            if field["key"] == current_key_field
        )
        group = [intent for intent in updates if intent.entity_key == entity_key]
        promotions = {
            field_key
            for intent in group
            if (field_key := intent.field_key) is not None
            and intent.field_role is FieldRole.IDENTIFIER
            and field_key != current_key_field
        }
        demotes_current = any(
            intent.field_key == current_key_field
            and intent.field_role is not FieldRole.IDENTIFIER
            for intent in group
        )
        if len(promotions) > 1:
            raise PatchGenerationError("同一条修改不能把多个字段同时设为唯一标识字段。")
        if demotes_current and not promotions:
            raise PatchGenerationError(
                f"将唯一标识字段「{current_name}」降级前，需要先指定新的唯一标识字段，"
                "请在同一句话里说明哪个字段作为新的标识。"
            )
        if promotions:
            rewrites[entity_key] = next(iter(promotions))
    return rewrites


# --- builder --------------------------------------------------------------------------


class _PatchBuilder:
    def __init__(
        self, snapshot: dict[str, Any], intents: list[ParsedIntent]
    ) -> None:
        self.doc = copy.deepcopy(snapshot)
        self.intents = intents
        self.ops: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self.rewrites = _identifier_rewrites(self.doc, intents)

    def run(self) -> None:
        for intent in self.intents:
            new_ops = self._build(intent)
            if not new_ops:
                continue
            # Layers 1+2 against the in-flight document; layer 3 after each
            # intent so a failure is attributed to the right modification.
            document = PatchDocument.from_raw(new_ops)
            try:
                self.doc = apply_patch(self.doc, document.ops)
                BusinessModel.model_validate(self.doc)
            except (PatchError, ValidationError) as exc:
                raise PatchGenerationError(
                    f"修改「{intent.kind.value}」无法通过模型校验：{exc}"
                ) from exc
            self.ops.extend(new_ops)

    # -- dispatch -------------------------------------------------------------

    def _build(self, intent: ParsedIntent) -> list[dict[str, Any]]:
        kind = intent.kind
        if kind is IntentKind.RENAME:
            return self._rename(intent)
        if kind is IntentKind.ENTITY_ADD:
            return self._entity_add(intent)
        if kind is IntentKind.FIELD_ADD:
            return self._field_add(intent)
        if kind is IntentKind.FIELD_REMOVE:
            return self._field_remove(intent)
        if kind is IntentKind.FIELD_UPDATE:
            return self._field_update(intent)
        if kind in {
            IntentKind.VIEW_FILTER_ADD,
            IntentKind.VIEW_FILTER_REMOVE,
            IntentKind.VIEW_COLUMN_ADD,
            IntentKind.VIEW_COLUMN_REMOVE,
            IntentKind.VIEW_SEARCH_ADD,
            IntentKind.VIEW_SEARCH_REMOVE,
        }:
            return self._view_member_toggle(intent)
        if kind is IntentKind.VIEW_SORT_SET:
            return self._view_sort_set(intent)
        if kind is IntentKind.RELATED_LIST_ADD:
            return self._related_list_add(intent)
        if kind is IntentKind.RELATED_LIST_REMOVE:
            return self._related_list_remove(intent)
        if kind is IntentKind.METRIC_ADD:
            return self._metric_add(intent)
        if kind is IntentKind.METRIC_REMOVE:
            return self._metric_remove(intent)
        if kind is IntentKind.LINK_REMOVE:
            return self._link_remove(intent)
        raise PatchGenerationError(f"暂不支持的修改类型：{kind.value}")  # pragma: no cover

    # -- new modules ----------------------------------------------------------

    def _claim_key(self, used: set[str], base: str, fallback: str) -> str:
        key = _dedupe_key(used, base, fallback)
        used.add(key)
        return key

    def _entity_add(self, intent: ParsedIntent) -> list[dict[str, Any]]:
        assert (
            intent.new_entity_name is not None
            and intent.new_entity_key is not None
            and intent.new_fields is not None
        )
        name = intent.new_entity_name
        entity_key = intent.new_entity_key
        if any(entity["key"] == entity_key for entity in self.doc["entities"]):
            raise PatchGenerationError(
                f"系统里已经有标识为 {entity_key!r} 的模块，无法重复新增。"
            )
        if any(
            entity["name"].strip().lower() == name.lower()
            for entity in self.doc["entities"]
        ):
            raise PatchGenerationError(f"系统里已经有「{name}」模块，无法重复新增。")

        fields = [
            BusinessField(
                key=spec.key,
                name=spec.name,
                type=spec.type,
                role=spec.role,
                values=spec.values,
                confidence=USER_AUTHORED_CONFIDENCE,
                needs_review=False,
            )
            for spec in intent.new_fields
        ]
        key_field = next(
            field.key for field in fields if field.role is FieldRole.IDENTIFIER
        )
        entity = Entity(
            key=entity_key,
            name=name,
            source=None,
            key_field=key_field,
            fields=fields,
        )
        ops: list[dict[str, Any]] = [
            _add("/entities/-", entity.model_dump(mode="json"))
        ]

        used_view_keys = {view["key"] for view in self.doc["views"]}
        list_key = self._claim_key(
            used_view_keys, f"{entity_key}_list", f"{entity_key}_view"
        )
        detail_key = self._claim_key(
            used_view_keys, f"{entity_key}_detail", f"{entity_key}_detail_view"
        )
        form_key = self._claim_key(
            used_view_keys, f"{entity_key}_form", f"{entity_key}_form_view"
        )

        columns = [key_field] + [
            spec.key for spec in intent.new_fields if spec.key != key_field
        ]
        columns = columns[:MAX_MODULE_LIST_COLUMNS]
        search = list(
            dict.fromkeys(
                [key_field]
                + [
                    spec.key
                    for spec in intent.new_fields
                    if spec.type is FieldType.PHONE
                ]
            )
        )
        filters = [
            spec.key
            for spec in intent.new_fields
            if spec.key != key_field
            and (spec.type is FieldType.ENUM or spec.role is FieldRole.DIMENSION)
        ][:MAX_MODULE_FILTERS]
        time_field = next(
            (spec for spec in intent.new_fields if spec.role is FieldRole.TIME), None
        )
        sorts = (
            [SortSpec(field=time_field.key, dir=SortDir.DESC)]
            if time_field is not None
            else []
        )

        ops.append(
            _add(
                "/views/-",
                ListView(
                    kind="list",
                    key=list_key,
                    entity=entity_key,
                    title=name,
                    columns=columns,
                    search=search,
                    filters=filters,
                    sorts=sorts,
                ).model_dump(mode="json"),
            )
        )
        ops.append(
            _add(
                "/views/-",
                DetailView(
                    kind="detail",
                    key=detail_key,
                    entity=entity_key,
                    title=f"{name}详情",
                    blocks=[DetailBlock(type=DetailBlockType.FIELDS)],
                ).model_dump(mode="json"),
            )
        )
        ops.append(
            _add(
                "/views/-",
                FormView(
                    kind="form",
                    key=form_key,
                    entity=entity_key,
                    title=f"{name}表单",
                    fields=None,
                ).model_dump(mode="json"),
            )
        )

        link_notes: list[str] = []
        used_link_keys = {link["key"] for link in self.doc["links"]}
        for spec in intent.new_links or []:
            parent = _entity(self.doc, spec.entity_key)
            link_key = self._claim_key(
                used_link_keys,
                f"{spec.entity_key}_to_{entity_key}",
                f"link_{len(used_link_keys) + 1}",
            )
            link = Link(
                key=link_key,
                **{"from": spec.entity_key},
                to=entity_key,
                type=LinkType.ONE_TO_MANY,
                on=LinkOn(**{"from": parent["key_field"], "to": spec.field_key}),
                confidence=USER_AUTHORED_CONFIDENCE,
                needs_review=False,
            )
            ops.append(_add("/links/-", link.model_dump(mode="json")))
            for view_index, view in _views_of(
                self.doc, kind="detail", entity_key=spec.entity_key
            ):
                if any(
                    block.get("link") == link_key for block in view["blocks"]
                ):
                    continue
                block = DetailBlock(
                    type=DetailBlockType.RELATED_LIST,
                    link=link_key,
                    title=f"关联{name}",
                ).model_dump(mode="json")
                ops.append(_add(f"/views/{view_index}/blocks/-", block))
            link_notes.append(
                f"已建立「{parent['name']}」→「{name}」的一对多关联，"
                f"并在「{parent['name']}」详情页添加关联列表。"
            )

        metric_op = intent.metric_op or MetricOp.COUNT
        metric_field_key = (
            intent.metric_field_key if metric_op is MetricOp.SUM else None
        )
        if metric_op is MetricOp.SUM and metric_field_key is not None:
            target_field = next(
                spec for spec in intent.new_fields if spec.key == metric_field_key
            )
            metric_name = f"{target_field.name}合计"
            definition = f"{name}「{target_field.name}」的合计值"
            metric_base_key = f"total_{metric_field_key}"
        else:
            metric_op = MetricOp.COUNT
            metric_name = f"{name}总数"
            definition = f"{name}记录的总条数"
            metric_base_key = f"{entity_key}_count"
        used_metric_keys = {metric["key"] for metric in self.doc["metrics"]}
        metric_key = self._claim_key(
            used_metric_keys, metric_base_key, f"{entity_key}_metric"
        )
        metric = Metric(
            key=metric_key,
            name=metric_name,
            entity=entity_key,
            formula=MetricFormula(op=metric_op, field=metric_field_key),
            business_definition=definition,
            confidence=USER_AUTHORED_CONFIDENCE,
            needs_review=False,
        )
        ops.append(_add("/metrics/-", metric.model_dump(mode="json")))
        self._attach_metric_card(ops, metric_key)

        ops.append(
            _add(
                "/navigation/-",
                NavigationItem(label=name, view=list_key).model_dump(mode="json"),
            )
        )

        self.notes.append(
            f"新增「{name}」模块：{len(fields)} 个字段，已生成列表、详情、表单视图。"
        )
        self.notes.append(f"首页新增指标「{metric_name}」。")
        self.notes.extend(link_notes)
        return ops

    # -- rename ---------------------------------------------------------------

    def _rename(self, intent: ParsedIntent) -> list[dict[str, Any]]:
        new_name = intent.new_name
        assert new_name is not None
        if intent.entity_key is None:
            return [_replace("/app/name", new_name)]
        entity_index = _entity_index(self.doc, intent.entity_key)
        if intent.field_key is None:
            return [_replace(f"/entities/{entity_index}/name", new_name)]
        field_index = _field_index(
            _entity(self.doc, intent.entity_key), intent.field_key
        )
        return [
            _replace(
                f"/entities/{entity_index}/fields/{field_index}/name", new_name
            )
        ]

    # -- fields ---------------------------------------------------------------

    def _field_add(self, intent: ParsedIntent) -> list[dict[str, Any]]:
        assert intent.entity_key is not None and intent.new_name is not None
        entity_index = _entity_index(self.doc, intent.entity_key)
        entity = self.doc["entities"][entity_index]
        name = intent.new_name.strip()
        if any(field["name"].strip() == name for field in entity["fields"]):
            raise PatchGenerationError(
                f"实体「{entity['name']}」中已经存在同名字段「{name}」，无需重复添加。"
            )
        key = _unique_field_key(entity, name)
        field = BusinessField(
            key=key,
            name=name,
            type=intent.field_type or FieldType.STRING,
            role=intent.field_role or FieldRole.TEXT,
            values=intent.enum_values,
            confidence=USER_AUTHORED_CONFIDENCE,
            needs_review=False,
        )
        type_label = _FIELD_TYPE_LABELS[field.type]
        self.notes.append(
            f"实体「{entity['name']}」新增{type_label}字段「{name}」。"
        )
        return [_add(f"/entities/{entity_index}/fields/-", field.model_dump(mode="json"))]

    def _field_remove(self, intent: ParsedIntent) -> list[dict[str, Any]]:
        assert intent.entity_key is not None and intent.field_key is not None
        entity_key = intent.entity_key
        entity_index = _entity_index(self.doc, entity_key)
        entity = self.doc["entities"][entity_index]
        field_index = _field_index(entity, intent.field_key)
        field = entity["fields"][field_index]
        field_key, field_name = field["key"], field["name"]

        if field_key == entity["key_field"]:
            raise PatchGenerationError(
                f"不能删除唯一标识字段「{field_name}」：系统依靠它识别每一条记录。"
            )
        for link in self.doc["links"]:
            if field_key in (link["on"]["from_"], link["on"]["to"]):
                other_end = (
                    link["to"] if link["from_"] == entity_key else link["from_"]
                )
                other_name = _entity(self.doc, other_end)["name"]
                raise PatchGenerationError(
                    f"字段「{field_name}」正用于与「{other_name}」的实体关联，"
                    "请先删除该关联，再删除字段。"
                )

        ops: list[dict[str, Any]] = []
        cascades: list[str] = []
        for view_index, view in _views_of(self.doc, kind="list", entity_key=entity_key):
            touched_members = self._remove_field_from_list_view(
                ops, view_index, view, field_key
            )
            cascades.extend(touched_members)
        for view_index, view in _views_of(self.doc, kind="form", entity_key=entity_key):
            if view["fields"] is None:
                continue
            for member_index in sorted(
                (
                    index
                    for index, ref in enumerate(view["fields"])
                    if ref == field_key
                ),
                reverse=True,
            ):
                ops.append(
                    _remove(f"/views/{view_index}/fields/{member_index}")
                )
                cascades.append("表单字段")

        metric_names = self._remove_metrics_on_field(ops, entity_key, field_key)
        if metric_names:
            cascades.append("基于该字段的指标 " + "、".join(f"「{n}」" for n in metric_names))

        ops.append(_remove(f"/entities/{entity_index}/fields/{field_index}"))
        effect = f"已删除字段「{field_name}」。"
        if cascades:
            effect += "同时移除：" + "、".join(dict.fromkeys(cascades)) + "。"
        self.notes.append(effect)
        return ops

    def _remove_field_from_list_view(
        self,
        ops: list[dict[str, Any]],
        view_index: int,
        view: dict[str, Any],
        field_key: str,
    ) -> list[str]:
        labels: list[str] = []
        for member, label in (
            ("columns", "列表列"),
            ("search", "搜索字段"),
            ("filters", "筛选器"),
        ):
            if field_key in view[member]:
                if member == "columns" and len(view["columns"]) == 1:
                    raise PatchGenerationError(
                        "删除该字段会使列表没有任何展示列，操作已拒绝。"
                    )
                member_index = view[member].index(field_key)
                ops.append(
                    _remove(f"/views/{view_index}/{member}/{member_index}")
                )
                labels.append(label)
        for sort_index in sorted(
            (
                index
                for index, sort in enumerate(view["sorts"])
                if sort["field"] == field_key
            ),
            reverse=True,
        ):
            ops.append(_remove(f"/views/{view_index}/sorts/{sort_index}"))
            labels.append("默认排序")
        return labels

    def _remove_metrics_on_field(
        self, ops: list[dict[str, Any]], entity_key: str, field_key: str
    ) -> list[str]:
        targets = [
            (index, metric)
            for index, metric in enumerate(self.doc["metrics"])
            if metric["entity"] == entity_key
            and metric["formula"]["field"] == field_key
        ]
        removed_names: list[str] = []
        for metric_index, metric in reversed(targets):
            removed_names.append(metric["name"])
            self._remove_metric_card(ops, metric["key"])
            ops.append(_remove(f"/metrics/{metric_index}"))
        return list(reversed(removed_names))

    def _remove_metric_card(self, ops: list[dict[str, Any]], metric_key: str) -> None:
        for view_index, dashboard in _views_of(self.doc, kind="dashboard"):
            for card_index in sorted(
                (
                    index
                    for index, ref in enumerate(dashboard["metrics"])
                    if ref == metric_key
                ),
                reverse=True,
            ):
                ops.append(
                    _remove(f"/views/{view_index}/metrics/{card_index}")
                )

    def _field_update(self, intent: ParsedIntent) -> list[dict[str, Any]]:
        assert intent.entity_key is not None and intent.field_key is not None
        entity_index = _entity_index(self.doc, intent.entity_key)
        entity = self.doc["entities"][entity_index]
        field_index = _field_index(entity, intent.field_key)
        field = entity["fields"][field_index]
        base = f"/entities/{entity_index}/fields/{field_index}"
        ops: list[dict[str, Any]] = []

        if intent.field_type is not None:
            new_type = intent.field_type
            if new_type is FieldType.ENUM:
                raise PatchGenerationError(
                    f"暂不支持把字段「{field['name']}」直接改为枚举类型"
                    "（枚举候选值无法从一句话确定），可改为新增一个枚举字段。"
                )
            if field["type"] == FieldType.ENUM.value:
                # The patch subset forbids null-valued replace; dropping the
                # member yields values=None on re-validation.
                ops.append(_remove(f"{base}/values"))
            ops.append(_replace(f"{base}/type", new_type.value))

        if intent.field_role is not None:
            ops.append(_replace(f"{base}/role", intent.field_role.value))
            if self.rewrites.get(intent.entity_key) == intent.field_key:
                ops.append(
                    _replace(f"/entities/{entity_index}/key_field", intent.field_key)
                )
                self.notes.append(
                    f"实体「{entity['name']}」的唯一标识字段变更为「{field['name']}」，"
                    "历史数据的识别方式可能受影响。"
                )
        if not ops:  # pragma: no cover - parser rejects empty updates
            raise PatchGenerationError(f"字段「{field['name']}」没有可修改的内容。")
        return ops

    # -- list view members ----------------------------------------------------

    def _view_member_toggle(self, intent: ParsedIntent) -> list[dict[str, Any]]:
        assert intent.entity_key is not None and intent.field_key is not None
        kind = intent.kind
        member = {
            IntentKind.VIEW_FILTER_ADD: "filters",
            IntentKind.VIEW_FILTER_REMOVE: "filters",
            IntentKind.VIEW_COLUMN_ADD: "columns",
            IntentKind.VIEW_COLUMN_REMOVE: "columns",
            IntentKind.VIEW_SEARCH_ADD: "search",
            IntentKind.VIEW_SEARCH_REMOVE: "search",
        }[kind]
        adding = kind.value.endswith("_add")
        list_views = _views_of(self.doc, kind="list", entity_key=intent.entity_key)
        if not list_views:  # parser already guards; keep the generator sound
            raise PatchGenerationError("当前实体没有列表视图，无法修改列表设置。")

        ops: list[dict[str, Any]] = []
        already = missing = 0
        for view_index, view in list_views:
            refs = view[member]
            present = intent.field_key in refs
            if adding:
                if present:
                    already += 1
                    continue
                ops.append(
                    _add(
                        f"/views/{view_index}/{member}/-",
                        intent.field_key,
                    )
                )
            else:
                if not present:
                    missing += 1
                    continue
                if member == "columns" and len(refs) == 1:
                    raise PatchGenerationError("列表至少需要保留一列，不能移除最后一列。")
                ops.append(
                    _remove(
                        f"/views/{view_index}/{member}/{refs.index(intent.field_key)}"
                    )
                )
        if adding and not ops:
            self.notes.append("该列表设置已存在，无需重复添加。")
        elif not adding and not ops:
            self.notes.append("该列表设置本就不存在，无需移除。")
        return ops

    def _view_sort_set(self, intent: ParsedIntent) -> list[dict[str, Any]]:
        assert intent.entity_key is not None and intent.field_key is not None
        assert intent.sort_dir is not None
        ops: list[dict[str, Any]] = []
        for view_index, _view in _views_of(
            self.doc, kind="list", entity_key=intent.entity_key
        ):
            ops.append(
                _replace(
                    f"/views/{view_index}/sorts",
                    [{"field": intent.field_key, "dir": intent.sort_dir.value}],
                )
            )
        return ops

    # -- related lists --------------------------------------------------------

    def _related_list_add(self, intent: ParsedIntent) -> list[dict[str, Any]]:
        assert intent.entity_key is not None and intent.child_entity_key is not None
        parent_key, child_key = intent.entity_key, intent.child_entity_key
        matched = _find_link(self.doc, parent_key, child_key)
        if matched is None:
            parent_name = _entity(self.doc, parent_key)["name"]
            child_name = _entity(self.doc, child_key)["name"]
            raise PatchGenerationError(
                f"实体「{parent_name}」与「{child_name}」之间还没有关联，"
                "无法在详情页添加关联列表。"
            )
        _index, link = matched
        detail_views = _views_of(self.doc, kind="detail", entity_key=parent_key)
        if not detail_views:
            raise PatchGenerationError("当前实体没有详情视图，无法添加关联列表。")

        child_name = _entity(self.doc, child_key)["name"]
        block = DetailBlock(
            type=DetailBlockType.RELATED_LIST,
            link=link["key"],
            title=f"关联{child_name}",
        ).model_dump(mode="json")
        ops: list[dict[str, Any]] = []
        for view_index, view in detail_views:
            if any(
                block_view["type"] == DetailBlockType.RELATED_LIST.value
                and block_view["link"] == link["key"]
                for block_view in view["blocks"]
            ):
                continue
            ops.append(_add(f"/views/{view_index}/blocks/-", block))
        if not ops:
            self.notes.append(f"「{child_name}」关联列表已在详情页中，无需重复添加。")
            return ops
        self.notes.append(f"详情页新增「{child_name}」关联列表。")
        if intent.limit is not None and intent.limit != DEFAULT_RELATED_LIST_LIMIT:
            self.notes.append(
                f"V0.1 关联列表暂不支持自定义条数，「最近 {intent.limit} 笔」"
                "将按系统默认条数展示。"
            )
        return ops

    def _related_list_remove(self, intent: ParsedIntent) -> list[dict[str, Any]]:
        assert intent.entity_key is not None and intent.child_entity_key is not None
        parent_key, child_key = intent.entity_key, intent.child_entity_key
        matched = _find_link(self.doc, parent_key, child_key)
        if matched is None:
            self.notes.append("两个实体之间不存在关联，无需移除关联列表。")
            return []
        _index, link = matched
        ops: list[dict[str, Any]] = []
        removed = 0
        for view_index, view in _views_of(self.doc, kind="detail", entity_key=parent_key):
            matches = [
                block_index
                for block_index, block_view in enumerate(view["blocks"])
                if block_view["link"] == link["key"]
            ]
            if not matches:
                continue
            if len(view["blocks"]) == 1:
                raise PatchGenerationError("详情页至少需要保留一个区块，不能移除唯一区块。")
            for block_index in sorted(matches, reverse=True):
                ops.append(_remove(f"/views/{view_index}/blocks/{block_index}"))
                removed += 1
        if removed == 0:
            self.notes.append("详情页中没有该关联列表，无需移除。")
        return ops

    # -- metrics --------------------------------------------------------------

    def _metric_identity(
        self, entity: dict[str, Any], intent: ParsedIntent
    ) -> tuple[str, str]:
        op = intent.metric_op
        assert op is not None
        if op is MetricOp.COUNT:
            return f"{entity['name']}总数", f"{entity['name']}记录的总条数"
        field_name = next(
            field["name"]
            for field in entity["fields"]
            if field["key"] == intent.metric_field_key
        )
        return f"{field_name}合计", f"{entity['name']}「{field_name}」的合计值"

    def _metric_add(self, intent: ParsedIntent) -> list[dict[str, Any]]:
        assert intent.entity_key is not None and intent.metric_op is not None
        entity = _entity(self.doc, intent.entity_key)
        name, definition = self._metric_identity(entity, intent)

        duplicate = any(
            metric["entity"] == intent.entity_key
            and metric["formula"]["op"] == intent.metric_op.value
            and metric["formula"]["field"] == intent.metric_field_key
            for metric in self.doc["metrics"]
        )
        if duplicate:
            self.notes.append(f"指标「{name}」已存在，无需重复添加。")
            return []

        used = {metric["key"] for metric in self.doc["metrics"]}
        if intent.metric_op.value == "count":
            key = _dedupe_key(
                used, f"{intent.entity_key}_count", f"{intent.entity_key}_record_count"
            )
        else:
            key = _dedupe_key(
                used,
                f"total_{intent.metric_field_key}",
                f"{intent.entity_key}_total_{intent.metric_field_key}",
            )
        metric = Metric(
            key=key,
            name=name,
            entity=intent.entity_key,
            formula=MetricFormula(op=intent.metric_op, field=intent.metric_field_key),
            business_definition=definition,
            confidence=USER_AUTHORED_CONFIDENCE,
            needs_review=False,
        )
        ops = [_add("/metrics/-", metric.model_dump(mode="json"))]
        self._attach_metric_card(ops, key)
        self.notes.append(f"首页新增指标「{name}」。")
        return ops

    def _attach_metric_card(
        self, ops: list[dict[str, Any]], metric_key: str
    ) -> None:
        """Place a metric on every dashboard, creating one when none exists."""
        dashboards = _views_of(self.doc, kind="dashboard")
        if dashboards:
            for view_index, dashboard in dashboards:
                if metric_key not in dashboard["metrics"]:
                    ops.append(_add(f"/views/{view_index}/metrics/-", metric_key))
            return
        view_keys = {view["key"] for view in self.doc["views"]}
        dashboard_key = DASHBOARD_KEY
        suffix = 2
        while dashboard_key in view_keys:
            dashboard_key = f"{DASHBOARD_KEY}_{suffix}"
            suffix += 1
        dashboard = DashboardView(
            kind="dashboard",
            key=dashboard_key,
            title="首页",
            metrics=[metric_key],
        ).model_dump(mode="json")
        ops.append(_add("/views/-", dashboard))
        ops.append(
            _add(
                "/navigation/0",
                NavigationItem(label="首页", view=dashboard_key).model_dump(mode="json"),
            )
        )

    def _metric_remove(self, intent: ParsedIntent) -> list[dict[str, Any]]:
        assert intent.target_metric_key is not None
        metric_key = intent.target_metric_key
        metric_index = next(
            (
                index
                for index, metric in enumerate(self.doc["metrics"])
                if metric["key"] == metric_key
            ),
            None,
        )
        if metric_index is None:  # parser resolves existing metrics; defensive
            self.notes.append("该指标不存在，无需移除。")
            return []
        name = self.doc["metrics"][metric_index]["name"]
        ops: list[dict[str, Any]] = []
        self._remove_metric_card(ops, metric_key)
        ops.append(_remove(f"/metrics/{metric_index}"))
        self.notes.append(f"已从首页移除指标「{name}」。")
        return ops

    # -- links ----------------------------------------------------------------

    def _link_remove(self, intent: ParsedIntent) -> list[dict[str, Any]]:
        assert intent.entity_key is not None and intent.child_entity_key is not None
        matched = _find_link(
            self.doc, intent.entity_key, intent.child_entity_key
        )
        if matched is None:
            self.notes.append("两个实体之间不存在关联，无需移除。")
            return []
        link_index, link = matched

        ops: list[dict[str, Any]] = []
        removed_blocks = 0
        for view_index, view in _views_of(self.doc, kind="detail"):
            matches = [
                block_index
                for block_index, block_view in enumerate(view["blocks"])
                if block_view["link"] == link["key"]
            ]
            if not matches:
                continue
            if len(view["blocks"]) == 1:
                raise PatchGenerationError(
                    "移除该关联会使某详情页没有任何区块，操作已拒绝。"
                )
            for block_index in sorted(matches, reverse=True):
                ops.append(_remove(f"/views/{view_index}/blocks/{block_index}"))
                removed_blocks += 1
        ops.append(_remove(f"/links/{link_index}"))
        if removed_blocks:
            self.notes.append(
                f"已删除实体关联，并同时移除详情页中的 {removed_blocks} 个关联列表区块。"
            )
        return ops


# --- public entry point --------------------------------------------------------------


def generate_patches(model: BusinessModel, result: IntentParseResult) -> PatchPlan:
    """Produce and dry-run the patch for all grounded intents atomically.

    Raises ``PatchGenerationError`` when any intent cannot be expressed as a
    legal model change; in that case no partial ops are returned. Unsupported
    intents live on ``result.unsupported`` and never reach this function.
    """
    snapshot = model.model_dump(mode="json")
    builder = _PatchBuilder(snapshot, list(result.intents))
    builder.run()

    if builder.ops:
        # Authoritative run: apply the complete op sequence to the untouched
        # snapshot through exactly the same core as a real version write.
        resulting = validate_patch_snapshot(snapshot, builder.ops)
    else:
        resulting = snapshot

    requires_confirmation = any(
        intent.risk is RiskLevel.HIGH for intent in result.intents
    )
    notes = list(result.notes) + builder.notes
    if requires_confirmation:
        notes.append("本次修改包含删除字段、删除指标或删除关联等高风险操作，需确认后才会生效。")
    return PatchPlan(
        ops=builder.ops,
        requires_confirmation=requires_confirmation,
        notes=notes,
        resulting_snapshot=resulting,
    )
