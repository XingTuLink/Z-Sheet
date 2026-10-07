"""SQLite engine, session factory and FastAPI dependency (SQLAlchemy 2.0).

No business tables exist yet on Day 2; table creation arrives with the model
storage work on Day 3.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for future ORM models."""


def make_engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )


settings = get_settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
engine: Engine = make_engine(settings.database_url)
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
