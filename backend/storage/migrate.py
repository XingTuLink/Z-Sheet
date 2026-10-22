"""Copy every Z-Sheet table from one database into another (SQLite -> PostgreSQL).

The copy path is deliberately dialect-neutral: the source schema is reflected,
rows are read and written through SQLAlchemy Core, so JSON documents and
timestamps keep their Python values. The target must already be migrated to the
same Alembic revision; this is verified before anything is written. Afterwards
the PostgreSQL identity sequences are re-synchronized, because rows are copied
with their original ids and a stale sequence would collide on the next insert.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Integer,
    Table,
    create_engine,
    delete,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Connection

from backend.storage import models  # noqa: F401  (registers the tables)
from backend.storage.db import Base


class MigrationError(RuntimeError):
    """Copy refused or incomplete; the message is user-facing Chinese."""


@dataclass(frozen=True)
class CopyReport:
    """Outcome of one database copy, per table."""

    revision: str
    table_counts: Mapping[str, int]


def _tables() -> list[Table]:
    return list(Base.metadata.sorted_tables)


def _current_revision(conn: Connection) -> str:
    if not inspect(conn).has_table("alembic_version"):
        return ""
    value = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    return str(value or "")


def _count(conn: Connection, table: Table) -> int:
    return int(conn.scalar(select(func.count()).select_from(table)) or 0)


def _normalize(value: Any) -> Any:
    """Canonical form for row comparison (naive datetimes are treated as UTC)."""
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _row_identity(table: Table, row: Mapping[Any, Any]) -> tuple[Any, ...]:
    return tuple(_normalize(row[column.name]) for column in table.primary_key.columns)


def _table_rows(conn: Connection, table: Table) -> dict[tuple[Any, ...], dict[str, Any]]:
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in conn.execute(select(table)).mappings():
        data = {key: _normalize(value) for key, value in row.items()}
        rows[_row_identity(table, row)] = data
    return rows


def _copy_table(source: Connection, target: Connection, table: Table, batch: int = 500) -> int:
    copied = 0
    result = source.execute(select(table))
    while True:
        chunk = result.fetchmany(batch)
        if not chunk:
            break
        target.execute(table.insert(), [dict(row._mapping) for row in chunk])
        copied += len(chunk)
    return copied


def _clear_target(conn: Connection, tables: Iterable[Table], replace: bool) -> None:
    counts = {table.name: _count(conn, table) for table in tables}
    non_empty = {name: count for name, count in counts.items() if count}
    if not non_empty:
        return
    if not replace:
        detail = "、".join(f"{name}={count}" for name, count in non_empty.items())
        raise MigrationError(
            "目标库已有数据，已停止写入（未做任何修改）。"
            f"现有行数：{detail}。确认要覆盖时请加 --replace。"
        )
    if conn.dialect.name == "postgresql":
        names = ", ".join(f'"{table.name}"' for table in tables)
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    else:
        # Reverse order keeps child tables first if FKs are added later.
        for table in reversed(list(tables)):
            conn.execute(delete(table))


def _sync_sequences(conn: Connection, tables: Iterable[Table]) -> None:
    """Advance PostgreSQL identity sequences past the copied ids."""
    if conn.dialect.name != "postgresql":
        return
    for table in tables:
        column = table.c.get("id")
        if column is None or not isinstance(column.type, Integer) or not column.primary_key:
            continue
        maximum = conn.scalar(select(func.max(column)))
        conn.exec_driver_sql(
            "SELECT setval(pg_get_serial_sequence(%s, 'id'), %s, %s)",
            (table.name, maximum or 1, maximum is not None),
        )


def _verify(source: Connection, target: Connection, tables: Iterable[Table]) -> None:
    for table in tables:
        expected = _table_rows(source, table)
        actual = _table_rows(target, table)
        if expected.keys() != actual.keys():
            missing = len(expected.keys() - actual.keys())
            extra = len(actual.keys() - expected.keys())
            raise MigrationError(
                f"表 {table.name} 主键集合不一致：缺少 {missing} 行，多出 {extra} 行。"
            )
        mismatched = [key for key, row in expected.items() if actual[key] != row]
        if mismatched:
            raise MigrationError(
                f"表 {table.name} 有 {len(mismatched)} 行内容不一致，"
                f"首个不一致主键：{mismatched[0]}。"
            )


def copy_database(source_url: str, target_url: str, *, replace: bool = False) -> CopyReport:
    """Copy all tables from ``source_url`` into ``target_url`` and verify them.

    ``replace`` is the explicit consent to wipe a non-empty target (including
    its identity sequences). Source and target must sit on the same Alembic
    revision, so the schema is never copied across incompatible versions.
    """
    if source_url == target_url:
        raise MigrationError("源库与目标库是同一个地址，无需迁移。")

    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url)
    tables = _tables()
    try:
        with source_engine.connect() as source, target_engine.begin() as target:
            source_revision = _current_revision(source)
            if not source_revision:
                raise MigrationError("源库缺少 alembic_version，无法确认结构版本。")
            target_revision = _current_revision(target)
            if target_revision != source_revision:
                raise MigrationError(
                    f"结构版本不一致：源库 {source_revision}，"
                    f"目标库 {target_revision or '未迁移'}。"
                    "请先对目标库执行 alembic upgrade head。"
                )

            missing = [
                table.name
                for table in tables
                if not inspect(source).has_table(table.name)
                or not inspect(target).has_table(table.name)
            ]
            if missing:
                raise MigrationError(f"表不存在：{'、'.join(missing)}。")

            _clear_target(target, tables, replace)
            for table in tables:
                _copy_table(source, target, table)
            _sync_sequences(target, tables)
            _verify(source, target, tables)

            counts = {table.name: _count(target, table) for table in tables}
            return CopyReport(revision=source_revision, table_counts=counts)
    finally:
        source_engine.dispose()
        target_engine.dispose()


__all__ = ["CopyReport", "MigrationError", "copy_database"]
