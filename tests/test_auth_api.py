"""Local accounts: registration, sign-in, sign-out and the current user."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.main import app

COOKIE = "zsheet_session"


def _client() -> TestClient:
    """Fresh client so cookie jars never leak between tests."""
    return TestClient(app)


def _register(
    client: TestClient,
    email: str = "owner@example.com",
    password: str = "password123",
    display_name: str = "张三",
):
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": display_name},
    )


def _me(client: TestClient) -> dict:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200, response.text
    return response.json()


def test_anonymous_me_before_any_account_is_open():
    payload = _me(_client())
    assert payload == {"user": None, "auth_required": False}


def test_register_signs_in_and_exposes_no_hash():
    client = _client()
    response = _register(client, email="Owner@Example.COM")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == "owner@example.com"
    assert body["auth_required"] is True
    assert "password" not in response.text
    assert COOKIE in client.cookies

    me = _me(client)
    assert me["user"]["email"] == "owner@example.com"
    assert me["user"]["display_name"] == "张三"
    assert me["auth_required"] is True


def test_duplicate_email_is_rejected_case_insensitively():
    client = _client()
    assert _register(client).status_code == 201
    response = _register(client, email="OWNER@example.com")
    assert response.status_code == 409
    assert "已注册" in response.json()["detail"]


def test_register_validates_email_password_and_name():
    client = _client()
    bad_email = client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "password": "password123"},
    )
    assert bad_email.status_code == 422
    short_password = client.post(
        "/api/v1/auth/register",
        json={"email": "a@example.com", "password": "short"},
    )
    assert short_password.status_code == 422
    long_name = client.post(
        "/api/v1/auth/register",
        json={
            "email": "b@example.com",
            "password": "password123",
            "display_name": "名" * 40,
        },
    )
    assert long_name.status_code == 422


def test_login_rejects_wrong_password_and_accepts_correct_one():
    client = _client()
    assert _register(client).status_code == 201
    client.post("/api/v1/auth/logout")

    wrong = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "wrong-password"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "邮箱或密码不正确"

    unknown = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "password123"},
    )
    assert unknown.status_code == 401

    ok = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "password123"},
    )
    assert ok.status_code == 200, ok.text
    assert _me(client)["user"]["email"] == "owner@example.com"


def test_logout_revokes_the_session_token():
    client = _client()
    assert _register(client).status_code == 201
    token = client.cookies.get(COOKIE)
    assert token

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    assert _me(client)["user"] is None

    # Replaying the old cookie must not sign the browser back in.
    replay = client.get("/api/v1/auth/me", cookies={COOKIE: token})
    assert replay.status_code == 200
    assert replay.json()["user"] is None


def test_anonymous_writes_are_blocked_once_an_account_exists():
    owner = _client()
    assert _register(owner).status_code == 201

    anonymous = _client()
    response = anonymous.post(
        "/api/v1/models/import",
        json={"app_key": "guarded", "yaml_content": "app: {}"},
    )
    assert response.status_code == 401
    assert anonymous.get("/api/v1/apps").status_code == 401
