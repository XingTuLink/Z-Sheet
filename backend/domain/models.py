"""Z-Sheet Business Model — the single source of truth (design doc chapter 7).

The AI only ever produces or patches these structures; the deterministic
renderer never calls a model. Layer-2 semantic validation (reference
integrity) lives here so that no invalid Business Model can be persisted.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .confidence import default_needs_review
from .enums import (
    DetailBlockType,
    FieldRole,
    FieldType,
    LinkType,
    MetricOp,
    SortDir,
)

MODEL_SCHEMA_VERSION = "0.1"

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _check_key(value: str, label: str) -> None:
    if not _KEY_PATTERN.match(value):
        raise ValueError(
            f"{label} must be snake_case starting with a letter, got {value!r}"
        )


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ConfidenceMixin(BaseModel):
    """Every AI inference carries a confidence and an explicit review flag."""

    confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool | None = None
    review_reason: str | None = None

    @model_validator(mode="after")
    def _derive_review_flag(self) -> ConfidenceMixin:
        if self.needs_review is None:
            self.needs_review = default_needs_review(self.confidence)
        elif self.needs_review is False and self.confidence < 0.85:
            raise ValueError(
                f"needs_review cannot be False for confidence {self.confidence} "
                "(medium/low confidence must be reviewed)"
            )
        return self


class AppSource(StrictModel):
    """Application-level provenance: the set of uploaded source files."""

    files: list[str] = Field(min_length=1)


class SourceRef(StrictModel):
    """Entity-level provenance: which file/sheet an entity was read from."""

    file: str
    sheet: str | None = None


class AppInfo(StrictModel):
    name: str = Field(min_length=1)
    source: AppSource | None = None


class BusinessField(ConfidenceMixin, StrictModel):
    key: str
    name: str = Field(min_length=1)
    type: FieldType
    role: FieldRole
    values: list[str] | None = None

    @model_validator(mode="after")
    def _check_enum_values(self) -> BusinessField:
        _check_key(self.key, "field key")
        if self.type is FieldType.ENUM:
            if not self.values:
                raise ValueError(f"enum field {self.key!r} must declare non-empty values")
        elif self.values is not None:
            raise ValueError(f"field {self.key!r} may only declare values when type=enum")
        return self


class Entity(StrictModel):
    key: str
    name: str = Field(min_length=1)
    source: SourceRef | None = None
    key_field: str
    fields: list[BusinessField] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_fields(self) -> Entity:
        _check_key(self.key, "entity key")
        _check_key(self.key_field, "key_field")
        keys = [f.key for f in self.fields]
        duplicates = {k for k in keys if keys.count(k) > 1}
        if duplicates:
            raise ValueError(f"entity {self.key!r} has duplicate field keys: {sorted(duplicates)}")
        if self.key_field not in keys:
            raise ValueError(
                f"entity {self.key!r} key_field {self.key_field!r} is not declared in fields"
            )
        return self

    def field_keys(self) -> set[str]:
        return {f.key for f in self.fields}


class LinkOn(StrictModel):
    from_: str = Field(alias="from")
    to: str


class Link(ConfidenceMixin, StrictModel):
    key: str
    from_: str = Field(alias="from")
    to: str
    type: LinkType
    on: LinkOn

    @model_validator(mode="after")
    def _check_key(self) -> Link:
        _check_key(self.key, "link key")
        _check_key(self.from_, "link from")
        _check_key(self.to, "link to")
        return self


class MetricFormula(StrictModel):
    op: MetricOp
    field: str | None = None

    @model_validator(mode="after")
    def _check_field(self) -> MetricFormula:
        if self.op is not MetricOp.COUNT and self.field is None:
            raise ValueError(f"metric op {self.op.value} requires a field")
        return self


class Metric(StrictModel):
    key: str
    name: str = Field(min_length=1)
    entity: str
    formula: MetricFormula
    business_definition: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_key(self) -> Metric:
        _check_key(self.key, "metric key")
        _check_key(self.entity, "metric entity")
        return self


class SortSpec(StrictModel):
    field: str
    dir: SortDir = SortDir.ASC


class ListView(StrictModel):
    kind: Literal["list"]
    key: str
    entity: str
    title: str = Field(min_length=1)
    columns: list[str] = Field(min_length=1)
    search: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    sorts: list[SortSpec] = Field(default_factory=list)


class DetailBlock(StrictModel):
    type: DetailBlockType
    link: str | None = None
    title: str | None = None

    @model_validator(mode="after")
    def _check_block(self) -> DetailBlock:
        if self.type is DetailBlockType.RELATED_LIST and not self.link:
            raise ValueError("related_list block requires a link")
        if self.type is DetailBlockType.FIELDS and self.link is not None:
            raise ValueError("fields block must not declare a link")
        return self


class DetailView(StrictModel):
    kind: Literal["detail"]
    key: str
    entity: str
    title: str = Field(min_length=1)
    blocks: list[DetailBlock] = Field(min_length=1)


class FormView(StrictModel):
    kind: Literal["form"]
    key: str
    entity: str
    title: str = Field(min_length=1)
    # None means "all fields of the entity"; an explicit list overrides it.
    fields: list[str] | None = None


class DashboardView(StrictModel):
    kind: Literal["dashboard"]
    key: str
    title: str = Field(min_length=1)
    metrics: list[str] = Field(default_factory=list)


View = Annotated[
    ListView | DetailView | FormView | DashboardView,
    Field(discriminator="kind"),
]


class NavigationItem(StrictModel):
    label: str = Field(min_length=1)
    view: str


class BusinessModel(StrictModel):
    version: Literal["0.1"] = MODEL_SCHEMA_VERSION
    app: AppInfo
    entities: list[Entity] = Field(min_length=1)
    links: list[Link] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    views: list[View] = Field(default_factory=list)
    navigation: list[NavigationItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_references(self) -> BusinessModel:
        errors: list[str] = []

        entity_map: dict[str, Entity] = {}
        for e in self.entities:
            if e.key in entity_map:
                errors.append(f"duplicate entity key {e.key!r}")
            entity_map[e.key] = e

        def require_entity(ref: str, where: str) -> Entity | None:
            if ref not in entity_map:
                errors.append(f"{where} references unknown entity {ref!r}")
                return None
            return entity_map[ref]

        link_keys: set[str] = set()
        for link in self.links:
            if link.key in link_keys:
                errors.append(f"duplicate link key {link.key!r}")
            link_keys.add(link.key)
            source = require_entity(link.from_, f"link {link.key!r} from")
            target = require_entity(link.to, f"link {link.key!r} to")
            if source and link.on.from_ not in source.field_keys():
                errors.append(
                    f"link {link.key!r} on.from {link.on.from_!r} "
                    f"is not a field of entity {link.from_!r}"
                )
            if target and link.on.to not in target.field_keys():
                errors.append(
                    f"link {link.key!r} on.to {link.on.to!r} "
                    f"is not a field of entity {link.to!r}"
                )

        metric_keys: set[str] = set()
        for metric in self.metrics:
            if metric.key in metric_keys:
                errors.append(f"duplicate metric key {metric.key!r}")
            metric_keys.add(metric.key)
            owner = require_entity(metric.entity, f"metric {metric.key!r}")
            if owner and metric.formula.field and metric.formula.field not in owner.field_keys():
                errors.append(
                    f"metric {metric.key!r} field {metric.formula.field!r} "
                    f"is not a field of entity {metric.entity!r}"
                )

        view_keys: set[str] = set()
        for view in self.views:
            if view.key in view_keys:
                errors.append(f"duplicate view key {view.key!r}")
            view_keys.add(view.key)
            _check_key(view.key, "view key")

            if isinstance(view, (ListView, DetailView, FormView)):
                owner = require_entity(view.entity, f"view {view.key!r}")
                if owner is None:
                    continue
                field_keys = owner.field_keys()
                if isinstance(view, ListView):
                    for col in view.columns:
                        if col not in field_keys:
                            errors.append(
                                f"list view {view.key!r} column {col!r} "
                                f"not on entity {view.entity!r}"
                            )
                    for ref in (*view.search, *view.filters):
                        if ref not in field_keys:
                            errors.append(
                                f"list view {view.key!r} search/filter {ref!r} "
                                f"not on entity {view.entity!r}"
                            )
                    for sort in view.sorts:
                        if sort.field not in field_keys:
                            errors.append(
                                f"list view {view.key!r} sort {sort.field!r} "
                                f"not on entity {view.entity!r}"
                            )
                elif isinstance(view, FormView) and view.fields is not None:
                    for ref in view.fields:
                        if ref not in field_keys:
                            errors.append(
                                f"form view {view.key!r} field {ref!r} "
                                f"not on entity {view.entity!r}"
                            )
                elif isinstance(view, DetailView):
                    for block in view.blocks:
                        if block.type is DetailBlockType.RELATED_LIST:
                            link = next(
                                (lk for lk in self.links if lk.key == block.link), None
                            )
                            if link is None:
                                errors.append(
                                    f"detail view {view.key!r} references "
                                    f"unknown link {block.link!r}"
                                )
                            elif view.entity not in (link.from_, link.to):
                                errors.append(
                                    f"detail view {view.key!r} link {link.key!r} "
                                    f"does not touch entity {view.entity!r}"
                                )
            else:  # DashboardView
                for ref in view.metrics:
                    if ref not in metric_keys:
                        errors.append(f"dashboard {view.key!r} references unknown metric {ref!r}")

        for index, item in enumerate(self.navigation):
            if item.view not in view_keys:
                errors.append(f"navigation[{index}] references unknown view {item.view!r}")

        if errors:
            raise ValueError("invalid Business Model: " + "; ".join(errors))
        return self
