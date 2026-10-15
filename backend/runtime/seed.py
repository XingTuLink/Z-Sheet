"""Demo seed data for the first renderer milestone.

The upload/parse path does not exist yet, so proving the technical chain
"model + data -> deterministic UI" needs bundled seed data
(design doc timeline 10-09: "demo model renders directly").

Seeding is lazy and idempotent: the first runtime bootstrap request imports
the demo model as version 1 when the app is absent, and record rows are read
from the seed YAML on every call. This module is replaced by parser-driven
persistence when the data runtime lands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from backend.domain.models import BusinessModel, Entity
from backend.runtime.value_validation import check_field_value
from backend.storage.models import ModelVersionRecord
from backend.storage.repositories import model_repository as repo

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
DEMO_APP_KEY = "default"
DEMO_MODEL_FILE = "sales_management.yaml"
DEMO_RECORDS_FILE = "sales_management.records.yaml"


def read_demo_model_yaml() -> str:
    return (EXAMPLES_DIR / DEMO_MODEL_FILE).read_text(encoding="utf-8")


def ensure_demo_model(db: Session) -> ModelVersionRecord:
    """Import the demo model as v1 if the app does not exist yet."""
    record = repo.get_current(db, DEMO_APP_KEY, raise_if_missing=False)
    if record is not None:
        return record
    return repo.import_model(
        db, DEMO_APP_KEY, read_demo_model_yaml(), operator="demo-seed"
    )


def _validate_entity_rows(
    entity: Entity, rows: Any
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError(f"seed records for entity {entity.key!r} must be a list")
    fields = {f.key: f for f in entity.fields}
    clean: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        where = f"records[{entity.key}][{index}]"
        if not isinstance(row, dict):
            raise ValueError(f"{where} must be a mapping")
        key_value = row.get(entity.key_field)
        if key_value is None or key_value == "":
            raise ValueError(f"{where}: key field {entity.key_field!r} is required")
        for field_key, value in row.items():
            field = fields.get(field_key)
            if field is None:
                raise ValueError(
                    f"{where}: unknown field {field_key!r} for entity {entity.key!r}"
                )
            if value is not None:
                check_field_value(field, value, where)
        clean.append(row)
    return clean


def validate_records(
    model: BusinessModel, raw: Any
) -> dict[str, list[dict[str, Any]]]:
    """Check seed rows against the Business Model (shape, keys and types)."""
    if not isinstance(raw, dict):
        raise ValueError("seed records file must contain a mapping at the top level")
    entities = {e.key: e for e in model.entities}
    validated: dict[str, list[dict[str, Any]]] = {}
    for entity_key, rows in raw.items():
        entity = entities.get(entity_key)
        if entity is None:
            raise ValueError(f"seed records reference unknown entity {entity_key!r}")
        validated[entity_key] = _validate_entity_rows(entity, rows)
    return validated


def load_demo_records(model: BusinessModel) -> dict[str, list[dict[str, Any]]]:
    text = (EXAMPLES_DIR / DEMO_RECORDS_FILE).read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    return validate_records(model, raw)
