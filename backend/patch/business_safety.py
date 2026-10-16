"""Design 8.3 third-layer business-safety checks, as a pure function.

The NL preview path enforces these implicitly: the deterministic generator
refuses a sentence that cannot become a business-sane change, and everything
else is gated behind an explicit preview → confirm review (design 8.3 / 9.2).
The raw ``POST /{app}/patches`` write path has no such review, so it must reject
the same categories outright rather than silently rewriting identity, key-field
types or metric definitions, or deleting structure.

Every check is transition-based (it fires only when this patch *introduces* the
risk), so a model that already carries an oddity is not punished by unrelated
edits. Pure: the pre-patch snapshot and the validated post-patch model go in, a
list of risks comes out. No database, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.domain.enums import FieldRole
from backend.domain.models import BusinessModel

# Risk categories, mirroring design 8.3 第三层：业务安全校验.
ENTITY_REMOVED = "entity_removed"
FIELD_REMOVED = "field_removed"
IDENTIFIER_CHANGED = "identifier_changed"
IDENTIFIER_DEMOTED = "identifier_demoted"
IDENTIFIER_CONFLICT = "identifier_conflict"
KEY_FIELD_TYPE_CHANGED = "key_field_type_changed"
METRIC_DEFINITION_CHANGED = "metric_definition_changed"
METRIC_REMOVED = "metric_removed"
LINK_REMOVED = "link_removed"


@dataclass(frozen=True)
class BusinessRisk:
    category: str
    message: str


class BusinessSafetyError(Exception):
    """A change trips design 8.3 and needs the review gate, not a raw write."""

    def __init__(self, risks: list[BusinessRisk]) -> None:
        super().__init__("；".join(risk.message for risk in risks))
        self.risks = risks


def _identifier_count(entity: Any) -> int:
    return sum(1 for field in entity.fields if field.role is FieldRole.IDENTIFIER)


def detect_business_risks(
    before: dict[str, Any], after: BusinessModel
) -> list[BusinessRisk]:
    """Return every design-8.3 risk the change introduces (empty = safe)."""
    before_model = BusinessModel.model_validate(before)
    risks: list[BusinessRisk] = []

    before_entities = {entity.key: entity for entity in before_model.entities}
    after_entities = {entity.key: entity for entity in after.entities}
    for key, before_entity in before_entities.items():
        after_entity = after_entities.get(key)
        if after_entity is None:
            risks.append(
                BusinessRisk(ENTITY_REMOVED, f"删除实体「{before_entity.name}」")
            )
            continue

        after_fields = {field.key: field for field in after_entity.fields}
        for field in before_entity.fields:
            if field.key not in after_fields:
                risks.append(
                    BusinessRisk(
                        FIELD_REMOVED,
                        f"删除实体「{before_entity.name}」的字段「{field.name}」",
                    )
                )

        identifier_count = _identifier_count(after_entity)
        if identifier_count > 1 and identifier_count > _identifier_count(
            before_entity
        ):
            names = "、".join(
                f"「{field.name}」"
                for field in after_entity.fields
                if field.role is FieldRole.IDENTIFIER
            )
            risks.append(
                BusinessRisk(
                    IDENTIFIER_CONFLICT,
                    f"实体「{after_entity.name}」出现多个唯一标识字段：{names}",
                )
            )

        before_key_field = next(
            (
                field
                for field in before_entity.fields
                if field.key == before_entity.key_field
            ),
            None,
        )
        after_key_field = after_fields.get(after_entity.key_field)
        if before_entity.key_field != after_entity.key_field:
            risks.append(
                BusinessRisk(
                    IDENTIFIER_CHANGED,
                    f"实体「{after_entity.name}」的唯一标识字段发生变更",
                )
            )
        elif (
            before_key_field is not None
            and after_key_field is not None
            and before_key_field.role is FieldRole.IDENTIFIER
            and after_key_field.role is not FieldRole.IDENTIFIER
        ):
            risks.append(
                BusinessRisk(
                    IDENTIFIER_DEMOTED,
                    f"实体「{after_entity.name}」的唯一标识字段"
                    f"「{after_key_field.name}」被降级，但没有同时指定新的标识字段",
                )
            )
        if (
            before_key_field is not None
            and after_key_field is not None
            and before_key_field.type is not after_key_field.type
        ):
            risks.append(
                BusinessRisk(
                    KEY_FIELD_TYPE_CHANGED,
                    f"实体「{after_entity.name}」的关键字段「{after_key_field.name}」"
                    f"类型由 {before_key_field.type.value} 变为 {after_key_field.type.value}",
                )
            )

    before_metrics = {metric.key: metric for metric in before_model.metrics}
    after_metrics = {metric.key: metric for metric in after.metrics}
    for key, metric in before_metrics.items():
        after_metric = after_metrics.get(key)
        if after_metric is None:
            risks.append(BusinessRisk(METRIC_REMOVED, f"删除指标「{metric.name}」"))
        elif after_metric.business_definition != metric.business_definition:
            risks.append(
                BusinessRisk(
                    METRIC_DEFINITION_CHANGED,
                    f"指标「{metric.name}」的业务口径被改写",
                )
            )

    before_links = {
        (link.from_, link.to, link.on.from_, link.on.to)
        for link in before_model.links
    }
    after_links = {
        (link.from_, link.to, link.on.from_, link.on.to) for link in after.links
    }
    for from_entity, to_entity, _on_from, _on_to in sorted(before_links - after_links):
        risks.append(
            BusinessRisk(LINK_REMOVED, f"删除实体关联 {from_entity} → {to_entity}")
        )

    return risks


__all__ = [
    "BusinessRisk",
    "BusinessSafetyError",
    "detect_business_risks",
]
