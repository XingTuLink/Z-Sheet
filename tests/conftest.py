"""Pytest bootstrap.

Point ZSHEET_DATA_DIR at a throwaway temp dir BEFORE any backend import, so
engine/session construction never touches the developer's local ./data.

Each test runs against a database migrated through the real Alembic scripts
(upgrade head -> downgrade base), which also exercises the migrations.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

os.environ.setdefault("ZSHEET_DATA_DIR", tempfile.mkdtemp(prefix="zsheet-test-"))

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _migrated_database():
    cfg = Config()  # no ini file: keeps Alembic's fileConfig quiet in tests
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    yield
    command.downgrade(cfg, "base")
