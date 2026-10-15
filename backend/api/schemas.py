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


class RollbackRequest(BaseModel):
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


class RuntimeBootstrapResponse(BaseModel):
    """Everything the deterministic renderer needs in one round trip."""

    app_key: str
    version: int
    model: dict[str, Any]
    records: dict[str, list[dict[str, Any]]]


class ConfirmResponse(BaseModel):
    """Day 15 Confirm result: the generated app key and what was persisted."""

    app_key: str
    version: int
    app_name: str
    links_accepted: list[str]
    links_rejected: list[str]
    record_counts: dict[str, int]


class PatchPreviewRequest(BaseModel):
    """One NL modification sentence against the current model version."""

    text: str = Field(min_length=1, max_length=500)
    operator: str = "local"


class PatchChangeOut(BaseModel):
    kind: str
    risk: str
    summary: str


class PatchUnsupportedOut(BaseModel):
    category: str
    reason: str
    suggestion: str


class PatchPreviewResponse(BaseModel):
    session_id: str
    base_version: int
    actionable: bool
    requires_confirmation: bool
    changes: list[PatchChangeOut]
    unsupported: list[PatchUnsupportedOut]
    notes: list[str]


class PatchDecisionResponse(BaseModel):
    status: str


class RecordPayload(BaseModel):
    """One business row submitted by the generated Form view."""

    row: dict[str, Any]


class RecordMutationResponse(BaseModel):
    app_key: str
    entity: str
    key: str
    row: dict[str, Any]


class PublicationRequest(BaseModel):
    """Create a publication: chosen public slug."""

    slug: str = Field(..., min_length=1, max_length=64)


class PublicationUpdateRequest(BaseModel):
    """Change the slug, pin the newest model version, or both at once."""

    slug: str | None = Field(default=None, min_length=1, max_length=64)
    republish: bool = False


class PublicationResponse(BaseModel):
    app_key: str
    slug: str
    status: str
    model_version: int
    current_version: int
    public: bool
    published_at: datetime | None = None
