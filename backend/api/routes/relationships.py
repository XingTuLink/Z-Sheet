"""Visual Relationship Editor endpoints.

A relationship edit is turned into deterministic JSON Patch ops here, then
cached as a normal patch session. Confirm/reject reuse
``/api/v1/models/{app_key}/patch-sessions/{id}/...`` so the editor gets the
same three-layer validation, version snapshot and CAS protection as a
natural-language change.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from backend.api.schemas import (
    PatchChangeOut,
    PatchPreviewResponse,
)
from backend.auth.permissions import Actor, require_editor
from backend.domain.enums import LinkType
from backend.domain.models import BusinessModel
from backend.patch.apply import PatchError
from backend.patch.intent_parser import IntentParseResult
from backend.patch.validation import validate_patch_snapshot
from backend.relationships import (
    ACTION_ADD,
    ACTION_DELETE,
    ACTION_LABELS,
    ACTION_UPDATE,
    RelationshipError,
    build_relationship_plan,
)
from backend.storage.db import get_db
from backend.storage.repositories import model_repository as repo
from backend.storage.repositories import patch_session_repository as sessions

router = APIRouter(
    prefix="/api/v1/apps/{app_key}/relationships", tags=["relationships"]
)


class RelationshipOn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)


class RelationshipPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    action: Literal["add", "update", "delete"]
    link_key: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    type: LinkType | None = None
    on: RelationshipOn | None = None


def _not_found(app_key: str) -> JSONResponse:
    return JSONResponse(
        status_code=404, content={"detail": f"系统 {app_key!r} 不存在"}
    )


@router.post("/preview", status_code=201, response_model=PatchPreviewResponse)
def preview_relationship(
    app_key: str,
    body: RelationshipPreviewRequest,
    actor: Actor = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Any:
    try:
        current = repo.get_current(db, app_key)
    except repo.AppNotFoundError:
        return _not_found(app_key)
    except ValueError as exc:  # malformed app_key
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    model: BusinessModel = repo.snapshot_to_model(current)
    try:
        plan = build_relationship_plan(
            model,
            action=body.action,
            link_key=body.link_key,
            from_key=body.from_,
            to_key=body.to,
            link_type=body.type,
            on_from=body.on.from_ if body.on else None,
            on_to=body.on.to if body.on else None,
        )
        validate_patch_snapshot(model.model_dump(mode="json"), plan.ops)
    except RelationshipError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    except (PatchError, ValidationError, ValueError) as exc:
        # A plan that fails the shared validator is still legal to report as a
        # rejected edit; it never reaches the model.
        return JSONResponse(
            status_code=422,
            content={"detail": f"该关联调整无法通过模型校验：{exc}"},
        )

    payload = sessions.PatchSessionPayload(
        user_text=plan.summary,
        parse_result=IntentParseResult(user_text=plan.summary),
        ops=plan.ops,
        requires_confirmation=plan.requires_confirmation,
        notes=plan.notes,
        operator=actor.label,
        source="relationship",
    )
    session_id = sessions.create_session(db, app_key, current.version, payload)

    return PatchPreviewResponse(
        session_id=session_id,
        base_version=current.version,
        actionable=bool(plan.ops),
        requires_confirmation=plan.requires_confirmation,
        changes=[
            PatchChangeOut(
                kind=f"relationship_{body.action}",
                risk="high" if plan.requires_confirmation else "low",
                summary=plan.summary,
            )
        ],
        unsupported=[],
        notes=plan.notes,
    )


__all__ = [
    "ACTION_ADD",
    "ACTION_DELETE",
    "ACTION_LABELS",
    "ACTION_UPDATE",
    "RelationshipPreviewRequest",
    "router",
]
