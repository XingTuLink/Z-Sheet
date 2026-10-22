"""Database engine, session factory and FastAPI dependency (SQLAlchemy 2.0).

SQLite stays the default (one file under ``data_dir``); setting
``ZSHEET_DATABASE_URL`` to a PostgreSQL URL switches the whole app over
without any other change.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for future ORM models."""


def make_engine(database_url: str) -> Engine:
    options: dict[str, Any] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    return create_engine(database_url, **options)


settings = get_settings()
if settings.is_sqlite:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
engine: Engine = make_engine(settings.sqlalchemy_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def database_alive() -> bool:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True
