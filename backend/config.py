"""Application settings, overridable via ZSHEET_* environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_VERSION = "0.1.0"
DEFAULT_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZSHEET_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    # JSON array or comma-separated string via env; defaults target the Vite dev server.
    cors_origins: list[str] = DEFAULT_DEV_ORIGINS

    @property
    def database_url(self) -> str:
        # as_posix keeps the SQLite URL valid on Windows (no backslashes).
        return f"sqlite:///{(self.data_dir / 'zsheet.db').as_posix()}"


def get_settings() -> Settings:
    return Settings()
