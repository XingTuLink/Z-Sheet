"""Tests for the runtime bootstrap endpoint and demo seed validation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.domain.serialization import model_from_yaml
from backend.runtime import seed

client = TestClient(app)


def test_bootstrap_auto_seeds_demo_on_first_call():
    response = client.get(f"/api/v1/runtime/{seed.DEMO_APP_KEY}/bootstrap")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["app_key"] == "default"
    assert body["version"] == 1
    assert body["model"]["app"]["name"] == "销售管理系统"
    # Renderer contract: every declared list/detail/form view is present.
    view_kinds = {v["kind"] for v in body["model"]["views"]}
    assert {"list", "detail", "form"} <= view_kinds

    records = body["records"]
    assert len(records["customer"]) == 5
    assert len(records["order"]) == 8
    first_customer = records["customer"][0]
    assert first_customer["customer_name"]
    assert first_customer["region"] in {"华东", "华南", "华北", "西南"}


def test_bootstrap_is_idempotent():
    first = client.get(f"/api/v1/runtime/{seed.DEMO_APP_KEY}/bootstrap")
    assert first.status_code == 200
    second = client.get(f"/api/v1/runtime/{seed.DEMO_APP_KEY}/bootstrap")
    assert second.status_code == 200
    assert second.json()["version"] == 1

    versions = client.get("/api/v1/models/default/versions").json()
    assert [v["version"] for v in versions] == [1]
    assert versions[0]["operator"] == "demo-seed"


def test_bootstrap_unknown_app_returns_404():
    response = client.get("/api/v1/runtime/never-imported/bootstrap")
    assert response.status_code == 404


def test_bootstrap_malformed_app_key_returns_422():
    response = client.get("/api/v1/runtime/Bad%20Key/bootstrap")
    assert response.status_code == 422


def test_seed_rows_have_declared_types():
    body = client.get(f"/api/v1/runtime/{seed.DEMO_APP_KEY}/bootstrap").json()
    for row in body["records"]["order"]:
        assert isinstance(row["order_no"], str)
        assert isinstance(row["amount"], (int, float))
        assert isinstance(row["order_date"], str)
        # ISO date shape, consumed directly by the date form control.
        assert row["order_date"][4] == "-" and row["order_date"][7] == "-"


def test_seed_file_validates_against_model():
    model = model_from_yaml(seed.read_demo_model_yaml())
    import yaml

    raw = yaml.safe_load(
        (seed.EXAMPLES_DIR / seed.DEMO_RECORDS_FILE).read_text(encoding="utf-8")
    )
    validated = seed.validate_records(model, raw)
    assert set(validated) == {"customer", "order"}


@pytest.mark.parametrize(
    "bad_row",
    [
        {"region": "华东"},  # missing key field
        {"customer_name": "x", "region": "华东", "ghost": 1},  # unknown field
        {"customer_name": "x", "region": "海外"},  # enum outside allowed values
        {"customer_name": 9},  # wrong type for key field
    ],
)
def test_seed_validation_rejects_bad_rows(bad_row: dict):
    model = model_from_yaml(seed.read_demo_model_yaml())
    with pytest.raises(ValueError):
        seed.validate_records(model, {"customer": [bad_row]})


def test_seed_validation_rejects_unknown_entity_and_top_level_shape():
    model = model_from_yaml(seed.read_demo_model_yaml())
    with pytest.raises(ValueError):
        seed.validate_records(model, {"ghost": []})
    with pytest.raises(ValueError):
        seed.validate_records(model, [])
