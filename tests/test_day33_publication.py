"""Day 33: publish apps under custom slugs with pinned model versions.

Covers slug rules, the management API, the strictly read-only public
bootstrap, pinning semantics (model pinned, records live), slug changes
invalidating the old URL, unpublish/re-publish with slug ownership retained,
and the fact no write endpoint exists under the public prefix.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.storage.repositories import publication_repository as pubs

client = TestClient(app)
SALES_YAML = (
    Path(__file__).resolve().parents[1] / "examples" / "sales_management.yaml"
).read_text(encoding="utf-8")

ADD_EMAIL_PATCH = {
    "ops": [
        {
            "op": "add",
            "path": "/entities/0/fields/-",
            "value": {
                "key": "email",
                "name": "邮箱",
                "type": "string",
                "role": "dimension",
                "confidence": 0.9,
            },
        }
    ],
    "source_request": "客户实体增加邮箱字段",
}


def _import(app_key: str) -> None:
    response = client.post(
        "/api/v1/models/import",
        json={"app_key": app_key, "yaml_content": SALES_YAML},
    )
    assert response.status_code == 201, response.text


def _publish(app_key: str, slug: str, expected: int = 201) -> dict:
    response = client.post(
        f"/api/v1/models/{app_key}/publication",
        json={"slug": slug},
    )
    assert response.status_code == expected, response.text
    return response.json()


# ---------- slug rules (pure unit level) ----------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "a",  # too short
        "x" * 41,  # too long
        "-leading",
        "trailing-",
        "has_underscore",
        "中文字符",
        "api",
        "app",
        "workspace",
        "demo",
    ],
)
def test_validate_slug_rejects_bad_and_reserved(raw: str) -> None:
    with pytest.raises(pubs.SlugError):
        pubs.validate_slug(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Kehu-XiaoShou", "kehu-xiaoshou"),
        ("  two-words  ", "two-words"),
        ("a1", "a1"),
        ("x" * 40, "x" * 40),
    ],
)
def test_validate_slug_normalizes_valid(raw: str, expected: str) -> None:
    assert pubs.validate_slug(raw) == expected


# ---------- management API ----------


def test_publish_unknown_app_is_404() -> None:
    body = _publish("ghost", "ghost-link", expected=404)
    assert "不存在" in body["detail"]


def test_publish_creates_publication_pinning_current_version() -> None:
    _import("workspace")
    body = _publish("workspace", "kehu-xiaoshou")
    assert body["app_key"] == "workspace"
    assert body["slug"] == "kehu-xiaoshou"
    assert body["status"] == "published"
    assert body["public"] is True
    assert body["model_version"] == 1
    assert body["current_version"] == 1
    assert body["published_at"]

    fetched = client.get("/api/v1/models/workspace/publication")
    assert fetched.status_code == 200
    assert fetched.json()["slug"] == "kehu-xiaoshou"


def test_get_publication_for_never_published_app_is_404() -> None:
    _import("lonely")
    assert client.get("/api/v1/models/lonely/publication").status_code == 404


def test_publish_twice_conflicts() -> None:
    _import("workspace")
    _publish("workspace", "first-link")
    body = _publish("workspace", "second-link", expected=409)
    assert "已发布" in body["detail"]


def test_bad_and_reserved_slug_rejected_with_422() -> None:
    _import("workspace")
    bad = client.post(
        "/api/v1/models/workspace/publication", json={"slug": "Bad_Slug!"}
    )
    assert bad.status_code == 422
    reserved = client.post(
        "/api/v1/models/workspace/publication", json={"slug": "admin"}
    )
    assert reserved.status_code == 422
    assert "保留" in reserved.json()["detail"]
    # Nothing was created.
    assert client.get("/api/v1/models/workspace/publication").status_code == 404


def test_slug_is_globally_unique_across_apps() -> None:
    _import("workspace")
    _import("workb")
    _publish("workspace", "shared-link")
    body = _publish("workb", "shared-link", expected=409)
    assert "占用" in body["detail"]


# ---------- public bootstrap: pinned model + live records + read-only ----------


def test_public_bootstrap_serves_pinned_model_but_live_records() -> None:
    _import("workspace")
    _publish("workspace", "kehu-xiaoshou")

    # A business row created by the owner must be visible on the public page.
    created = client.post(
        "/api/v1/runtime/workspace/entities/customer/records",
        json={"row": {"customer_name": "公开测试客户"}},
    )
    assert created.status_code == 201, created.text

    public = client.get("/api/v1/p/kehu-xiaoshou/bootstrap")
    assert public.status_code == 200
    payload = public.json()
    assert payload["version"] == 1
    customer_fields = {
        f["key"] for f in payload["model"]["entities"][0]["fields"]
    }
    assert "email" not in customer_fields
    names = [
        row["customer_name"] for row in payload["records"]["customer"]
    ]
    assert "公开测试客户" in names

    # Owner evolves the model to v2.
    patched = client.post(
        "/api/v1/models/workspace/patches", json=ADD_EMAIL_PATCH
    )
    assert patched.status_code == 201, patched.text

    # Public page stays on v1 ...
    public_after = client.get("/api/v1/p/kehu-xiaoshou/bootstrap")
    assert public_after.status_code == 200
    public_model = public_after.json()["model"]
    assert "email" not in {
        f["key"] for f in public_model["entities"][0]["fields"]
    }
    assert public_after.json()["version"] == 1

    # ... while the owner sees v2 ...
    owner = client.get("/api/v1/runtime/workspace/bootstrap")
    assert owner.json()["version"] == 2

    # ... and the management state reports a newer current version.
    state = client.get("/api/v1/models/workspace/publication").json()
    assert state["model_version"] == 1
    assert state["current_version"] == 2


def test_public_surface_has_no_write_endpoint() -> None:
    _import("workspace")
    _publish("workspace", "kehu-xiaoshou")
    # Method Not Allowed: the only public route is GET bootstrap.
    post = client.post(
        "/api/v1/p/kehu-xiaoshou/bootstrap", json={"row": {}}
    )
    assert post.status_code == 405
    # No CRUD-style child routes exist (404/405 both prove there is no write
    # endpoint under the public prefix; unmatched POSTs hit the SPA mount).
    write = client.post(
        "/api/v1/p/kehu-xiaoshou/entities/customer/records",
        json={"row": {"customer_name": "hacker"}},
    )
    assert write.status_code in (404, 405)
    # The attempted write never landed.
    public = client.get("/api/v1/p/kehu-xiaoshou/bootstrap").json()
    assert all(
        row["customer_name"] != "hacker" for row in public["records"]["customer"]
    )


def test_unknown_and_unpublished_slug_return_same_404() -> None:
    assert client.get("/api/v1/p/never-existed/bootstrap").status_code == 404

    _import("workspace")
    _publish("workspace", "temp-link")
    unpublished = client.delete("/api/v1/models/workspace/publication")
    assert unpublished.status_code == 200
    assert unpublished.json()["public"] is False

    assert client.get("/api/v1/p/temp-link/bootstrap").status_code == 404


# ---------- slug changes, republish, unpublish lifecycle ----------


def test_change_slug_invalidates_old_url_immediately() -> None:
    _import("workspace")
    _publish("workspace", "old-link")

    bad = client.put(
        "/api/v1/models/workspace/publication", json={"slug": "BAD!"}
    )
    assert bad.status_code == 422
    # Old link still works after a rejected change.
    assert client.get("/api/v1/p/old-link/bootstrap").status_code == 200

    ok = client.put(
        "/api/v1/models/workspace/publication", json={"slug": "new-link"}
    )
    assert ok.status_code == 200
    assert ok.json()["slug"] == "new-link"

    assert client.get("/api/v1/p/old-link/bootstrap").status_code == 404
    assert client.get("/api/v1/p/new-link/bootstrap").status_code == 200


def test_unpublished_slug_cannot_be_squatted() -> None:
    _import("workspace")
    _import("workb")
    _publish("workspace", "guarded-link")
    client.delete("/api/v1/models/workspace/publication")

    # The row keeps owning the slug even while offline.
    squat = _publish("workb", "guarded-link", expected=409)
    assert "占用" in squat["detail"]


def test_republish_pins_newest_version_and_restores_link() -> None:
    _import("workspace")
    _publish("workspace", "kehu-xiaoshou")
    client.post("/api/v1/models/workspace/patches", json=ADD_EMAIL_PATCH)

    # Take it offline first.
    client.delete("/api/v1/models/workspace/publication")
    assert client.get("/api/v1/p/kehu-xiaoshou/bootstrap").status_code == 404

    # republish=true both restores the link and pins the newest version.
    restored = client.put(
        "/api/v1/models/workspace/publication", json={"republish": True}
    )
    assert restored.status_code == 200
    body = restored.json()
    assert body["public"] is True
    assert body["model_version"] == 2
    assert body["current_version"] == 2

    public = client.get("/api/v1/p/kehu-xiaoshou/bootstrap")
    assert public.status_code == 200
    assert public.json()["version"] == 2


def test_put_without_any_change_is_422() -> None:
    _import("workspace")
    _publish("workspace", "kehu-xiaoshou")
    response = client.put(
        "/api/v1/models/workspace/publication", json={}
    )
    assert response.status_code == 422


def test_update_publication_for_missing_app_is_404() -> None:
    assert (
        client.put(
            "/api/v1/models/ghost/publication", json={"republish": True}
        ).status_code
        == 404
    )
    assert (
        client.delete("/api/v1/models/ghost/publication").status_code == 404
    )
