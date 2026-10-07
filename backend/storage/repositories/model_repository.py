"""Persistence service for Business Models and their version history.

Implements design doc 6.1 / 9.3 / 28.7:

- import creates v1 (no patch);
- every validated change appends a new immutable version row;
- a change that fails JSON Patch application or Pydantic re-validation is
  rejected and never written.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal, overload

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.models import BusinessModel
from backend.domain.serialization import model_from_yaml
from backend.patch.apply import PatchError, apply_patch
from backend.patch.json_patch import PatchDocument
from backend.storage.models import ModelVersionRecord

APP_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class AppNotFoundError(Exception):
    def __init__(self, app_key: str) -> None:
        super().__init__(f"app not found: {app_key}")
        self.app_key = app_key


class AppExistsError(Exception):
    def __init__(self, app_key: str) -> None:
        super().__init__(f"app already exists: {app_key}")
        self.app_key = app_key


def validate_app_key(app_key: str) -> str:
    if not APP_KEY_PATTERN.match(app_key):
        raise ValueError(
            "app_key must be 1-64 chars: lowercase letters, digits, '_' or '-', "
            "starting with a letter or digit"
        )
    return app_key


def snapshot_to_model(record: ModelVersionRecord) -> BusinessModel:
    return BusinessModel.model_validate(record.snapshot)


def import_model(
    db: Session,
    app_key: str,
    yaml_text: str,
    *,
    operator: str = "local",
) -> ModelVersionRecord:
    """Validate YAML and create version 1 for a new app."""
    validate_app_key(app_key)
    try:
        model = model_from_yaml(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc.__class__.__name__}") from exc
    # Semantic problems with the model itself raise pydantic.ValidationError.

    if get_current(db, app_key, raise_if_missing=False) is not None:
        raise AppExistsError(app_key)

    record = ModelVersionRecord(
        app_key=app_key,
        version=1,
        snapshot=model.model_dump(mode="json"),
        patch=None,
        operator=operator,
        source_request=None,
        validation_status="passed",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@overload
def get_current(db: Session, app_key: str) -> ModelVersionRecord: ...


@overload
def get_current(
    db: Session, app_key: str, *, raise_if_missing: Literal[True]
) -> ModelVersionRecord: ...


@overload
def get_current(
    db: Session, app_key: str, *, raise_if_missing: Literal[False]
) -> ModelVersionRecord | None: ...


def get_current(
    db: Session, app_key: str, *, raise_if_missing: bool = True
) -> ModelVersionRecord | None:
    validate_app_key(app_key)
    record = db.scalar(
        select(ModelVersionRecord)
        .where(ModelVersionRecord.app_key == app_key)
        .order_by(ModelVersionRecord.version.desc())
        .limit(1)
    )
    if record is None and raise_if_missing:
        raise AppNotFoundError(app_key)
    return record


def list_versions(db: Session, app_key: str) -> Sequence[ModelVersionRecord]:
    get_current(db, app_key)  # 404 early for unknown apps
    return db.scalars(
        select(ModelVersionRecord)
        .where(ModelVersionRecord.app_key == app_key)
        .order_by(ModelVersionRecord.version.asc())
    ).all()


def get_version(db: Session, app_key: str, version: int) -> ModelVersionRecord:
    record = db.scalar(
        select(ModelVersionRecord).where(
            ModelVersionRecord.app_key == app_key,
            ModelVersionRecord.version == version,
        )
    )
    if record is None:
        raise AppNotFoundError(f"{app_key}@v{version}")
    return record


def apply_patch_as_new_version(
    db: Session,
    app_key: str,
    raw_ops: list[dict[str, Any]],
    *,
    source_request: str | None = None,
    operator: str = "local",
) -> ModelVersionRecord:
    """Validate + apply a raw JSON Patch and append a new version atomically."""
    validate_app_key(app_key)
    current = get_current(db, app_key)

    # Layer 1: patch shape (RFC 6902 subset).
    document = PatchDocument.from_raw(raw_ops)
    # Layer 2: structural application against the current snapshot.
    new_snapshot = apply_patch(current.snapshot, document.ops)
    # Layer 3: full Business Model semantics (raises ValidationError on failure).
    model = BusinessModel.model_validate(new_snapshot)

    record = ModelVersionRecord(
        app_key=app_key,
        version=current.version + 1,
        snapshot=model.model_dump(mode="json"),
        patch=raw_ops,
        operator=operator,
        source_request=source_request,
        validation_status="passed",
    )
    try:
        db.add(record)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(record)
    return record


__all__ = [
    "AppExistsError",
    "AppNotFoundError",
    "PatchError",
    "apply_patch_as_new_version",
    "get_current",
    "get_version",
    "import_model",
    "list_versions",
    "snapshot_to_model",
    "validate_app_key",
]
