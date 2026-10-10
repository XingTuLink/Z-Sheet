"""Day 23 — NL Patch Intent Parser.

Turns one Chinese sentence ("客户列表增加跟进状态筛选。") into a list of
**structured intents** grounded against the current Business Model. This stage
stops at intent: it never produces a patch and never mutates anything. Day 24
turns validated intents into a JSON Patch; Day 25 adds preview/confirm UI.

Architectural rules (design doc chapters 6, 8, 16):

- The LLM proposes intents referencing entities/fields by their *displayed
  names*; this module resolves them to real model keys and rejects anything
  that does not exist. The LLM cannot invent nodes.
- Whether an intent is destructive is decided **here, deterministically**
  (chapter 8 layer 3), never trusted from the model.
- Requests outside V0.1 (conditional formatting, data edits, time-window
  metrics, many-to-many…) come back as structured ``UnsupportedIntent`` with
  a Chinese, user-facing reason instead of a silent failure.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.ai.provider import ChatClient
from backend.domain.enums import FieldRole, FieldType, MetricOp, SortDir
from backend.domain.models import (
    BusinessModel,
    DetailView,
    Entity,
    FormView,
    ListView,
)

# --- intent taxonomy -----------------------------------------------------------------


class IntentKind(StrEnum):
    """Every model modification V0.1 can express as a patch.

    Keep this list aligned with what Day 24's patch generator can emit and
    with the renderer's V0.1 capabilities (design doc chapter 16).
    """

    RENAME = "rename"
    FIELD_ADD = "field_add"
    FIELD_REMOVE = "field_remove"
    FIELD_UPDATE = "field_update"
    VIEW_FILTER_ADD = "view_filter_add"
    VIEW_FILTER_REMOVE = "view_filter_remove"
    VIEW_SORT_SET = "view_sort_set"
    VIEW_COLUMN_ADD = "view_column_add"
    VIEW_COLUMN_REMOVE = "view_column_remove"
    VIEW_SEARCH_ADD = "view_search_add"
    VIEW_SEARCH_REMOVE = "view_search_remove"
    RELATED_LIST_ADD = "related_list_add"
    RELATED_LIST_REMOVE = "related_list_remove"
    METRIC_ADD = "metric_add"
    METRIC_REMOVE = "metric_remove"
    LINK_REMOVE = "link_remove"


class RiskLevel(StrEnum):
    LOW = "low"
    HIGH = "high"


# Chapter 8 layer 3: destructive changes always need an explicit confirmation.
HIGH_RISK_KINDS: frozenset[IntentKind] = frozenset(
    {
        IntentKind.FIELD_REMOVE,
        IntentKind.FIELD_UPDATE,
        IntentKind.METRIC_REMOVE,
        IntentKind.LINK_REMOVE,
    }
)

# V0.1 metrics, mirroring the deterministic assembler: count and sum only.
INTENT_METRIC_OPS: frozenset[str] = frozenset({MetricOp.COUNT, MetricOp.SUM})
_NUMERIC_FIELD_TYPES: frozenset[str] = frozenset({FieldType.NUMBER, FieldType.MONEY})

RELATED_LIST_MIN, RELATED_LIST_MAX = 1, 50
DEFAULT_RELATED_LIST_LIMIT = 10
MAX_NEW_NAME_LENGTH = 30

# entity/field intents that genuinely need the entity slot
_ENTITY_REQUIRED_KINDS: frozenset[IntentKind] = frozenset(set(IntentKind) - {IntentKind.RENAME})


class UnsupportedCategory(StrEnum):
    """Why a sentence cannot become a V0.1 model patch."""

    DATA_QUERY = "data_query"          # temporary filtering/search, not a model change
    DATA_EDIT = "data_edit"            # create/edit/delete business rows
    CONDITIONAL_FORMAT = "conditional_format"  # 标红/颜色/样式
    TIME_WINDOW_METRIC = "time_window_metric"  # 本月/本周/同比环比
    ENTITY_CHANGE = "entity_change"    # add/remove whole entities (re-upload)
    MANY_TO_MANY = "many_to_many"
    LAYOUT = "layout"                  # drag-drop/自由布局
    PERMISSION = "permission"
    WORKFLOW = "workflow"              # 审批/通知/工作流
    BI = "bi"                          # pivot/complex charts
    EXCEL_SYNC = "excel_sync"          # Excel 双向同步
    UNKNOWN = "unknown"


_SUPPORTED_CATEGORIES = frozenset(UnsupportedCategory)

# --- structured output ----------------------------------------------------------------


class ParsedIntent(BaseModel):
    """One grounded, validated modification intent."""

    model_config = ConfigDict(extra="forbid")

    kind: IntentKind
    risk: RiskLevel
    # Display name of the addressed entity (None only for app-level rename).
    entity_key: str | None = None
    field_key: str | None = None
    # Optional second target (related-list child entity / metric entity…).
    child_entity_key: str | None = None
    # Typed parameter slots; only the ones relevant to `kind` are populated.
    new_name: str | None = None
    field_type: FieldType | None = None
    field_role: FieldRole | None = None
    enum_values: list[str] | None = None
    sort_dir: SortDir | None = None
    limit: int | None = None
    metric_op: MetricOp | None = None
    metric_field_key: str | None = None
    # metric_remove addresses an existing metric by its own key.
    target_metric_key: str | None = None
    # True when the field_update would change which field is the identifier.
    changes_identifier: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return max(0.0, min(1.0, value))


class UnsupportedIntent(BaseModel):
    """A request the parser understood but V0.1 cannot fulfill via a patch."""

    model_config = ConfigDict(extra="forbid")

    category: UnsupportedCategory
    reason: str = Field(min_length=1)
    suggestion: str = ""
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class IntentParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_text: str = Field(min_length=1)
    intents: list[ParsedIntent] = Field(default_factory=list)
    unsupported: list[UnsupportedIntent] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.intents and not self.unsupported


# --- raw LLM wire shape ---------------------------------------------------------------


class LLMIntentSpec(BaseModel):
    """Lenient per-item LLM output; resolved/validated against the model later."""

    model_config = ConfigDict(extra="ignore")

    kind: str
    entity: str | None = None
    field: str | None = None
    child_entity: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    category: str | None = None
    reason: str = ""
    suggestion: str = ""
    confidence: float = 0.7

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return max(0.0, min(1.0, value))


class LLMIntentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intents: list[LLMIntentSpec] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


INTENT_SYSTEM_PROMPT = """你是 Z-Sheet 的「修改意图理解器」。用户已从一个 Excel 生成了业务系统，
现在用一句话提出修改。你的任务是把这句话解析成**结构化意图 JSON**，不生成补丁、不改数据。

## 输出格式（仅输出一个 JSON 对象，不要输出其它内容）

{
  "intents": [
    {
      "kind": "意图类型",
      "entity": "实体名称（与输入模型中的 name 或 key 完全一致）",
      "field": "字段名称（与输入模型中字段 name 或 key 一致；不涉及字段可省略）",
      "child_entity": "第二个实体（仅详情页关联列表等场景）",
      "params": {"按意图类型填写参数键值"},
      "reason": "一句中文说明你的理解",
      "confidence": 0.9
    }
  ],
  "notes": ["需要提醒用户的歧义或补充说明"]
}

## 可解析的意图类型（kind）与 params

- rename：改名。target 由 entity/field 决定：
  - 都省略 → 改应用名，params: {"new_name": "新名称"}；
  - 只有 entity → 改实体显示名；
  - entity + field → 改字段显示名（params.new_name）。
- field_add：实体新增字段。params:
  {"name": "字段中文名", "type": "string|number|money|date|enum|phone",
   "role": "identifier|dimension|measure|time|enum|text",
   "values": ["枚举值"]（仅 enum 给）}。
- field_remove：删除实体的已有字段（高风险，如实解析即可）。
- field_update：修改字段类型/角色。params: {"type": "...", "role": "..."}，只填用户明确要求的部分。
- view_filter_add / view_filter_remove：实体列表增加/移除筛选器，field 为筛选字段。
- view_sort_set：设置列表默认排序，params: {"dir": "asc|desc"}，field 为排序字段。
- view_column_add / view_column_remove：列表增加/移除展示列，field 为目标字段。
- view_search_add / view_search_remove：列表增加/移除搜索字段，field 为目标字段。
- related_list_add：实体详情页增加子实体关联列表，child_entity 填子实体，
  params: {"limit": 10}（“最近 N 笔”里的条数，整数）。
- related_list_remove：实体详情页移除某子实体关联列表，child_entity 填子实体。
- metric_add：首页增加指标。params: {"op": "count|sum", "field": "金额列名"}；
  count 不给 field（实体记录总数）；sum 的 field 必须是金额/数值列。
- metric_remove：移除已有指标，field 省略；
  params: {"metric_key": "指标 key"} 或在 reason 中写指标名。
- link_remove：移除实体间一对多关联，child_entity 填关联对端实体。

## 无法用 V0.1 模型补丁完成的请求，也放进 intents，kind 固定为 "unsupported"：
{"kind": "unsupported", "category": "下面之一", "reason": "中文原因",
 "suggestion": "可选的替代建议", "confidence": 0.9}

category 取值：
- data_query：临时查数据/筛选数据（如“把华东客户筛出来看看”）——这不是改模型，列表页可直接筛选；
- data_edit：新增/修改/删除业务数据行（如“把张三的电话改成…”）——应在系统页面直接操作数据；
- conditional_format：标红、颜色、条件格式、样式——V0.1 渲染器不支持；
- time_window_metric：本月/本周/本年/同比/环比/趋势等带时间窗的统计——V0.1 指标不支持时间过滤；
- entity_change：新增/删除整张实体表——需要重新上传包含对应工作表的 Excel；
- many_to_many：多对多关系——V0.1 只支持一对多；
- layout：拖拽布局、页面位置调整；
- permission：权限、角色、账号；
- workflow：审批流、通知、提醒、自动化；
- bi：透视表、复杂图表；
- excel_sync：与 Excel 双向同步；
- unknown：不属于以上任何一类、且无法映射到模型修改。

## 规则

1. 只能引用输入模型里真实存在的实体/字段名称，严禁编造；不确定就放 notes 或解析为 unsupported。
2. 一句话可能包含多个意图（如“列表加跟进状态筛选并按更新时间倒序”），拆成两条。
3. 与修改模型无关的寒暄、提问（“这是什么”“怎么做的”）输出空 intents 并在 notes 说明。
4. params 只放与该 kind 相关的键；new_name 原样保留中文，不要擅自美化。
5. 不要输出 JSON 以外的任何文字。
"""


def build_model_digest(model: BusinessModel) -> dict[str, Any]:
    """Serialize the live model for grounding: names, keys and view settings."""
    entities: list[dict[str, Any]] = []
    for entity in model.entities:
        entities.append(
            {
                "key": entity.key,
                "name": entity.name,
                "key_field": entity.key_field,
                "fields": [
                    {
                        "key": f.key,
                        "name": f.name,
                        "type": f.type.value,
                        "role": f.role.value,
                        **({"values": f.values} if f.values is not None else {}),
                    }
                    for f in entity.fields
                ],
            }
        )
    links = [
        {"key": link.key, "from": link.from_, "to": link.to}
        for link in model.links
    ]
    metrics = [
        {
            "key": m.key,
            "name": m.name,
            "entity": m.entity,
            "op": m.formula.op.value,
            "field": m.formula.field,
        }
        for m in model.metrics
    ]
    views: list[dict[str, Any]] = []
    for view in model.views:
        item: dict[str, Any] = {
            "key": view.key,
            "kind": view.kind,
            "title": view.title,
        }
        # Only list/detail/form views are bound to an entity; the dashboard
        # carries metrics instead.
        if isinstance(view, ListView):
            item.update(
                entity=view.entity,
                columns=list(view.columns),
                search=list(view.search),
                filters=list(view.filters),
                sorts=[
                    {"field": sort.field, "dir": sort.dir.value}
                    for sort in view.sorts
                ],
            )
        elif isinstance(view, DetailView | FormView):
            item["entity"] = view.entity
        views.append(item)
    return {
        "app": {"name": model.app.name},
        "entities": entities,
        "links": links,
        "metrics": metrics,
        "views": views,
    }


def build_messages(model: BusinessModel, user_text: str) -> list[dict[str, str]]:
    digest = json.dumps(build_model_digest(model), ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": INTENT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"当前系统模型：\n{digest}\n\n"
                f"用户的修改要求（原文）：\n{user_text.strip()}\n\n"
                "请输出结构化意图 JSON。"
            ),
        },
    ]


# --- grounding helpers ---------------------------------------------------------------


def _resolve_node(name: str | None, candidates: dict[str, str]) -> str | None:
    """Map a user/LLM-visible name to a key.

    Candidates map key -> display name. Match order: exact key, exact display
    name, case-insensitive name, unique substring containment.
    """
    if not name:
        return None
    raw = name.strip()
    if not raw:
        return None
    if raw in candidates:  # exact key
        return raw
    for key, display in candidates.items():
        if raw == display:
            return key
    lowered = raw.lower()
    for key, display in candidates.items():
        if lowered == display.lower():
            return key
    fuzzy = [key for key, display in candidates.items() if raw in display or display in raw]
    if len(fuzzy) == 1:
        return fuzzy[0]
    return None


def _entity_candidates(model: BusinessModel) -> dict[str, str]:
    return {e.key: e.name for e in model.entities}


def _field_candidates(entity: Entity) -> dict[str, str]:
    return {f.key: f.name for f in entity.fields}


def _find_entity(model: BusinessModel, name: str | None) -> Entity | None:
    key = _resolve_node(name, _entity_candidates(model))
    if key is None:
        return None
    return next((e for e in model.entities if e.key == key), None)


def _require_list_view(model: BusinessModel, entity_key: str) -> ListView | None:
    for view in model.views:
        if isinstance(view, ListView) and view.entity == entity_key:
            return view
    return None


def _risk_for(kind: IntentKind) -> RiskLevel:
    return RiskLevel.HIGH if kind in HIGH_RISK_KINDS else RiskLevel.LOW


def _clean_new_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > MAX_NEW_NAME_LENGTH:
        text = text[:MAX_NEW_NAME_LENGTH]
    return text


def _build_unsupported(spec: LLMIntentSpec) -> UnsupportedIntent | None:
    category = (spec.category or UnsupportedCategory.UNKNOWN).strip().lower()
    if category not in _SUPPORTED_CATEGORIES:
        category = UnsupportedCategory.UNKNOWN
    reason = spec.reason.strip() or "该修改在 V0.1 中不受支持。"
    return UnsupportedIntent(
        category=UnsupportedCategory(category),
        reason=reason,
        suggestion=spec.suggestion.strip(),
        confidence=spec.confidence,
    )


def _ground_intent(
    model: BusinessModel,
    spec: LLMIntentSpec,
    notes: list[str],
) -> ParsedIntent | UnsupportedIntent | None:
    raw_kind = spec.kind.strip().lower()
    if raw_kind == "unsupported":
        return _build_unsupported(spec)
    try:
        kind = IntentKind(raw_kind)
    except ValueError:
        notes.append(f"LLM 返回了未知的修改意图类型 {raw_kind!r}，已忽略")
        return None

    params = spec.params or {}

    # metric_remove is addressed by the globally unique metric key; the entity
    # is recovered from the metric, so it bypasses the entity-required gate.
    if kind is IntentKind.METRIC_REMOVE:
        metric_ref = params.get("metric_key") or spec.field or spec.entity or spec.reason
        metric_key = _resolve_node(
            str(metric_ref) if metric_ref else None,
            {m.key: m.name for m in model.metrics},
        )
        if metric_key is None:
            notes.append(f"要移除的指标 {metric_ref!r} 在首页指标中不存在，已忽略")
            return None
        target_metric = next(m for m in model.metrics if m.key == metric_key)
        return ParsedIntent(
            kind=kind,
            risk=RiskLevel.HIGH,
            entity_key=target_metric.entity,
            target_metric_key=metric_key,
            confidence=spec.confidence,
            reason=spec.reason.strip(),
        )

    # App-level rename is the only other intent without an entity.
    if kind is IntentKind.RENAME and not spec.entity:
        new_name = _clean_new_name(params.get("new_name"))
        if not new_name:
            notes.append("改名意图缺少新名称（params.new_name），已忽略")
            return None
        return ParsedIntent(
            kind=kind,
            risk=RiskLevel.LOW,
            new_name=new_name,
            confidence=spec.confidence,
            reason=spec.reason.strip(),
        )

    if kind in _ENTITY_REQUIRED_KINDS and not spec.entity:
        notes.append(f"意图 {kind.value} 缺少实体名称，已忽略")
        return None

    entity = _find_entity(model, spec.entity)
    if entity is None:
        notes.append(
            f"意图 {kind.value} 引用的实体 {spec.entity!r} 在当前系统中不存在或名称有歧义，已忽略"
        )
        return None

    base: dict[str, Any] = {
        "kind": kind,
        "risk": _risk_for(kind),
        "entity_key": entity.key,
        "confidence": spec.confidence,
        "reason": spec.reason.strip(),
    }

    # Intents that additionally address a field of the entity.
    needs_field = kind in {
        IntentKind.RENAME,
        IntentKind.FIELD_REMOVE,
        IntentKind.FIELD_UPDATE,
        IntentKind.VIEW_FILTER_ADD,
        IntentKind.VIEW_FILTER_REMOVE,
        IntentKind.VIEW_SORT_SET,
        IntentKind.VIEW_COLUMN_ADD,
        IntentKind.VIEW_COLUMN_REMOVE,
        IntentKind.VIEW_SEARCH_ADD,
        IntentKind.VIEW_SEARCH_REMOVE,
    }
    # metric_add sum addresses an existing numeric column too.
    metric_field_required = kind is IntentKind.METRIC_ADD and str(
        params.get("op", MetricOp.COUNT)
    ) != MetricOp.COUNT.value

    if needs_field or kind is IntentKind.FIELD_ADD or metric_field_required:
        # metric_add sum carries its target column in params.field per the
        # prompt contract; every other kind addresses the field top-level.
        field_ref = spec.field
        if kind is IntentKind.METRIC_ADD and not field_ref:
            field_ref = params.get("field")
        field_key = _resolve_node(field_ref, _field_candidates(entity))
        if field_key is None:
            if kind is IntentKind.FIELD_ADD:
                field_key = None  # brand-new field; validated separately below
            else:
                notes.append(
                    f"意图 {kind.value} 引用的字段 {spec.field!r} 在实体「{entity.name}」"
                    "中不存在或名称有歧义，已忽略"
                )
                return None
        base["field_key"] = field_key

    if kind is IntentKind.RENAME:
        new_name = _clean_new_name(params.get("new_name"))
        if not new_name:
            notes.append("字段改名意图缺少新名称（params.new_name），已忽略")
            return None
        base["new_name"] = new_name
        return ParsedIntent(**base)

    if kind is IntentKind.FIELD_ADD:
        new_name = _clean_new_name(params.get("name")) or _clean_new_name(spec.field)
        if not new_name:
            notes.append(f"实体「{entity.name}」新增字段意图缺少字段名，已忽略")
            return None
        try:
            field_type = FieldType(str(params.get("type", FieldType.STRING)).strip())
            field_role = FieldRole(str(params.get("role", FieldRole.TEXT)).strip())
        except ValueError:
            notes.append(
                f"实体「{entity.name}」新增字段 {new_name!r} 的类型/角色不合法，已忽略"
            )
            return None
        enum_values = params.get("values")
        if field_type is FieldType.ENUM:
            if not isinstance(enum_values, list) or not all(
                isinstance(v, str) and v.strip() for v in enum_values
            ):
                notes.append(
                    f"实体「{entity.name}」新增枚举字段 {new_name!r} 缺少非空枚举值，已忽略"
                )
                return None
            enum_values = [str(v).strip() for v in enum_values]
        elif enum_values:
            enum_values = None
        base.update(
            new_name=new_name,
            field_type=field_type,
            field_role=field_role,
            enum_values=enum_values,
        )
        return ParsedIntent(**base)

    if kind is IntentKind.FIELD_UPDATE:
        target_field = next(f for f in entity.fields if f.key == base["field_key"])
        changes_identifier = (
            target_field.role is not FieldRole.IDENTIFIER
            and str(params.get("role", "")).strip() == FieldRole.IDENTIFIER.value
        ) or (
            target_field.key == entity.key_field
            and str(params.get("role", "")).strip()
            not in {"", FieldRole.IDENTIFIER.value}
        )
        raw_type = params.get("type")
        raw_role = params.get("role")
        try:
            field_type = FieldType(str(raw_type).strip()) if raw_type is not None else None
            field_role = FieldRole(str(raw_role).strip()) if raw_role is not None else None
        except ValueError:
            notes.append(
                f"字段「{target_field.name}」修改后的类型/角色不合法，已忽略"
            )
            return None
        if field_type is None and field_role is None:
            notes.append(f"字段「{target_field.name}」的修改意图没有给出新类型或新角色，已忽略")
            return None
        base.update(
            field_type=field_type,
            field_role=field_role,
            changes_identifier=changes_identifier,
        )
        return ParsedIntent(**base)

    if kind in {
        IntentKind.VIEW_FILTER_ADD,
        IntentKind.VIEW_FILTER_REMOVE,
        IntentKind.VIEW_COLUMN_ADD,
        IntentKind.VIEW_COLUMN_REMOVE,
        IntentKind.VIEW_SEARCH_ADD,
        IntentKind.VIEW_SEARCH_REMOVE,
        IntentKind.VIEW_SORT_SET,
    } and _require_list_view(model, entity.key) is None:
        notes.append(f"实体「{entity.name}」没有列表视图，无法执行 {kind.value}，已忽略")
        return None

    if kind is IntentKind.VIEW_SORT_SET:
        try:
            base["sort_dir"] = SortDir(str(params.get("dir", SortDir.DESC)).strip())
        except ValueError:
            notes.append(
                f"实体「{entity.name}」排序意图的方向 {params.get('dir')!r} 非法，已忽略"
            )
            return None
        return ParsedIntent(**base)

    if kind in {IntentKind.RELATED_LIST_ADD, IntentKind.RELATED_LIST_REMOVE}:
        child = _find_entity(model, spec.child_entity)
        if child is None:
            notes.append(
                f"关联列表意图引用的子实体 {spec.child_entity!r} 不存在或名称有歧义，已忽略"
            )
            return None
        base["child_entity_key"] = child.key
        if kind is IntentKind.RELATED_LIST_ADD:
            raw_limit = params.get("limit", DEFAULT_RELATED_LIST_LIMIT)
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                limit = DEFAULT_RELATED_LIST_LIMIT
            base["limit"] = max(RELATED_LIST_MIN, min(RELATED_LIST_MAX, limit))
        return ParsedIntent(**base)

    if kind is IntentKind.METRIC_ADD:
        op_value = str(params.get("op", MetricOp.COUNT)).strip()
        if op_value not in INTENT_METRIC_OPS:
            notes.append(
                f"实体「{entity.name}」新增指标使用了 V0.1 不支持的算子 {op_value!r}，"
                "当前仅支持 count/sum，已忽略"
            )
            return None
        metric_field_key: str | None = None
        if op_value == MetricOp.SUM.value:
            metric_field_key = base.get("field_key")
            target = next(
                (f for f in entity.fields if f.key == metric_field_key), None
            )
            if target is None or target.type.value not in _NUMERIC_FIELD_TYPES:
                notes.append(
                    f"实体「{entity.name}」的 sum 指标必须引用已有的数值/金额列，已忽略"
                )
                return None
        base.update(metric_op=MetricOp(op_value), metric_field_key=metric_field_key)
        base["field_key"] = None  # metric field lives in metric_field_key
        return ParsedIntent(**base)

    if kind is IntentKind.LINK_REMOVE:
        child = _find_entity(model, spec.child_entity)
        if child is None:
            notes.append(
                f"移除关联意图引用的对端实体 {spec.child_entity!r} 不存在或名称有歧义，已忽略"
            )
            return None
        if not any(
            {edge.from_, edge.to} == {entity.key, child.key}
            for edge in model.links
        ):
            notes.append(
                f"实体「{entity.name}」与「{child.name}」之间不存在关联，已忽略"
            )
            return None
        base["child_entity_key"] = child.key
        return ParsedIntent(**base)

    # view_filter/column/search add/remove need no extra params.
    return ParsedIntent(**base)


# --- public entry point --------------------------------------------------------------


def parse_intents(
    model: BusinessModel,
    user_text: str,
    llm_client: ChatClient,
) -> IntentParseResult:
    """Parse one NL modification sentence into grounded intents.

    Raises the provider's ``LLMConfigError``/``LLMCallError`` on
    configuration/network failures — NL parsing has no deterministic
    fallback, the caller surfaces those as a user-visible error.
    """
    text = user_text.strip()
    if not text:
        raise ValueError("user_text must not be empty")

    data = llm_client.complete_json(build_messages(model, text))
    try:
        response = LLMIntentResponse.model_validate(data)
    except Exception as exc:  # malformed structured output
        raise ValueError(f"LLM 意图响应无法解析：{exc}") from exc

    notes = list(response.notes)
    intents: list[ParsedIntent] = []
    unsupported: list[UnsupportedIntent] = []
    for spec in response.intents:
        if not spec.kind.strip():
            notes.append("LLM 返回了缺少 kind 的意图项，已忽略")
            continue
        grounded = _ground_intent(model, spec, notes)
        if isinstance(grounded, ParsedIntent):
            intents.append(grounded)
        elif isinstance(grounded, UnsupportedIntent):
            unsupported.append(grounded)

    if not intents and not unsupported and not notes:
        notes.append("未能从这句话中识别出可执行的系统修改意图，请换一种说法。")

    return IntentParseResult(
        user_text=text, intents=intents, unsupported=unsupported, notes=notes
    )
