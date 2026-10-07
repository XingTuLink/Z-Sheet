"""API request/response schemas for model versioning."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_APP_KEY = "default"


class ModelImportRequest(BaseModel):
    app_key: str = DEFAULT_APP_KEY
    yaml_content: str = Field(min_length=1)
    operator: str = "local"


class PatchSubmitRequest(BaseModel):
    ops: list[dict[str, Any]] = Field(min_length=1)
    source_request: str | None = None
    operator: str = "local"


class VersionMeta(BaseModel):
    id: int
    app_key: str
    version: int
    operator: str
    source_request: str | None
    validation_status: str
    has_patch: bool
    created_at: datetime


class VersionDetail(VersionMeta):
    snapshot: dict[str, Any]
    patch: list[dict[str, Any]] | None
