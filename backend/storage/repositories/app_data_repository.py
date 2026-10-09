"""Persistence for confirmed business rows (Day 15).

One ``app_data`` row per app: the records document is replaced wholesale on
every Confirm (re-upload), which matches the V0.1 flow. Per-row CRUD storage
arrives later and will supersede this document model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.storage.models import AppDataRecord
from backend.storage.repositories.model_repository import validate_app_key


def upsert_records(db: Session, app_key: str, records: dict[str, Any]) -> None:
    """Replace the whole records document for an app and commit."""
    validate_app_key(app_key)
    record = db.get(AppDataRecord, app_key)
    if record is None:
        record = AppDataRecord(app_key=app_key, records=records)
        db.add(record)
    else:
        record.records = records
        record.updated_at = datetime.now(UTC)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def get_records(db: Session, app_key: str) -> dict[str, Any] | None:
    """Return the confirmed records document, or None when the app has none."""
    validate_app_key(app_key)
    record = db.get(AppDataRecord, app_key)
    if record is None:
        return None
    return record.records


__all__ = ["get_records", "upsert_records"]
