"""App-level roles: owner / editor / viewer, and anonymous public access."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.main import app

SALES_YAML = (
    Path(__file__).resolve().parents[1] / "examples" / "sales_management.yaml"
).read_text(encoding="utf-8")


def _client() -> TestClient:
    return TestClient(app)


def _signup(client: TestClient, email: str, password: str = "password123") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": email.split("@")[0]},
    )
    assert response.status_code == 201, response.text
    return response.json()["user"]


def _import(client: TestClient, app_key: str) -> None:
    response = client.post(
        "/api/v1/models/import",
        json={"app_key": app_key, "yaml_content": SALES_YAML},
    )
    assert response.status_code == 201, response.text


def _members(client: TestClient, app_key: str):
    return client.get(f"/api/v1/apps/{app_key}/members")


def test_creator_becomes_owner_of_the_new_system():
    owner = _client()
    _signup(owner, "owner@example.com")
    _import(owner, "crm")

    response = _members(owner, "crm")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unclaimed"] is False
    assert [member["role"] for member in body["members"]] == ["owner"]
    assert body["members"][0]["email"] == "owner@example.com"


def test_other_accounts_neither_see_nor_read_the_system():
    owner = _client()
    _signup(owner, "owner@example.com")
    _import(owner, "crm")

    stranger = _client()
    _signup(stranger, "stranger@example.com")

    assert stranger.get("/api/v1/apps").json() == []
    assert stranger.get("/api/v1/models/crm").status_code == 403
    assert stranger.get("/api/v1/apps/crm/overview").status_code == 403
    preview = stranger.post(
        "/api/v1/models/crm/patches", json={"text": "给客户加一个邮箱字段"}
    )
    assert preview.status_code == 403


def test_viewer_reads_but_cannot_write_until_promoted_to_editor():
    owner = _client()
    _signup(owner, "owner@example.com")
    _import(owner, "crm")

    viewer = _client()
    viewer_user = _signup(viewer, "viewer@example.com")

    added = owner.post(
        "/api/v1/apps/crm/members",
        json={"email": "viewer@example.com", "role": "viewer"},
    )
    assert added.status_code == 201, added.text
    assert {member["role"] for member in added.json()["members"]} == {"owner", "viewer"}

    assert viewer.get("/api/v1/models/crm").status_code == 200
    assert viewer.get("/api/v1/apps/crm/audit").status_code == 200
    apps = viewer.get("/api/v1/apps")
    assert apps.status_code == 200, apps.text
    assert apps.json()[0]["app_key"] == "crm"
    publish = viewer.post(
        "/api/v1/models/crm/publication", json={"slug": "sales-viewer"}
    )
    assert publish.status_code == 403
    assert _members(viewer, "crm").status_code == 403

    promoted = owner.patch(
        f"/api/v1/apps/crm/members/{viewer_user['id']}", json={"role": "editor"}
    )
    assert promoted.status_code == 200, promoted.text
    assert viewer.post(
        "/api/v1/models/crm/publication", json={"slug": "sales-viewer"}
    ).status_code == 201


def test_editor_cannot_manage_members():
    owner = _client()
    _signup(owner, "owner@example.com")
    _import(owner, "crm")

    editor = _client()
    _signup(editor, "editor@example.com")
    owner.post(
        "/api/v1/apps/crm/members",
        json={"email": "editor@example.com", "role": "editor"},
    )

    assert _members(editor, "crm").status_code == 403
    assert editor.post(
        "/api/v1/apps/crm/members",
        json={"email": "owner@example.com", "role": "viewer"},
    ).status_code == 403


def test_the_last_owner_cannot_be_demoted_or_removed():
    owner = _client()
    user = _signup(owner, "owner@example.com")
    _import(owner, "crm")

    demote = owner.patch(
        f"/api/v1/apps/crm/members/{user['id']}", json={"role": "editor"}
    )
    assert demote.status_code == 422
    assert "所有者" in demote.json()["detail"]
    assert owner.delete(f"/api/v1/apps/crm/members/{user['id']}").status_code == 422


def test_member_must_have_an_account():
    owner = _client()
    _signup(owner, "owner@example.com")
    _import(owner, "crm")

    missing = owner.post(
        "/api/v1/apps/crm/members",
        json={"email": "nobody@example.com", "role": "viewer"},
    )
    assert missing.status_code == 404
    bad_role = owner.post(
        "/api/v1/apps/crm/members",
        json={"email": "owner@example.com", "role": "admin"},
    )
    assert bad_role.status_code == 422


def test_public_link_stays_anonymous_after_accounts_exist():
    owner = _client()
    _signup(owner, "owner@example.com")
    _import(owner, "crm")
    published = owner.post(
        "/api/v1/models/crm/publication", json={"slug": "sales-public"}
    )
    assert published.status_code == 201, published.text

    anonymous = _client()
    assert anonymous.get("/api/v1/p/sales-public/bootstrap").status_code == 200
    assert anonymous.get("/api/v1/models/crm").status_code == 401


def test_demo_app_stays_open_for_visitors():
    owner = _client()
    _signup(owner, "owner@example.com")

    anonymous = _client()
    assert anonymous.get("/api/v1/runtime/default/bootstrap").status_code == 200


def test_legacy_system_without_members_stays_reachable():
    # Created while no account existed: no membership rows are written.
    bootstrap_client = _client()
    _import(bootstrap_client, "legacy")

    later = _client()
    _signup(later, "later@example.com")
    assert later.get("/api/v1/models/legacy").status_code == 200
    assert [app["app_key"] for app in later.get("/api/v1/apps").json()] == ["legacy"]
