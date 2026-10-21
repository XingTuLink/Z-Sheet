"""Model connection settings (global, switchable, no restart).

Profiles are stored in the local database so the instance works without
editing ``.env``. API keys are never returned in full — list/detail responses
carry the same ``mask_key`` form used in logs. With no active profile the
app falls back to ``ZSHEET_LLM_*`` exactly as before.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from backend.ai.provider import (
    LLMConfig,
    LLMProbeResult,
    mask_key,
    probe_llm_connection,
)
from backend.ai.runtime_config import SOURCE_DATABASE, SOURCE_ENV
from backend.config import get_settings
from backend.logging_config import get_logger
from backend.storage.db import get_db
from backend.storage.models import LlmProfileRecord
from backend.storage.repositories import llm_repository

router = APIRouter(prefix="/api/v1/llm", tags=["llm"])

logger = get_logger("llm.settings")

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class LlmProfileOut(BaseModel):
    id: int
    name: str
    base_url: str
    api_key: str
    model: str
    timeout: float
    enable_thinking: bool
    max_retries: int
    is_active: bool
    updated_at: datetime | None = None


class LlmStatus(BaseModel):
    """What the next model call will actually use."""

    source: Literal["database", "env"]
    active_profile_id: int | None = None
    base_url: str
    model: str
    api_key: str
    is_local: bool
    timeout: float
    enable_thinking: bool
    max_retries: int
    available: bool


class LlmProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=255)
    api_key: str = Field(default="", max_length=512)
    model: str = Field(min_length=1, max_length=128)
    timeout: float = Field(default=60.0, ge=1.0, le=600.0)
    enable_thinking: bool = True
    max_retries: int = Field(default=1, ge=0, le=5)
    activate: bool = True

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("接口地址必须是 http:// 或 https:// 开头的完整地址")
        return value.strip().rstrip("/")


class LlmProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: str | None = Field(default=None, min_length=1, max_length=255)
    # None keeps the stored key; "" clears it (local engines need no key).
    api_key: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    timeout: float | None = Field(default=None, ge=1.0, le=600.0)
    enable_thinking: bool | None = None
    max_retries: int | None = Field(default=None, ge=0, le=5)
    activate: bool | None = None

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("接口地址必须是 http:// 或 https:// 开头的完整地址")
        return value.strip().rstrip("/")


class LlmTestInput(BaseModel):
    """Probe a draft (or a saved profile when only ``profile_id`` is sent)."""

    profile_id: int | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    timeout: float = Field(default=15.0, ge=1.0, le=60.0)


class LlmTestResult(BaseModel):
    status: str
    detail: str
    models: list[str] = Field(default_factory=list)


def _host_is_local(base_url: str) -> bool:
    return (urlparse(base_url).hostname or "").lower() in _LOCAL_HOSTS


def _out(record: LlmProfileRecord) -> LlmProfileOut:
    return LlmProfileOut(
        id=record.id,
        name=record.name,
        base_url=record.base_url,
        api_key=mask_key(record.api_key),
        model=record.model,
        timeout=record.timeout,
        enable_thinking=record.enable_thinking,
        max_retries=record.max_retries,
        is_active=record.is_active,
        updated_at=(
            record.updated_at.replace(tzinfo=UTC)
            if record.updated_at is not None and record.updated_at.tzinfo is None
            else record.updated_at
        ),
    )


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _not_found() -> JSONResponse:
    return _error(status.HTTP_404_NOT_FOUND, "模型配置不存在")


def _missing_key(base_url: str) -> str | None:
    if _host_is_local(base_url):
        return None
    return "公有云接口需要填写 API Key"


@router.get("/status", response_model=LlmStatus)
def llm_status(db: Session = Depends(get_db)) -> LlmStatus:
    """Effective configuration, including the .env fallback."""
    active = llm_repository.get_active(db)
    if active is not None:
        config = LLMConfig(
            base_url=active.base_url,
            api_key=active.api_key,
            model=active.model,
            timeout=active.timeout,
            enable_thinking=active.enable_thinking,
            max_retries=active.max_retries,
        )
        return LlmStatus(
            source=SOURCE_DATABASE,
            active_profile_id=active.id,
            base_url=config.base_url,
            model=config.model,
            api_key=mask_key(config.api_key),
            is_local=config.is_local,
            timeout=config.timeout,
            enable_thinking=config.enable_thinking,
            max_retries=config.max_retries,
            available=config.available,
        )
    config = get_settings().llm_config
    return LlmStatus(
        source=SOURCE_ENV,
        base_url=config.base_url,
        model=config.model,
        api_key=mask_key(config.api_key),
        is_local=config.is_local,
        timeout=config.timeout,
        enable_thinking=config.enable_thinking,
        max_retries=config.max_retries,
        available=config.available,
    )


@router.get("/profiles", response_model=list[LlmProfileOut])
def list_profiles(db: Session = Depends(get_db)) -> list[LlmProfileOut]:
    return [_out(record) for record in llm_repository.list_all(db)]


@router.post(
    "/profiles", response_model=LlmProfileOut, status_code=status.HTTP_201_CREATED
)
def create_profile(
    body: LlmProfileInput, db: Session = Depends(get_db)
) -> LlmProfileOut | JSONResponse:
    if llm_repository.find_by_name(db, body.name) is not None:
        return _error(status.HTTP_409_CONFLICT, f"配置名称「{body.name}」已存在")
    missing = _missing_key(body.base_url)
    if missing is not None and not body.api_key:
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, missing)

    record = llm_repository.create(
        db,
        name=body.name,
        base_url=body.base_url,
        api_key=body.api_key,
        model=body.model,
        timeout=body.timeout,
        enable_thinking=body.enable_thinking,
        max_retries=body.max_retries,
        activate=body.activate,
    )
    logger.info(
        "保存模型配置：%s（%s / %s，%s）",
        record.name,
        record.base_url,
        record.model,
        "已启用" if record.is_active else "未启用",
    )
    return _out(record)


@router.patch("/profiles/{profile_id}", response_model=LlmProfileOut)
def update_profile(
    profile_id: int, body: LlmProfileUpdate, db: Session = Depends(get_db)
) -> LlmProfileOut | JSONResponse:
    record = llm_repository.get(db, profile_id)
    if record is None:
        return _not_found()
    if body.name is not None and body.name != record.name:
        other = llm_repository.find_by_name(db, body.name)
        if other is not None and other.id != record.id:
            return _error(status.HTTP_409_CONFLICT, f"配置名称「{body.name}」已存在")

    base_url = body.base_url if body.base_url is not None else record.base_url
    api_key = body.api_key if body.api_key is not None else record.api_key
    missing = _missing_key(base_url)
    if missing is not None and not api_key:
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, missing)

    record = llm_repository.update_profile(
        db,
        record,
        name=body.name,
        base_url=body.base_url,
        api_key=body.api_key,
        model=body.model,
        timeout=body.timeout,
        enable_thinking=body.enable_thinking,
        max_retries=body.max_retries,
    )
    if body.activate:
        record = llm_repository.activate(db, record)
    logger.info(
        "更新模型配置：%s（%s / %s，%s）",
        record.name,
        record.base_url,
        record.model,
        "已启用" if record.is_active else "未启用",
    )
    return _out(record)


@router.post("/profiles/{profile_id}/activate", response_model=LlmProfileOut)
def activate_profile(
    profile_id: int, db: Session = Depends(get_db)
) -> LlmProfileOut | JSONResponse:
    record = llm_repository.get(db, profile_id)
    if record is None:
        return _not_found()
    record = llm_repository.activate(db, record)
    logger.info("启用模型配置：%s（%s / %s）", record.name, record.base_url, record.model)
    return _out(record)


@router.delete("/profiles/{profile_id}", response_model=None)
def delete_profile(
    profile_id: int, db: Session = Depends(get_db)
) -> dict[str, str] | JSONResponse:
    record = llm_repository.get(db, profile_id)
    if record is None:
        return _not_found()
    name = record.name
    llm_repository.delete(db, record)
    logger.info("删除模型配置：%s", name)
    return {"detail": "已删除"}


@router.post("/test", response_model=LlmTestResult)
def test_connection(
    body: LlmTestInput, db: Session = Depends(get_db)
) -> LlmTestResult | JSONResponse:
    """Probe an endpoint without saving; stored fields fill any blanks."""
    stored = (
        llm_repository.get(db, body.profile_id)
        if body.profile_id is not None
        else None
    )
    if body.profile_id is not None and stored is None:
        return _not_found()

    base_url = body.base_url or (stored.base_url if stored else "")
    model = body.model or (stored.model if stored else "")
    api_key = body.api_key if body.api_key is not None else (
        stored.api_key if stored else ""
    )
    if not base_url or not model:
        return _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "请填写接口地址和模型名后再测试"
        )

    config = LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=body.timeout,
    )
    result: LLMProbeResult = probe_llm_connection(config)
    logger.info(
        "模型连接测试：%s（%s / %s）→ %s",
        base_url,
        model,
        mask_key(api_key),
        result.status,
    )
    return LlmTestResult(
        status=result.status,
        detail=result.detail,
        models=list(result.models),
    )


__all__ = ["router"]
