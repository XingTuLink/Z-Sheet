"""Enumerations for the Z-Sheet Business Model (schema version 0.1).

Frozen on Foundation Day 1; additions require a schema migration decision.
"""

from __future__ import annotations

from enum import StrEnum


class FieldType(StrEnum):
    """Physical field types supported in V0.1 (design doc 11.2)."""

    STRING = "string"
    NUMBER = "number"
    MONEY = "money"
    DATE = "date"
    ENUM = "enum"
    PHONE = "phone"


class FieldRole(StrEnum):
    """Semantic business role of a field (design doc, semantic role stage)."""

    IDENTIFIER = "identifier"
    DIMENSION = "dimension"
    MEASURE = "measure"
    TIME = "time"
    ENUM = "enum"
    TEXT = "text"


class LinkType(StrEnum):
    """Relation cardinality supported in V0.1 (design doc 11.3)."""

    ONE_TO_MANY = "one_to_many"


class ViewKind(StrEnum):
    """Deterministic renderer view kinds (design doc 28.5)."""

    LIST = "list"
    DETAIL = "detail"
    FORM = "form"
    DASHBOARD = "dashboard"


class DetailBlockType(StrEnum):
    FIELDS = "fields"
    RELATED_LIST = "related_list"


class MetricOp(StrEnum):
    """Aggregations allowed on a measure in V0.1."""

    SUM = "sum"
    AVG = "avg"
    COUNT = "count"
    MIN = "min"
    MAX = "max"


class SortDir(StrEnum):
    ASC = "asc"
    DESC = "desc"
