"""Assemble inferred sheets into a validated Business Model (Day 11, M2).

Consumes the Day 5-10 outputs on each profiled sheet plus the Day 11 link
candidates and constructs the chapter-7 domain model: entities, links,
metrics, the deterministic view set (list/detail/form/dashboard) and
navigation. Construction goes through the domain pydantic models, so an
assembled model is by definition reference-integral.

Sheets that cannot be an entity (unknown kind, no identifier) are skipped
with an explicit assembly note instead of producing a broken model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
    AppSource,
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
    SourceRef,
)

from .keywords import PRICE_NAME_KEYWORDS, has_keyword
from .naming import unique_entity_keys, unique_field_keys
from .schemas import InferredField, InferredLink, ProfiledParsedSheet, ProfiledParsedWorkbook

MAX_LIST_COLUMNS = 6
MAX_FILTER_FIELDS = 4

# A child-side column only *behaves* as a foreign key: Excel has no FK
# constraint, so the join field itself is never high-confidence no matter how
# clean the value overlap is (design 7.1 / 10.2 禁止假确定). Its confidence is
# frozen at the medium tier and it always enters the review queue.
FK_FIELD_CONFIDENCE = 0.63
FK_FIELD_REASON_SAME_NAME = "列名一致，但源数据没有外键定义"
FK_FIELD_REASON_SEMANTIC = "字段语义与父表对应，但源数据没有外键定义"

_ROLE_ORDER = {
    "time": 0,
    "measure": 1,
    "dimension": 2,
    "enum": 3,
    "identifier": 4,
    "text": 5,
}


@dataclass(frozen=True)
class EntityPlan:
    sheet: ProfiledParsedSheet
    key: str
    name: str
    key_field_column: str
    key_field_key: str
    # original column name -> snake_case field key, in sheet column order
    field_keys: dict[str, str]


def build_entity_plans(
    workbook: ProfiledParsedWorkbook,
) -> tuple[list[EntityPlan], list[str]]:
    """Pick assemblable sheets and namespace their keys/fields."""
    eligible: list[ProfiledParsedSheet] = []
    notes: list[str] = []
    for sheet in workbook.sheets:
        entity = sheet.inferred_entity
        if sheet.is_empty or entity is None:
            continue
        if entity.key == "unknown":
            notes.append(f"工作表「{sheet.name}」未纳入模型：实体类型无法识别（unknown）")
            continue
        if not entity.key_field:
            notes.append(f"工作表「{sheet.name}」未纳入模型：缺少唯一标识字段，无法确定 key_field")
            continue
        eligible.append(sheet)

    keys = unique_entity_keys([sheet.inferred_entity.key for sheet in eligible])  # type: ignore[union-attr]

    plans: list[EntityPlan] = []
    for sheet, entity_key in zip(eligible, keys, strict=True):
        entity = sheet.inferred_entity
        assert entity is not None and entity.key_field is not None
        field_key_list, _ = unique_field_keys(sheet.columns)
        field_keys = dict(zip(sheet.columns, field_key_list, strict=True))
        plans.append(
            EntityPlan(
                sheet=sheet,
                key=entity_key,
                name=entity.name,
                key_field_column=entity.key_field,
                key_field_key=field_keys[entity.key_field],
                field_keys=field_keys,
            )
        )
    return plans, notes


def _business_field(
    column: str,
    field_key: str,
    inferred: InferredField,
    fk_review_reason: str | None = None,
) -> BusinessField:
    ftype = inferred.type
    values = inferred.enum_values if ftype == "enum" else None
    if ftype == "enum" and not values:
        # Defensive: an enum without a value set is illegal in the domain;
        # fall back to string rather than emit an invalid model.
        ftype = "string"
        values = None
    confidence = min(inferred.confidence, inferred.role_confidence)
    needs_review: bool | None = None
    review_reason: str | None = None
    if fk_review_reason is not None:
        # This column is the child side of an inferred link: no real FK exists
        # in the source, so freeze medium confidence and force review.
        confidence = FK_FIELD_CONFIDENCE
        needs_review = True
        review_reason = fk_review_reason
    return BusinessField(
        key=field_key,
        name=column,
        type=FieldType(ftype),
        role=FieldRole(inferred.role),
        confidence=confidence,
        needs_review=needs_review,
        review_reason=review_reason,
        values=values,
    )


def _fk_child_reasons(
    links: list[InferredLink],
) -> dict[tuple[str, str], str]:
    """Map (child entity key, child on-field key) to its review reason."""
    reasons: dict[tuple[str, str], str] = {}
    for link in links:
        reason = (
            FK_FIELD_REASON_SAME_NAME
            if "same_field_name" in link.signals
            else FK_FIELD_REASON_SEMANTIC
        )
        reasons[(link.to_entity, link.on_to)] = reason
    return reasons


def _build_entity(
    plan: EntityPlan,
    file_name: str,
    fk_child_reasons: dict[tuple[str, str], str],
) -> Entity:
    inferred_by_column = {field.column: field for field in plan.sheet.inferred_fields}
    fields = [
        _business_field(
            column,
            field_key,
            inferred_by_column[column],
            fk_child_reasons.get((plan.key, field_key)),
        )
        for column, field_key in plan.field_keys.items()
    ]
    return Entity(
        key=plan.key,
        name=plan.name,
        source=SourceRef(file=file_name, sheet=plan.sheet.name),
        key_field=plan.key_field_key,
        fields=fields,
    )


def _build_links(links: list[InferredLink]) -> list[Link]:
    return [
        Link(
            key=link.key,
            **{"from": link.from_entity},
            to=link.to_entity,
            type=LinkType.ONE_TO_MANY,
            on=LinkOn(**{"from": link.on_from, "to": link.on_to}),
            confidence=link.confidence,
            needs_review=True,
            review_reason=link.review_reason,
        )
        for link in links
    ]


def _build_metrics(
    plan: EntityPlan, entity: Entity, used_metric_keys: set[str]
) -> list[Metric]:
    metrics: list[Metric] = []
    count_key = f"{plan.key}_count"
    if count_key in used_metric_keys:
        count_key = f"{plan.key}_record_count"
    used_metric_keys.add(count_key)
    metrics.append(
        Metric(
            key=count_key,
            name=f"{plan.name}总数",
            entity=plan.key,
            formula=MetricFormula(op=MetricOp.COUNT),
            business_definition=f"{plan.name}记录的总条数（自动生成口径）",
        )
    )

    for field in entity.fields:
        if field.type is not FieldType.MONEY or field.role is not FieldRole.MEASURE:
            continue
        # Unit prices are catalog attributes, not flow amounts: summing them
        # across a catalog is business nonsense. Only flow money gets a metric.
        if has_keyword(field.name, PRICE_NAME_KEYWORDS):
            continue
        base_key = f"total_{field.key}"
        metric_key = base_key if base_key not in used_metric_keys else f"{plan.key}_{base_key}"
        used_metric_keys.add(metric_key)
        metrics.append(
            Metric(
                key=metric_key,
                name=f"{field.name}合计",
                entity=plan.key,
                formula=MetricFormula(op=MetricOp.SUM, field=field.key),
                business_definition=(
                    f"{plan.name}「{field.name}」的合计值（自动生成口径，需业务确认）"
                ),
            )
        )
    return metrics


def _ordered_field_keys(plan: EntityPlan, entity: Entity) -> list[str]:
    key_field_first = [entity.key_field]
    by_key = {field.key: field for field in entity.fields}

    def rank(field_key: str) -> tuple[int, int]:
        field = by_key[field_key]
        if field.type == FieldType.PHONE:
            group = 4
        else:
            group = _ROLE_ORDER.get(field.role.value, 5)
        return group, entity.fields.index(field)

    rest = [
        field.key
        for field in sorted(
            (f for f in entity.fields if f.key != entity.key_field),
            key=lambda f: rank(f.key),
        )
    ]
    return (key_field_first + rest)[:MAX_LIST_COLUMNS]


def _build_views_and_metrics(
    plans: list[EntityPlan],
    entities: dict[str, Entity],
    links: list[Link],
) -> tuple[list[Metric], list[object], list[NavigationItem]]:
    all_metrics: list[Metric] = []
    used_metric_keys: set[str] = set()
    views: list[object] = []
    navigation: list[NavigationItem] = []

    children_by_parent: dict[str, list[Link]] = {}
    for link in links:
        children_by_parent.setdefault(link.from_, []).append(link)

    for plan in plans:
        entity = entities[plan.key]
        columns = _ordered_field_keys(plan, entity)

        search = [entity.key_field] + [
            field.key for field in entity.fields if field.type is FieldType.PHONE
        ]
        filters = [
            field.key
            for field in entity.fields
            if field.key != entity.key_field
            and (field.type is FieldType.ENUM or field.role is FieldRole.DIMENSION)
        ][:MAX_FILTER_FIELDS]
        time_field = next(
            (field for field in entity.fields if field.role is FieldRole.TIME), None
        )
        sorts = (
            [SortSpec(field=time_field.key, dir=SortDir.DESC)]
            if time_field is not None
            else []
        )

        views.append(
            ListView(
                kind="list",
                key=f"{plan.key}_list",
                entity=plan.key,
                title=plan.name,
                columns=columns,
                search=search,
                filters=filters,
                sorts=sorts,
            )
        )

        blocks = [DetailBlock(type=DetailBlockType.FIELDS)]
        for child_link in children_by_parent.get(plan.key, []):
            child_name = entities[child_link.to].name
            blocks.append(
                DetailBlock(
                    type=DetailBlockType.RELATED_LIST,
                    link=child_link.key,
                    title=f"关联{child_name}",
                )
            )
        views.append(
            DetailView(
                kind="detail",
                key=f"{plan.key}_detail",
                entity=plan.key,
                title=f"{plan.name}详情",
                blocks=blocks,
            )
        )
        views.append(
            FormView(
                kind="form",
                key=f"{plan.key}_form",
                entity=plan.key,
                title=f"{plan.name}表单",
                fields=None,
            )
        )

        navigation.append(NavigationItem(label=plan.name, view=f"{plan.key}_list"))
        all_metrics.extend(_build_metrics(plan, entity, used_metric_keys))

    if all_metrics:
        views.append(
            DashboardView(
                kind="dashboard",
                key="home_dashboard",
                title="首页",
                metrics=[metric.key for metric in all_metrics],
            )
        )
        navigation.insert(0, NavigationItem(label="首页", view="home_dashboard"))

    return all_metrics, views, navigation


def _app_name(file_name: str) -> str:
    stem = Path(file_name).stem.strip()
    return stem or "未命名应用"


def assemble_model(
    workbook: ProfiledParsedWorkbook,
    plans: list[EntityPlan],
    links: list[InferredLink],
) -> BusinessModel | None:
    if not plans:
        return None

    fk_child_reasons = _fk_child_reasons(links)
    entities = [
        _build_entity(plan, workbook.file_name, fk_child_reasons) for plan in plans
    ]
    entity_map = {plan.key: entity for plan, entity in zip(plans, entities, strict=True)}
    domain_links = _build_links(links)
    metrics, views, navigation = _build_views_and_metrics(plans, entity_map, domain_links)

    return BusinessModel(
        app=AppInfo(
            name=_app_name(workbook.file_name),
            source=AppSource(files=[workbook.file_name]),
        ),
        entities=entities,
        links=domain_links,
        metrics=metrics,
        views=views,  # type: ignore[arg-type]
        navigation=navigation,
    )
