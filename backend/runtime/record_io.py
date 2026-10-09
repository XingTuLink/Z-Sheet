"""Typed row validation and row-level mutations for running apps.

Day 15 persisted an entire records document on Confirm. Day 18 keeps that
simple SQLite JSON storage for V0.1 but adds the service layer required by
the rendered Form/Detail/List views: validate one row against the current
Business Model, then create/update/delete it and replace the document
transactionally.

The demo app is normally served from checked-in seed rows. Its first write
clones those rows into ``app_data``; after that the demo is an ordinary
editable app until its volume is reset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from backend.domain.models import BusinessModel, Entity
from backend.runtime import seed
from backend.runtime.value_validation import check_field_value
from backend.storage.models import ModelVersionRecord
from backend.storage.repositories import app_data_repository
from backend.storage.repositories import model_repository as repo


class RecordError(ValueError):
    """Base class for row mutation failures mapped to 4xx responses."""


class RecordNotFound(RecordError):
    """The app, entity or row does not exist (404)."""


class RecordConflict(RecordError):
    """A different row already owns the supplied business key (409)."""


@dataclass(frozen=True)
class RuntimeData:
    record: ModelVersionRecord
    model: BusinessModel
    records: dict[str, list[dict[str, Any]]]
    """Whether records came from mutable app_data (False while seeded demo)."""


def load_runtime_data(db: Session, app_key: str) -> RuntimeData:
    """Resolve the current model and effective row document for an app."""
    try:
        version = repo.get_current(db, app_key, raise_if_missing=False)
        if version is None:
            if app_key != seed.DEMO_APP_KEY:
                raise RecordNotFound(f"应用 {app_key!r} 不存在")
            version = seed.ensure_demo_model(db)
        model = BusinessModel.model_validate(version.snapshot)
        stored = app_data_repository.get_records(db, app_key)
        if stored is not None:
            records = stored
        elif app_key == seed.DEMO_APP_KEY:
            records = seed.load_demo_records(model)
        else:
            records = {}
    except ValueError as exc:
        # validate_app_key / malformed persisted model errors are request
        # shape/semantics failures, not a missing resource.
        if isinstance(exc, RecordNotFound):
            raise
        raise RecordError(str(exc)) from exc

    normalized = {
        entity.key: list(records.get(entity.key, []) or [])
        for entity in model.entities
    }
    return RuntimeData(record=version, model=model, records=normalized)


def get_entity(model: BusinessModel, entity_key: str) -> Entity:
    entity = next((item for item in model.entities if item.key == entity_key), None)
    if entity is None:
        raise RecordNotFound(f"实体 {entity_key!r} 不存在")
    return entity


def validate_row(entity: Entity, raw: Any) -> dict[str, Any]:
    """Validate a form row and normalize it to every declared field key."""
    if not isinstance(raw, dict):
        raise RecordError("提交内容必须是一条记录对象")
    fields = {field.key: field for field in entity.fields}
    unknown = sorted(set(raw) - set(fields))
    if unknown:
        raise RecordError(f"包含实体中不存在的字段：{', '.join(unknown)}")

    clean: dict[str, Any] = {field.key: raw.get(field.key) for field in entity.fields}
    key_value = clean.get(entity.key_field)
    if key_value is None or key_value == "":
        raise RecordError(f"{entity.name}标识不能为空")

    for field in entity.fields:
        value = clean[field.key]
        if value is not None:
            try:
                check_field_value(field, value, f"{entity.name}记录")
            except ValueError as exc:
                raise RecordError(str(exc)) from exc
    return clean


def _row_key(row: dict[str, Any], entity: Entity) -> str:
    return str(row[entity.key_field])


def _find_row(
    rows: list[dict[str, Any]], entity: Entity, key: str
) -> tuple[int, dict[str, Any]] | None:
    for index, row in enumerate(rows):
        if _row_key(row, entity) == key:
            return index, row
    return None


def create_row(
    db: Session, app_key: str, entity_key: str, raw: Any
) -> tuple[str, dict[str, Any]]:
    data = load_runtime_data(db, app_key)
    entity = get_entity(data.model, entity_key)
    row = validate_row(entity, raw)
    rows = data.records.setdefault(entity.key, [])
    key = _row_key(row, entity)
    if _find_row(rows, entity, key) is not None:
        raise RecordConflict(f"{entity.name}标识已存在：{key}")
    rows.append(row)
    app_data_repository.upsert_records(db, app_key, data.records)
    return key, row


def update_row(
    db: Session, app_key: str, entity_key: str, key: str, raw: Any
) -> dict[str, Any]:
    data = load_runtime_data(db, app_key)
    entity = get_entity(data.model, entity_key)
    row = validate_row(entity, raw)
    if _row_key(row, entity) != key:
        raise RecordError("记录标识不能被修改")
    rows = data.records.setdefault(entity.key, [])
    located = _find_row(rows, entity, key)
    if located is None:
        raise RecordNotFound(f"记录不存在：{key}")
    rows[located[0]] = row
    app_data_repository.upsert_records(db, app_key, data.records)
    return row


def delete_row(db: Session, app_key: str, entity_key: str, key: str) -> None:
    data = load_runtime_data(db, app_key)
    entity = get_entity(data.model, entity_key)
    rows = data.records.setdefault(entity.key, [])
    located = _find_row(rows, entity, key)
    if located is None:
        raise RecordNotFound(f"记录不存在：{key}")
    rows.pop(located[0])
    app_data_repository.upsert_records(db, app_key, data.records)
