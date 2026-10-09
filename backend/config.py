"""Application settings, overridable via ZSHEET_* environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.ai.provider import LLMConfig

PROJECT_VERSION = "0.1.0"
DEFAULT_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

# Experiment-phase default: DeepSeek public cloud. Point these at an Ollama /
# vLLM endpoint (e.g. http://localhost:11434/v1, model qwen2.5:7b) to run fully
# local; leave the key empty there because local engines need no auth.
DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-flash"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZSHEET_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    # JSON array or comma-separated string via env; defaults target the Vite dev server.
    cors_origins: list[str] = DEFAULT_DEV_ORIGINS

    # OpenAI-compatible LLM used by the understanding layer (and later NL Patch).
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_api_key: str = ""
    llm_model: str = DEFAULT_LLM_MODEL
    llm_timeout: float = 60.0
    llm_enable_thinking: bool = True

    @property
    def database_url(self) -> str:
        # as_posix keeps the SQLite URL valid on Windows (no backslashes).
        return f"sqlite:///{(self.data_dir / 'zsheet.db').as_posix()}"

    @property
    def llm_config(self) -> LLMConfig:
        return LLMConfig(
            base_url=self.llm_base_url,
            api_key=self.llm_api_key,
            model=self.llm_model,
            timeout=self.llm_timeout,
            enable_thinking=self.llm_enable_thinking,
        )


def get_settings() -> Settings:
    return Settings()
