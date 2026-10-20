"""Storage-agnostic access to confirmed business rows.

Callers only see the :class:`RecordStore` interface; the current SQL
implementation stores one row per business row. Replacing the database (or
the storage strategy) means adding another implementation here, not touching
the runtime, Confirm or the row CRUD service.

The interface is deliberately model-agnostic: callers resolve ``row_key`` from
the entity's key field, so the store never reads Business Models.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Float, case, cast, delete, false, func, or_, select
from sqlalchemy.orm import Session

from backend.storage.models import BusinessRecord

Row = tuple[str, str, dict[str, Any]]
"""``(entity_key, row_key, data)`` for one row."""

EMPTY_FILTER_VALUE = "__empty__"
"""Filter value that selects blank cells (null or empty string)."""


@dataclass(frozen=True)
class FieldSort:
    """One declared sort; ``value_type`` comes from the model field type."""

    field: str
    direction: str = "asc"
    value_type: str = "string"


@dataclass(frozen=True)
class RowQuery:
    """Server-side list query: filter/search/sort then one page of rows."""

    offset: int = 0
    limit: int = 10
    search: str = ""
    search_fields: tuple[str, ...] = ()
    filters: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    sorts: tuple[FieldSort, ...] = ()


@dataclass(frozen=True)
class RowPage:
    rows: list[dict[str, Any]]
    total: int


NUMERIC_TYPES = frozenset({"number", "money"})


def _value_expression(field_key: str) -> Any:
    """Text view of one JSON cell (json_extract on SQLite)."""
    return BusinessRecord.data[field_key].as_string()


def _blank_expression(expression: Any) -> Any:
    return or_(expression.is_(None), expression == "")


def _search_conditions(query: RowQuery) -> list[Any]:
    tokens = [token for token in query.search.lower().split() if token]
    if not tokens or not query.search_fields:
        return []
    expressions = [_value_expression(key) for key in query.search_fields]
    return [
        or_(
            *[
                func.lower(expression).contains(token, autoescape=True)
                for expression in expressions
            ]
        )
        for token in tokens
    ]


def _filter_conditions(query: RowQuery) -> list[Any]:
    conditions = _search_conditions(query)
    for field_key, selected in query.filters.items():
        if not selected:
            continue
        expression = _value_expression(field_key)
        options: list[Any] = []
        literals = [value for value in selected if value != EMPTY_FILTER_VALUE]
        if literals:
            options.append(expression.in_(literals))
        if EMPTY_FILTER_VALUE in selected:
            options.append(_blank_expression(expression))
        conditions.append(or_(*options) if options else false())
    return conditions


def _order_terms(sorts: tuple[FieldSort, ...]) -> list[Any]:
    """Typed ORDER BY with blanks last and the row id as a stable tiebreak."""
    terms: list[Any] = []
    for sort in sorts:
        expression = _value_expression(sort.field)
        blank = _blank_expression(expression)
        terms.append(case((blank, 1), else_=0))
        ordering = (
            cast(expression, Float)
            if sort.value_type in NUMERIC_TYPES
            else expression
        )
        terms.append(
            ordering.desc().nulls_last()
            if sort.direction == "desc"
            else ordering.asc().nulls_last()
        )
    terms.append(BusinessRecord.id.asc())
    return terms


def unique_row_key(base: str, taken: set[str]) -> str:
    """Return a row key that is not in ``taken`` (``k``, ``k#2``, ``k#3``...).

    Workbook rows may legitimately repeat a business key (Confirm flags such
    duplicates for review but does not block generation), while storage
    identity must stay unique. The first occurrence keeps the plain key so
    URLs stay natural; later duplicates get a deterministic suffix.
    """
    if base not in taken:
        return base
    suffix = 2
    while f"{base}#{suffix}" in taken:
        suffix += 1
    return f"{base}#{suffix}"


class RecordStore(ABC):
    """Row storage contract bound to one session/transaction."""

    @abstractmethod
    def load(self, app_key: str) -> dict[str, list[dict[str, Any]]]:
        """All rows of an app grouped by entity, in insertion order."""

    @abstractmethod
    def key_exists(self, app_key: str, entity_key: str, row_key: str) -> bool:
        """Whether that identity is already taken."""

    @abstractmethod
    def insert_row(
        self,
        app_key: str,
        entity_key: str,
        row_key: str,
        data: dict[str, Any],
        model_version: int,
        *,
        commit: bool = True,
    ) -> None:
        """Insert one row; a duplicate identity raises ``IntegrityError``."""

    @abstractmethod
    def update_row(
        self,
        app_key: str,
        entity_key: str,
        row_key: str,
        data: dict[str, Any],
        model_version: int,
        *,
        commit: bool = True,
    ) -> bool:
        """Replace one row's data; False when it does not exist."""

    @abstractmethod
    def delete_row(
        self,
        app_key: str,
        entity_key: str,
        row_key: str,
        *,
        commit: bool = True,
    ) -> bool:
        """Remove one row; False when it does not exist."""

    @abstractmethod
    def replace_app(
        self,
        app_key: str,
        rows: Iterable[Row],
        model_version: int,
        *,
        commit: bool = True,
    ) -> int:
        """Replace every row of an app with ``rows`` (Confirm / seed clone)."""

    @abstractmethod
    def get(
        self, app_key: str, entity_key: str, row_key: str
    ) -> dict[str, Any] | None:
        """One row's data, or None when that identity does not exist."""

    @abstractmethod
    def count(self, app_key: str, entity_key: str | None = None) -> int:
        """Number of rows in an app, optionally limited to one entity."""

    @abstractmethod
    def query(self, app_key: str, entity_key: str, query: RowQuery) -> RowPage:
        """Filter/search/sort one entity and return a single page."""

    @abstractmethod
    def facets(
        self, app_key: str, entity_key: str, fields: Sequence[str]
    ) -> dict[str, list[tuple[str, int]]]:
        """Value counts per field over every row of the entity."""


class SqlRecordStore(RecordStore):
    """SQLAlchemy implementation (SQLite today, PostgreSQL later)."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def load(self, app_key: str) -> dict[str, list[dict[str, Any]]]:
        records: dict[str, list[dict[str, Any]]] = {}
        rows: Sequence[BusinessRecord] = self._db.scalars(
            select(BusinessRecord)
            .where(BusinessRecord.app_key == app_key)
            .order_by(BusinessRecord.id.asc())
        ).all()
        for row in rows:
            records.setdefault(row.entity_key, []).append(row.data)
        return records

    def key_exists(self, app_key: str, entity_key: str, row_key: str) -> bool:
        return (
            self._db.scalar(
                select(BusinessRecord.id).where(
                    BusinessRecord.app_key == app_key,
                    BusinessRecord.entity_key == entity_key,
                    BusinessRecord.row_key == row_key,
                )
            )
            is not None
        )

    def insert_row(
        self,
        app_key: str,
        entity_key: str,
        row_key: str,
        data: dict[str, Any],
        model_version: int,
        *,
        commit: bool = True,
    ) -> None:
        self._db.add(
            BusinessRecord(
                app_key=app_key,
                entity_key=entity_key,
                row_key=row_key,
                data=data,
                model_version=model_version,
            )
        )
        self._finish(commit)

    def update_row(
        self,
        app_key: str,
        entity_key: str,
        row_key: str,
        data: dict[str, Any],
        model_version: int,
        *,
        commit: bool = True,
    ) -> bool:
        record = self._find(app_key, entity_key, row_key)
        if record is None:
            return False
        record.data = data
        record.model_version = model_version
        record.updated_at = datetime.now(UTC)
        self._finish(commit)
        return True

    def delete_row(
        self,
        app_key: str,
        entity_key: str,
        row_key: str,
        *,
        commit: bool = True,
    ) -> bool:
        record = self._find(app_key, entity_key, row_key)
        if record is None:
            return False
        self._db.delete(record)
        self._finish(commit)
        return True

    def replace_app(
        self,
        app_key: str,
        rows: Iterable[Row],
        model_version: int,
        *,
        commit: bool = True,
    ) -> int:
        self._db.execute(
            delete(BusinessRecord).where(BusinessRecord.app_key == app_key)
        )
        taken: dict[str, set[str]] = {}
        count = 0
        for entity_key, row_key, data in rows:
            keys = taken.setdefault(entity_key, set())
            stored_key = unique_row_key(row_key, keys)
            keys.add(stored_key)
            self._db.add(
                BusinessRecord(
                    app_key=app_key,
                    entity_key=entity_key,
                    row_key=stored_key,
                    data=data,
                    model_version=model_version,
                )
            )
            count += 1
        self._finish(commit)
        return count

    def get(
        self, app_key: str, entity_key: str, row_key: str
    ) -> dict[str, Any] | None:
        record = self._find(app_key, entity_key, row_key)
        return record.data if record is not None else None

    def count(self, app_key: str, entity_key: str | None = None) -> int:
        conditions = [BusinessRecord.app_key == app_key]
        if entity_key is not None:
            conditions.append(BusinessRecord.entity_key == entity_key)
        return int(
            self._db.scalar(
                select(func.count(BusinessRecord.id)).where(*conditions)
            )
            or 0
        )

    def query(self, app_key: str, entity_key: str, query: RowQuery) -> RowPage:
        conditions = [
            BusinessRecord.app_key == app_key,
            BusinessRecord.entity_key == entity_key,
            *_filter_conditions(query),
        ]
        total = int(
            self._db.scalar(
                select(func.count(BusinessRecord.id)).where(*conditions)
            )
            or 0
        )
        statement = (
            select(BusinessRecord.data)
            .where(*conditions)
            .order_by(*_order_terms(query.sorts))
            .offset(query.offset)
            .limit(query.limit)
        )
        rows = list(self._db.scalars(statement).all())
        return RowPage(rows=rows, total=total)

    def facets(
        self, app_key: str, entity_key: str, fields: Sequence[str]
    ) -> dict[str, list[tuple[str, int]]]:
        result: dict[str, list[tuple[str, int]]] = {}
        for field_key in fields:
            expression = _value_expression(field_key)
            counts: dict[str, int] = {}
            grouped = self._db.execute(
                select(expression, func.count(BusinessRecord.id))
                .where(
                    BusinessRecord.app_key == app_key,
                    BusinessRecord.entity_key == entity_key,
                )
                .group_by(expression)
            ).all()
            for raw, count in grouped:
                value = EMPTY_FILTER_VALUE if raw in (None, "") else str(raw)
                counts[value] = counts.get(value, 0) + int(count)
            result[field_key] = list(counts.items())
        return result

    def _find(
        self, app_key: str, entity_key: str, row_key: str
    ) -> BusinessRecord | None:
        return self._db.scalar(
            select(BusinessRecord)
            .where(
                BusinessRecord.app_key == app_key,
                BusinessRecord.entity_key == entity_key,
                BusinessRecord.row_key == row_key,
            )
            .order_by(BusinessRecord.id.asc())
            .limit(1)
        )

    def _finish(self, commit: bool) -> None:
        if commit:
            self._db.commit()
        else:
            self._db.flush()


def get_record_store(db: Session) -> RecordStore:
    """Resolve the configured record store for this session."""
    return SqlRecordStore(db)


__all__ = [
    "EMPTY_FILTER_VALUE",
    "FieldSort",
    "RecordStore",
    "Row",
    "RowPage",
    "RowQuery",
    "SqlRecordStore",
    "get_record_store",
    "unique_row_key",
]
