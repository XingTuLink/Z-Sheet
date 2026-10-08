"""Deterministic one-to-many link inference (Day 11, design 11.3).

Evidence order: same field name, value-set overlap, uniqueness, distribution,
field semantics. V0.1 supports one-to-many only; if both sides are unique the
candidate is rejected.

Every produced link is forced into review: spreadsheets carry no foreign-key
definition, so even a high-confidence link (design 7.1 uses 0.88) must be
confirmed by a human (design 10.1 explicitly allows forcing review).
"""

from __future__ import annotations

from dataclasses import dataclass

from .keywords import (
    CUSTOMER_SHEET_KEYWORDS,
    IDENTIFIER_NAME_KEYWORDS,
    PRODUCT_SHEET_KEYWORDS,
    has_keyword,
)
from .model_assembly import EntityPlan
from .schemas import InferredLink, ProfiledParsedWorkbook

# Overlap = share of the parent key values that also appear on the child side.
STRONG_RECALL = 0.80
MEDIUM_RECALL = 0.50
HIGH_RECALL_NAMELESS = 0.90
HIGH_PRECISION_NAMELESS = 0.50

# Foreign-key columns are identifier-style; generic same-name columns such as
# 备注/状态 must never create name-only links.
_FK_NAME_KEYWORDS = IDENTIFIER_NAME_KEYWORDS
_PARTY_SHEET_KEYWORDS = CUSTOMER_SHEET_KEYWORDS + PRODUCT_SHEET_KEYWORDS


@dataclass(frozen=True)
class _Candidate:
    child_column: str
    child_field_key: str
    confidence: float
    recall: float
    precision: float
    signals: tuple[str, ...]
    reason: str


def _column_values(plan: EntityPlan, column_name: str) -> list[str]:
    pos = plan.sheet.columns.index(column_name)
    values: list[str] = []
    for row in plan.sheet.rows:
        if pos >= len(row):
            continue
        value = row[pos]
        if value is not None and value.strip():
            values.append(value.strip())
    return values


def _score_candidate(
    parent: EntityPlan,
    child: EntityPlan,
    child_column: str,
    child_field_key: str,
) -> _Candidate | None:
    if child_column == child.key_field_column:
        return None  # a child's own primary key cannot be its foreign key

    parent_values = _column_values(parent, parent.key_field_column)
    if not parent_values:
        return None
    parent_set = set(parent_values)
    child_raw = _column_values(child, child_column)
    if not child_raw:
        return None
    child_set = set(child_raw)

    # "Many" side must repeat; a unique child column is not one-to-many.
    if len(child_set) >= len(child_raw):
        return None

    overlap = parent_set & child_set
    recall = len(overlap) / len(parent_set)
    precision = len(overlap) / len(child_set)
    same_name = (
        parent.key_field_column.strip() == child_column.strip()
    )

    if same_name and recall >= STRONG_RECALL:
        return _Candidate(
            child_column, child_field_key, 0.88, recall, precision,
            ("same_field_name", "value_set_overlap", "parent_unique"),
            "同名字段值集合高度重叠，但源数据没有外键定义",
        )
    if same_name and recall >= MEDIUM_RECALL:
        return _Candidate(
            child_column, child_field_key, 0.75, recall, precision,
            ("same_field_name", "partial_value_overlap"),
            "同名字段值仅部分重叠，外键关系需人工确认",
        )
    if (
        not same_name
        and recall >= HIGH_RECALL_NAMELESS
        and precision >= HIGH_PRECISION_NAMELESS
        and has_keyword(child_column, _PARTY_SHEET_KEYWORDS)
    ):
        return _Candidate(
            child_column, child_field_key, 0.75, recall, precision,
            ("value_set_overlap", "semantic_column_name"),
            "字段名不同但值集合高度重叠，疑似外键，需人工确认",
        )
    if (
        same_name
        and recall < MEDIUM_RECALL
        and has_keyword(child_column, _FK_NAME_KEYWORDS)
    ):
        return _Candidate(
            child_column, child_field_key, 0.63, recall, precision,
            ("same_field_name", "value_set_not_verified"),
            "列名一致但值集合未通过重叠验证（设计 10.2，禁止假确定）",
        )
    return None


def infer_links(
    workbook: ProfiledParsedWorkbook, plans: list[EntityPlan]
) -> list[InferredLink]:
    """Scan every parent/child pair; keep at most the best link per pair."""
    links: list[InferredLink] = []
    for parent in plans:
        for child in plans:
            if parent.key == child.key:
                continue
            best: _Candidate | None = None
            for child_column, child_field_key in child.field_keys.items():
                candidate = _score_candidate(
                    parent, child, child_column, child_field_key
                )
                if candidate is None:
                    continue
                if best is None or (
                    candidate.confidence,
                    candidate.recall,
                ) > (best.confidence, best.recall):
                    best = candidate
            if best is None:
                continue
            links.append(
                InferredLink(
                    key=f"{parent.key}_to_{child.key}",
                    from_entity=parent.key,
                    to_entity=child.key,
                    on_from=parent.key_field_key,
                    on_to=best.child_field_key,
                    confidence=best.confidence,
                    # Every V0.1 link is review-gated: no FK exists in source.
                    needs_review=True,
                    review_reason=best.reason,
                    signals=list(best.signals),
                    overlap_recall=round(best.recall, 4),
                    overlap_precision=round(best.precision, 4),
                )
            )
    return links
