"""Typed row validation and row-level mutations for running apps.

Rows are stored one record per business row, so create/update/delete touch
only the affected row instead of replacing a whole app document. Validation
still runs against the current Business Model before anything is written.

The demo app is served from checked-in seed rows. Its first read copies those
rows into the record store; after that the demo is an ordinary editable app
until its volume is reset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.domain.models import BusinessModel, Entity
from backend.runtime import seed
from backend.runtime.value_validation import check_field_value
from backend.storage.models import ModelVersionRecord
from backend.storage.record_store import RecordStore, get_record_store
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


def _materialize_seed(
    db: Session, store: RecordStore, version: ModelVersionRecord, model: BusinessModel
) -> None:
    """Persist the bundled demo rows the first time the demo app is read.

    Every read path (bootstrap, list query, dashboard) needs rows in the
    record store, so seeding happens here instead of on the first edit.
    """
    if version.app_key != seed.DEMO_APP_KEY:
        return
    if store.count(version.app_key) > 0:
        return
    seed_rows = seed.load_demo_records(model)
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for entity in model.entities:
        for row in seed_rows.get(entity.key, []):
            rows.append((entity.key, _row_key(row, entity), row))
    store.replace_app(version.app_key, rows, version.version)


def _model_for_reading(
    db: Session, version: ModelVersionRecord
) -> tuple[BusinessModel, RecordStore]:
    try:
        model = BusinessModel.model_validate(version.snapshot)
    except ValueError as exc:
        # Malformed persisted model is a semantics failure, not a 404.
        raise RecordError(str(exc)) from exc
    store = get_record_store(db)
    _materialize_seed(db, store, version, model)
    return model, store


def load_current_model(
    db: Session, app_key: str
) -> tuple[ModelVersionRecord, BusinessModel]:
    """Resolve an app's current model without loading every business row."""
    version = repo.get_current(db, app_key, raise_if_missing=False)
    if version is None:
        if app_key != seed.DEMO_APP_KEY:
            raise RecordNotFound(f"应用 {app_key!r} 不存在")
        version = seed.ensure_demo_model(db)
    model, _ = _model_for_reading(db, version)
    return version, model


def load_pinned_model(
    db: Session, app_key: str, model_version: int
) -> tuple[ModelVersionRecord, BusinessModel]:
    """Resolve a published model version without loading every business row."""
    version = repo.get_version(db, app_key, model_version)
    model, _ = _model_for_reading(db, version)
    return version, model


def _runtime_data_from_version(
    db: Session, version: ModelVersionRecord
) -> RuntimeData:
    """Build the full render payload for one model version.

    Records are read live; only the model is version-specific.
    """
    model, store = _model_for_reading(db, version)
    stored = store.load(version.app_key)
    normalized = {
        entity.key: list(stored.get(entity.key, []) or [])
        for entity in model.entities
    }
    return RuntimeData(record=version, model=model, records=normalized)


def load_runtime_data(db: Session, app_key: str) -> RuntimeData:
    """Resolve the current model and every row of an app."""
    version, _ = load_current_model(db, app_key)
    return _runtime_data_from_version(db, version)


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


def create_row(
    db: Session, app_key: str, entity_key: str, raw: Any
) -> tuple[str, dict[str, Any]]:
    record, model = load_current_model(db, app_key)
    entity = get_entity(model, entity_key)
    row = validate_row(entity, raw)
    key = _row_key(row, entity)
    store = get_record_store(db)
    if store.key_exists(app_key, entity.key, key):
        raise RecordConflict(f"{entity.name}标识已存在：{key}")
    try:
        store.insert_row(app_key, entity.key, key, row, record.version, commit=False)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RecordConflict(f"{entity.name}标识已存在：{key}") from exc
    except Exception:
        db.rollback()
        raise
    return key, row


def update_row(
    db: Session, app_key: str, entity_key: str, key: str, raw: Any
) -> dict[str, Any]:
    record, model = load_current_model(db, app_key)
    entity = get_entity(model, entity_key)
    row = validate_row(entity, raw)
    if _row_key(row, entity) != key:
        raise RecordError("记录标识不能被修改")
    store = get_record_store(db)
    try:
        updated = store.update_row(
            app_key, entity.key, key, row, record.version, commit=False
        )
        if not updated:
            db.rollback()
            raise RecordNotFound(f"记录不存在：{key}")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return row


def delete_row(db: Session, app_key: str, entity_key: str, key: str) -> None:
    _, model = load_current_model(db, app_key)
    entity = get_entity(model, entity_key)
    store = get_record_store(db)
    try:
        removed = store.delete_row(app_key, entity.key, key, commit=False)
        if not removed:
            db.rollback()
            raise RecordNotFound(f"记录不存在：{key}")
        db.commit()
    except Exception:
        db.rollback()
        raise
