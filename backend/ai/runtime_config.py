"""Resolve the LLM configuration that is effective right now.

An active profile saved in the database wins; with none, the ``ZSHEET_LLM_*``
environment settings apply, so instances installed before profiles existed keep
working unchanged. Saving or switching a profile therefore takes effect on the
next call — no restart.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.ai.provider import LLMConfig
from backend.config import get_settings
from backend.logging_config import get_logger
from backend.storage.models import LlmProfileRecord
from backend.storage.repositories import llm_repository

logger = get_logger("llm.config")

SOURCE_DATABASE = "database"
SOURCE_ENV = "env"


def config_from_profile(profile: LlmProfileRecord) -> LLMConfig:
    return LLMConfig(
        base_url=profile.base_url,
        api_key=profile.api_key,
        model=profile.model,
        timeout=profile.timeout,
        enable_thinking=profile.enable_thinking,
        max_retries=profile.max_retries,
    )


def resolve_llm_config(db: Session) -> LLMConfig:
    """Effective config for a request that already holds a session."""
    active = llm_repository.get_active(db)
    if active is not None:
        return config_from_profile(active)
    return get_settings().llm_config


def resolve_llm_config_safely() -> LLMConfig:
    """Effective config for callers without a session (understanding, startup).

    A missing/unmigrated database must never prevent generation: any lookup
    failure simply falls back to the environment settings.
    """
    from backend.storage.db import SessionLocal

    try:
        with SessionLocal() as db:
            return resolve_llm_config(db)
    except Exception:
        logger.warning("读取模型配置失败，使用 .env 配置", exc_info=True)
        return get_settings().llm_config


__all__ = [
    "SOURCE_DATABASE",
    "SOURCE_ENV",
    "config_from_profile",
    "resolve_llm_config",
    "resolve_llm_config_safely",
]
