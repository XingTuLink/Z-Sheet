"""Server-side list queries, detail rows, related lists and dashboard data."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import UniqueConstraint

from backend.api.main import app
from backend.domain.models import BusinessModel
from backend.runtime import query
from backend.storage.db import SessionLocal
from backend.storage.models import BusinessRecord
from backend.storage.record_store import get_record_store

client = TestClient(app)
SALES_YAML = (
    Path(__file__).resolve().parents[1] / "examples" / "sales_management.yaml"
).read_text(encoding="utf-8")


def _import(app_key: str) -> None:
    response = client.post(
        "/api/v1/models/import",
        json={"app_key": app_key, "yaml_content": SALES_YAML},
    )
    assert response.status_code == 201, response.text


def _store_rows(app_key: str, entity: str, rows: list[dict[str, Any]]) -> None:
    with SessionLocal() as db:
        store = get_record_store(db)
        for row in rows:
            key_field = "customer_name" if entity == "customer" else "order_no"
            store.insert_row(
                app_key, entity, str(row[key_field]), row, 1, commit=False
            )
        db.commit()


def _seed_workspace() -> None:
    _import("workspace")
    _store_rows(
        "workspace",
        "customer",
        [
            {"customer_name": "客户A", "region": "华东", "owner": "张三"},
            {"customer_name": "客户B", "region": "华南", "owner": "李四"},
            {"customer_name": "客户C", "region": "", "owner": "张三"},
            {"customer_name": "客户D", "region": "华东", "owner": None},
        ],
    )
    _store_rows(
        "workspace",
        "order",
        [
            {
                "order_no": "SO-1",
                "customer_name": "客户A",
                "amount": 100,
                "order_date": "2026-01-03",
            },
            {
                "order_no": "SO-2",
                "customer_name": "客户A",
                "amount": 20,
                "order_date": "2026-02-11",
            },
            {
                "order_no": "SO-3",
                "customer_name": "客户B",
                "amount": 300,
                "order_date": "2026-07-01",
            },
            {
                "order_no": "SO-4",
                "customer_name": "客户B",
                "amount": None,
                "order_date": "2026-08-05",
            },
        ],
    )


def _list(entity: str, **params: Any) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/runtime/workspace/entities/{entity}/rows", params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_bootstrap_no_longer_ships_business_rows() -> None:
    _seed_workspace()
    body = client.get("/api/v1/runtime/workspace/bootstrap").json()
    assert "records" not in body
    assert body["version"] == 1


def test_demo_dashboard_uses_seeded_rows() -> None:
    body = client.get("/api/v1/runtime/default/dashboard")
    assert body.status_code == 200, body.text
    payload = body.json()
    assert payload["metrics"]["customer_count"] == 5
    assert payload["metrics"]["order_count"] == 8
    assert payload["recent"] is not None
    assert payload["trend"]["entity"] == "order"
    assert payload["distribution"] is not None


def test_row_identity_is_indexed_for_lookup() -> None:
    # The unique constraint also serves as the (app_key, entity_key, row_key)
    # index behind paged lookups, so no redundant index is created.
    table = BusinessRecord.metadata.tables[BusinessRecord.__tablename__]
    identity = next(
        constraint
        for constraint in table.constraints
        if constraint.name == "uq_business_records_identity"
    )
    assert isinstance(identity, UniqueConstraint)
    assert [column.name for column in identity.columns] == [
        "app_key",
        "entity_key",
        "row_key",
    ]


def test_list_paginates_and_reports_totals() -> None:
    _seed_workspace()
    first = _list("customer", page=1, page_size=2)
    assert first["total"] == 4
    assert first["entity_total"] == 4
    assert first["page"] == 1
    assert first["page_count"] == 2
    assert [row["customer_name"] for row in first["rows"]] == ["客户A", "客户B"]

    second = _list("customer", page=2, page_size=2)
    assert [row["customer_name"] for row in second["rows"]] == ["客户C", "客户D"]

    # A page past the end clamps to the last page instead of returning nothing.
    clamped = _list("customer", page=99, page_size=2)
    assert clamped["page"] == 2
    assert len(clamped["rows"]) == 2


def test_list_sorts_numerically_and_keeps_blanks_last() -> None:
    _seed_workspace()
    ascending = _list(
        "order", sorts=json.dumps([{"field": "amount", "dir": "asc"}])
    )
    assert [row["amount"] for row in ascending["rows"]] == [20, 100, 300, None]

    descending = _list(
        "order", sorts=json.dumps([{"field": "amount", "dir": "desc"}])
    )
    assert [row["amount"] for row in descending["rows"]] == [300, 100, 20, None]


def test_list_search_filters_and_facets() -> None:
    _seed_workspace()
    searched = _list("customer", search="客户C", search_fields="customer_name")
    assert searched["total"] == 1
    assert searched["rows"][0]["customer_name"] == "客户C"

    filtered = _list("customer", filters=json.dumps({"region": ["华东"]}))
    assert filtered["total"] == 2
    assert filtered["entity_total"] == 4

    blanks = _list("customer", filters=json.dumps({"region": ["__empty__"]}))
    assert [row["customer_name"] for row in blanks["rows"]] == ["客户C"]

    facets = _list("customer", facets="region")["facets"]["region"]
    counts = {item["value"]: item["count"] for item in facets}
    assert counts == {"华东": 2, "__empty__": 1, "华南": 1}


def test_list_rejects_unknown_query_fields() -> None:
    _seed_workspace()
    response = client.get(
        "/api/v1/runtime/workspace/entities/customer/rows",
        params={"sorts": json.dumps([{"field": "ghost", "dir": "asc"}])},
    )
    assert response.status_code == 422
    assert "ghost" in response.json()["detail"]

    bad_filter = client.get(
        "/api/v1/runtime/workspace/entities/customer/rows",
        params={"filters": json.dumps({"ghost": ["x"]})},
    )
    assert bad_filter.status_code == 422


def test_single_row_and_missing_row() -> None:
    _seed_workspace()
    found = client.get("/api/v1/runtime/workspace/entities/customer/rows/客户A")
    assert found.status_code == 200
    assert found.json()["row"]["owner"] == "张三"

    missing = client.get("/api/v1/runtime/workspace/entities/customer/rows/不存在")
    assert missing.status_code == 404


def test_related_rows_follow_the_link() -> None:
    _seed_workspace()
    response = client.get(
        "/api/v1/runtime/workspace/entities/order/related",
        params={
            "parent_entity": "customer",
            "link": "customer_orders",
            "parent": "客户A",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert [row["order_no"] for row in body["rows"]] == ["SO-2", "SO-1"]

    limited = client.get(
        "/api/v1/runtime/workspace/entities/order/related",
        params={
            "parent_entity": "customer",
            "link": "customer_orders",
            "parent": "客户A",
            "limit": 1,
        },
    ).json()
    assert limited["total"] == 2
    assert len(limited["rows"]) == 1


def test_related_rows_support_self_link() -> None:
    model = BusinessModel.model_validate(
        {
            "version": "0.1",
            "app": {"name": "组织"},
            "entities": [
                {
                    "key": "employee",
                    "name": "员工",
                    "key_field": "employee_id",
                    "fields": [
                        {
                            "key": "employee_id",
                            "name": "工号",
                            "type": "string",
                            "role": "identifier",
                            "confidence": 1.0,
                            "needs_review": False,
                        },
                        {
                            "key": "manager_id",
                            "name": "上级工号",
                            "type": "string",
                            "role": "dimension",
                            "confidence": 1.0,
                            "needs_review": False,
                        },
                    ],
                }
            ],
            "links": [
                {
                    "key": "employee_manager",
                    "from": "employee",
                    "to": "employee",
                    "type": "one_to_one",
                    "on": {"from": "employee_id", "to": "manager_id"},
                    "confidence": 1.0,
                    "needs_review": False,
                }
            ],
        }
    )
    rows = [
        {"employee_id": "E1", "manager_id": ""},
        {"employee_id": "E2", "manager_id": "E1"},
        {"employee_id": "E3", "manager_id": "E1"},
        {"employee_id": "E4", "manager_id": "E2"},
    ]
    with SessionLocal() as db:
        store = get_record_store(db)
        for row in rows:
            store.insert_row(
                "self_link_app", "employee", row["employee_id"], row, 1, commit=False
            )
        db.commit()
        body = query.related_rows(
            db,
            model,
            "self_link_app",
            parent_entity="employee",
            link_key="employee_manager",
            parent_row_key="E1",
        )
    assert body["total"] == 2
    assert {row["employee_id"] for row in body["rows"]} == {"E2", "E3"}


def test_dashboard_aggregates_live_rows() -> None:
    _seed_workspace()
    body = client.get("/api/v1/runtime/workspace/dashboard").json()
    assert body["metrics"] == {
        "customer_count": 4,
        "order_count": 4,
        "total_amount": 420,
    }

    recent = body["recent"]
    assert recent["entity"] == "order"
    assert recent["detail_view"] is None
    assert [row["order_no"] for row in recent["rows"]] == [
        "SO-4",
        "SO-3",
        "SO-2",
        "SO-1",
    ]

    trend = body["trend"]
    assert trend["entity"] == "order"
    assert trend["measure_field"] == "amount"
    assert [point["label"] for point in trend["points"]] == [
        "2026-01",
        "2026-02",
        "2026-07",
        "2026-08",
    ]

    distribution = body["distribution"]
    assert distribution["entity"] == "customer"
    assert distribution["field"] == "region"
    assert distribution["total"] == 3
    assert distribution["items"][0] == {"label": "华东", "count": 2}


def test_ten_thousand_rows_page_fast_and_small() -> None:
    _import("bulk")
    _store_rows(
        "bulk",
        "order",
        [
            {
                "order_no": f"SO-{index:05d}",
                "customer_name": f"客户{index % 50}",
                "amount": index,
                "order_date": "2026-03-01",
            }
            for index in range(10_000)
        ],
    )
    started = time.perf_counter()
    body = client.get(
        "/api/v1/runtime/bulk/entities/order/rows",
        params={
            "page": 500,
            "page_size": 50,
            "sorts": json.dumps([{"field": "amount", "dir": "desc"}]),
        },
    )
    elapsed = time.perf_counter() - started
    assert body.status_code == 200, body.text
    payload = body.json()
    assert payload["total"] == 10_000
    assert payload["page"] == 200
    assert len(payload["rows"]) == 50
    # Descending amounts: the last page holds 49 ... 0, exclusive of the top.
    assert payload["rows"][0]["amount"] == 49
    assert len(body.content) < 60_000
    assert elapsed < 2.0

    # Bootstrap carries the model only, so 10k rows no longer inflate it.
    bootstrap = client.get("/api/v1/runtime/bulk/bootstrap")
    assert bootstrap.status_code == 200
    assert "records" not in bootstrap.json()
    assert len(bootstrap.content) < 100_000
