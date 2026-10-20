"""Storage layering: one record per business row and the legacy migration.

Covers the three acceptance points that are not already exercised by the
existing runtime/Confirm suites:

* rows survive the store round-trip and duplicates keep every row under a
  deterministic suffixed key;
* updating one row touches only that row (ids and row count stay stable);
* the 0006 backfill copies legacy documents losslessly and can be re-run.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.api.main import app
from backend.storage.db import SessionLocal, engine
from backend.storage.models import BusinessRecord
from backend.storage.record_store import get_record_store

client = TestClient(app)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_0006 = PROJECT_ROOT / "migrations" / "versions" / "0006_business_records.py"


def _alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return cfg


def _customer(name: str, **overrides: object) -> dict[str, object]:
    return {
        "customer_name": name,
        "region": "华东",
        "owner": "测试负责人",
        "phone": "13800000000",
        **overrides,
    }


def _stored_rows(app_key: str) -> list[BusinessRecord]:
    db = SessionLocal()
    try:
        return list(
            db.scalars(
                select(BusinessRecord)
                .where(BusinessRecord.app_key == app_key)
                .order_by(BusinessRecord.id.asc())
            ).all()
        )
    finally:
        db.close()


def test_store_roundtrip_keeps_duplicate_business_keys() -> None:
    db = SessionLocal()
    try:
        store = get_record_store(db)
        count = store.replace_app(
            "acme",
            [
                ("customer", "甲公司", {"customer_name": "甲公司"}),
                ("customer", "甲公司", {"customer_name": "甲公司"}),
                ("customer", "乙公司", {"customer_name": "乙公司"}),
            ],
            3,
        )
        assert count == 3
        assert [row["customer_name"] for row in store.load("acme")["customer"]] == [
            "甲公司",
            "甲公司",
            "乙公司",
        ]
        assert [r.row_key for r in _stored_rows("acme")] == ["甲公司", "甲公司#2", "乙公司"]
    finally:
        db.close()


def test_update_row_does_not_rewrite_other_rows() -> None:
    db = SessionLocal()
    try:
        store = get_record_store(db)
        store.replace_app(
            "acme",
            [
                ("customer", "A", {"customer_name": "A", "region": "华东"}),
                ("customer", "B", {"customer_name": "B", "region": "华东"}),
            ],
            1,
        )
        before = {row.row_key: row.id for row in _stored_rows("acme")}

        assert store.update_row(
            "acme", "customer", "A", {"customer_name": "A", "region": "华南"}, 2
        )
        after_rows = _stored_rows("acme")
        after = {row.row_key: row.id for row in after_rows}

        assert after == before
        assert store.load("acme")["customer"] == [
            {"customer_name": "A", "region": "华南"},
            {"customer_name": "B", "region": "华东"},
        ]
        untouched = next(row for row in after_rows if row.row_key == "B")
        assert untouched.model_version == 1
    finally:
        db.close()


def test_api_update_keeps_row_identity() -> None:
    assert (
        client.post(
            "/api/v1/runtime/default/entities/customer/records",
            json={"row": _customer("单行客户A")},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/runtime/default/entities/customer/records",
            json={"row": _customer("单行客户B")},
        ).status_code
        == 201
    )
    before = {
        row.row_key: row.id
        for row in _stored_rows("default")
        if row.row_key in {"单行客户A", "单行客户B"}
    }
    count_before = len(_stored_rows("default"))

    response = client.put(
        "/api/v1/runtime/default/entities/customer/records/单行客户A",
        json={"row": _customer("单行客户A", owner="新负责人")},
    )
    assert response.status_code == 200

    after_rows = _stored_rows("default")
    assert len(after_rows) == count_before
    after = {
        row.row_key: row.id
        for row in after_rows
        if row.row_key in {"单行客户A", "单行客户B"}
    }
    assert after == before
    assert next(row for row in after_rows if row.row_key == "单行客户A").data["owner"] == (
        "新负责人"
    )


def _insert_legacy_document(document: dict[str, Any]) -> None:
    metadata = sa.MetaData()
    versions = sa.Table("model_versions", metadata, autoload_with=engine)
    app_data = sa.Table("app_data", metadata, autoload_with=engine)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            versions.insert().values(
                app_key="legacy",
                version=1,
                snapshot={
                    "app": {"key": "legacy", "name": "旧系统"},
                    "entities": [
                        {
                            "key": "customer",
                            "name": "客户",
                            "key_field": "customer_name",
                            "fields": [],
                        }
                    ],
                    "metrics": [],
                    "views": [],
                },
                patch=None,
                operator="legacy",
                source_request=None,
                validation_status="passed",
                created_at=now,
            )
        )
        conn.execute(
            app_data.insert().values(
                app_key="legacy", records=document, updated_at=now
            )
        )


def test_legacy_documents_migrate_losslessly_and_idempotently() -> None:
    cfg = _alembic_config()
    command.downgrade(cfg, "0005")

    document = {
        "customer": [
            {"customer_name": "甲公司", "region": "华东"},
            {"customer_name": "甲公司", "region": "华北"},
            {"customer_name": "乙公司", "region": "华南"},
        ]
    }
    _insert_legacy_document(document)

    command.upgrade(cfg, "head")

    migrated = _stored_rows("legacy")
    assert [row.row_key for row in migrated] == ["甲公司", "甲公司#2", "乙公司"]
    assert [row.data for row in migrated] == document["customer"]
    assert {row.entity_key for row in migrated} == {"customer"}
    assert {row.model_version for row in migrated} == {1}

    spec = importlib.util.spec_from_file_location(
        "migration_0006_business_records", MIGRATION_0006
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    with engine.begin() as conn:
        inserted = migration.backfill_legacy_documents(conn)

    assert inserted == 0
    assert len(_stored_rows("legacy")) == 3
