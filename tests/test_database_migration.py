"""SQLite -> PostgreSQL copy tool: preservation, refusal and revision checks."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select

from backend.storage.db import Base
from backend.storage.migrate import MigrationError, copy_database

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _migrate(url: str, revision: str = "head") -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, revision)


def _seed(url: str) -> None:
    tables = Base.metadata.tables
    now = datetime(2026, 10, 22, 8, 30, tzinfo=UTC)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            tables["model_versions"].insert().values(
                id=3,
                app_key="demo",
                version=2,
                snapshot={"entities": [{"key": "customer", "key_field": "name"}]},
                patch=[{"op": "replace", "path": "/title", "value": "客户"}],
                operator="本地用户",
                source_request="把标题改成客户",
                validation_status="passed",
                created_at=now,
            )
        )
        conn.execute(
            tables["business_records"].insert().values(
                id=7,
                app_key="demo",
                entity_key="customer",
                row_key="张三",
                data={
                    "name": "张三",
                    "amount": 1200.5,
                    "tags": ["vip", "new"],
                    "note": "含换行\n与中文",
                },
                model_version=2,
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            tables["app_data"].insert().values(
                app_key="legacy",
                records={"customer": [{"name": "李四"}]},
                updated_at=now,
            )
        )
        conn.execute(
            tables["users"].insert().values(
                id=5,
                email="owner@example.com",
                password_hash="scrypt$dummy",
                display_name="所有者",
                created_at=now,
            )
        )
        conn.execute(
            tables["audit_logs"].insert().values(
                id=9,
                app_key="demo",
                user_id=5,
                actor="owner@example.com",
                action="record.update",
                summary="更新客户张三",
                detail={"before": {"name": "李四"}, "after": {"name": "张三"}},
                created_at=now,
            )
        )
    engine.dispose()


def test_copy_preserves_rows_ids_and_json(tmp_path: Path) -> None:
    source_path = tmp_path / "source.db"
    target_path = tmp_path / "target.db"
    source_url, target_url = _url(source_path), _url(target_path)
    _migrate(source_url)
    _migrate(target_url)
    _seed(source_url)

    report = copy_database(source_url, target_url)

    assert report.revision == "0009"
    counts = report.table_counts
    assert counts["model_versions"] == 1
    assert counts["business_records"] == 1
    assert counts["users"] == 1
    assert counts["audit_logs"] == 1
    assert counts["app_data"] == 1

    engine = create_engine(target_url)
    with engine.connect() as conn:
        record = conn.execute(
            select(Base.metadata.tables["business_records"])
        ).mappings().one()
        assert record["id"] == 7
        assert record["row_key"] == "张三"
        assert record["data"]["tags"] == ["vip", "new"]
        assert record["data"]["note"] == "含换行\n与中文"
        assert record["data"]["amount"] == 1200.5

        version = conn.execute(
            select(Base.metadata.tables["model_versions"])
        ).mappings().one()
        assert version["id"] == 3
        assert version["patch"] == [{"op": "replace", "path": "/title", "value": "客户"}]

        audit = conn.execute(select(Base.metadata.tables["audit_logs"])).mappings().one()
        assert audit["detail"]["after"] == {"name": "张三"}
    engine.dispose()


def test_copy_refuses_non_empty_target_without_replace(tmp_path: Path) -> None:
    source_url, target_url = _url(tmp_path / "source.db"), _url(tmp_path / "target.db")
    _migrate(source_url)
    _migrate(target_url)
    _seed(source_url)
    _seed(target_url)

    with pytest.raises(MigrationError, match="目标库已有数据"):
        copy_database(source_url, target_url)

    report = copy_database(source_url, target_url, replace=True)
    assert report.table_counts["business_records"] == 1


def test_copy_rejects_revision_mismatch(tmp_path: Path) -> None:
    source_url, target_url = _url(tmp_path / "source.db"), _url(tmp_path / "target.db")
    _migrate(source_url)
    _migrate(target_url, "0008")

    with pytest.raises(MigrationError, match="结构版本不一致"):
        copy_database(source_url, target_url)
