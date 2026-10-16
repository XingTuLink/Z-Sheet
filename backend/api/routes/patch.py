"""NL Patch preview/confirm/reject endpoints (Day 25).

Flow:
  POST   /models/{app}/patch-sessions           parse + generate, cache ops
  POST   /models/{app}/patch-sessions/{id}/confirm   apply cached ops
  POST   /models/{app}/patch-sessions/{id}/reject    discard cached ops

The client only ever sends the sentence, then the opaque session token and a
decision. Patch ops never travel back from the client (anti-tampering, same
pattern as Day 22 Confirm), and confirm refuses when the model moved on between
preview and decision.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from backend.ai.provider import ChatClient, LLMCallError, LLMConfigError
from backend.api.routes.models import _version_detail
from backend.api.schemas import (
    PatchChangeOut,
    PatchDecisionResponse,
    PatchPreviewRequest,
    PatchPreviewResponse,
    PatchUnsupportedOut,
    VersionDetail,
)
from backend.config import get_settings
from backend.logging_config import get_logger
from backend.patch.apply import PatchError
from backend.patch.explain import explain_intents
from backend.patch.generator import PatchGenerationError, generate_patches
from backend.patch.intent_parser import parse_intents
from backend.storage.db import get_db
from backend.storage.repositories import model_repository as repo
from backend.storage.repositories import patch_session_repository as sessions

router = APIRouter(prefix="/api/v1/models/{app_key}/patch-sessions", tags=["patch"])

logger = get_logger("patch")


def get_chat_client() -> ChatClient:
    """Default LLM client; tests override this dependency with a fake."""
    return ChatClient(get_settings().llm_config)


def _not_found(app_key: str) -> JSONResponse:
    return JSONResponse(
        status_code=404, content={"detail": f"app {app_key!r} not found"}
    )


@router.post("", status_code=201, response_model=PatchPreviewResponse)
def preview_patch(
    app_key: str,
    body: PatchPreviewRequest,
    db: Session = Depends(get_db),
    client: ChatClient = Depends(get_chat_client),
) -> Any:
    try:
        current = repo.get_current(db, app_key)
    except repo.AppNotFoundError:
        return _not_found(app_key)
    except ValueError as exc:  # malformed app_key
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    text = body.text.strip()
    if not text:
        return JSONResponse(
            status_code=422, content={"detail": "修改内容不能为空"}
        )

    model = repo.snapshot_to_model(current)
    try:
        result = parse_intents(model, text, client)
        plan = generate_patches(model, result)
    except LLMConfigError as exc:
        logger.warning("Patch 预览被拒绝：LLM 未配置（app=%s）：%s", app_key, exc)
        return JSONResponse(status_code=503, content={"detail": str(exc)})
    except LLMCallError as exc:
        logger.warning(
            "Patch 预览 LLM 调用失败（app=%s，已按重试预算耗尽）：%s", app_key, exc
        )
        return JSONResponse(
            status_code=503,
            content={"detail": f"语义理解服务暂时不可用：{exc}"},
        )
    except ValueError as exc:  # malformed structured output from the LLM
        logger.warning("Patch 预览 LLM 输出无法解析（app=%s）：%s", app_key, exc)
        return JSONResponse(
            status_code=502,
            content={"detail": f"意图解析失败，请重试。{exc}"},
        )
    except PatchGenerationError as exc:
        # Hard safety refusal: the sentence parsed, but it cannot become a
        # legal V0.1 change. Its Chinese message is user-facing guidance.
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    changes = explain_intents(model, result)
    payload = sessions.PatchSessionPayload(
        user_text=result.user_text,
        parse_result=result,
        ops=plan.ops,
        requires_confirmation=plan.requires_confirmation,
        notes=plan.notes,
        operator=body.operator,
    )
    session_id = sessions.create_session(db, app_key, current.version, payload)

    return PatchPreviewResponse(
        session_id=session_id,
        base_version=current.version,
        actionable=bool(plan.ops),
        requires_confirmation=plan.requires_confirmation,
        changes=[PatchChangeOut(**item.model_dump()) for item in changes],
        unsupported=[
            PatchUnsupportedOut(
                category=item.category.value,
                reason=item.reason,
                suggestion=item.suggestion,
            )
            for item in result.unsupported
        ],
        notes=plan.notes,
    )


def _load_pending(
    db: Session, app_key: str, session_id: str
) -> tuple[sessions.PatchSessionRecord | None, JSONResponse | None]:
    record = sessions.get_session(db, session_id, app_key)
    if record is None:
        return None, JSONResponse(
            status_code=404, content={"detail": "修改预览不存在或已失效，请重新生成"}
        )
    if sessions.is_expired(record):
        return None, JSONResponse(
            status_code=409,
            content={"detail": "修改预览已过期（有效期 1 小时），请重新生成"},
        )
    if record.status != sessions.STATUS_PENDING:
        return None, JSONResponse(
            status_code=409,
            content={"detail": "该修改预览已经处理过，请勿重复提交"},
        )
    return record, None


@router.post("/{session_id}/confirm", response_model=VersionDetail)
def confirm_patch(
    app_key: str,
    session_id: str,
    db: Session = Depends(get_db),
) -> Any:
    record, error = _load_pending(db, app_key, session_id)
    if error is not None or record is None:
        return error

    payload = sessions.load_payload(record)
    try:
        current = repo.get_current(db, app_key)
    except repo.AppNotFoundError:
        return _not_found(app_key)

    if current.version != record.base_version:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "系统模型在预览之后已有更新，请基于最新版本重新生成修改预览"
            },
        )
    if not payload.ops:
        return JSONResponse(
            status_code=422, content={"detail": "本次请求没有可应用的修改"}
        )

    # Claim the session atomically (pending -> confirmed, compare-and-swap on
    # status) before touching the model: a second concurrent confirm loses the
    # race and gets 409 instead of applying the same ops twice. The flip stays
    # uncommitted and rides along with apply_patch_as_new_version's commit, so
    # its rollback on failure returns the session to pending.
    if not sessions.begin_confirm(db, record):
        return JSONResponse(
            status_code=409,
            content={"detail": "该修改预览已经处理过，请勿重复提交"},
        )
    try:
        new_record = repo.apply_patch_as_new_version(
            db,
            app_key,
            payload.ops,
            source_request=payload.user_text,
            operator=payload.operator,
        )
    except repo.AppNotFoundError:
        return _not_found(app_key)
    except repo.VersionConflictError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    except (PatchError, PydanticValidationError, ValueError) as exc:
        # The identical ops+snapshot pair passed three-layer validation at
        # preview time, so this path should be unreachable; never surface a
        # raw 500 if it is.
        logger.warning("补丁确认后应用失败（app=%s）：%s", app_key, exc)
        return JSONResponse(
            status_code=422,
            content={"detail": f"补丁应用失败，请重新生成预览：{exc}"},
        )
    logger.info(
        "补丁已应用：app=%s v%d→v%d（session=%s）",
        app_key,
        record.base_version,
        new_record.version,
        session_id,
    )
    return _version_detail(new_record)


@router.post("/{session_id}/reject", response_model=PatchDecisionResponse)
def reject_patch(app_key: str, session_id: str, db: Session = Depends(get_db)) -> Any:
    record, error = _load_pending(db, app_key, session_id)
    if error is not None or record is None:
        return error
    sessions.mark_decided(db, record, sessions.STATUS_REJECTED)
    return PatchDecisionResponse(status=sessions.STATUS_REJECTED)
