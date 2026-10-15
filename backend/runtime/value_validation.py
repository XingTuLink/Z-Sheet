"""Shared six-type value validation for seed, Confirm and runtime CRUD."""

from __future__ import annotations

from datetime import date
from typing import Any

from backend.domain.models import BusinessField, FieldType


class RecordValueError(ValueError):
    """A row value does not match its declared field type."""


def check_field_value(field: BusinessField, value: Any, where: str) -> None:
    """Validate one non-null cell using the six supported field types."""
    field_type = field.type
    if field_type in (FieldType.STRING, FieldType.PHONE):
        if not isinstance(value, str):
            raise RecordValueError(f"{where}: field {field.key!r} expects a string")
    elif field_type is FieldType.DATE:
        if not isinstance(value, str):
            raise RecordValueError(f"{where}: field {field.key!r} expects YYYY-MM-DD string")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise RecordValueError(
                f"{where}: field {field.key!r} has invalid ISO date {value!r}"
            ) from exc
    elif field_type is FieldType.ENUM:
        if not isinstance(value, str) or value not in (field.values or []):
            allowed = ", ".join(field.values or [])
            raise RecordValueError(
                f"{where}: field {field.key!r} value {value!r} is not one of: {allowed}"
            )
    else:  # number / money: bool is a subclass of int and must be rejected.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RecordValueError(f"{where}: field {field.key!r} expects a number")
