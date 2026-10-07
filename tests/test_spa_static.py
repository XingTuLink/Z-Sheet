"""Regression tests for SPA static serving and client-route fallback."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.main import SPASinglePageFiles


def _make_client(tmp_path) -> TestClient:
    (tmp_path / "index.html").write_text(
        "<!doctype html><title>spa</title>", encoding="utf-8"
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("export default 1", encoding="utf-8")

    # FastAPI (not bare Starlette): its default HTTPException handler renders
    # JSON 404s, which is the production contract the fallback must preserve.
    app = FastAPI()

    @app.get("/api/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    app.mount("/", SPASinglePageFiles(directory=str(tmp_path), html=True))
    return TestClient(app)


def test_index_and_assets_are_served(tmp_path):
    client = _make_client(tmp_path)
    assert client.get("/").status_code == 200
    body = client.get("/").text
    assert "<title>spa</title>" in body

    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert asset.text == "export default 1"


def test_client_routes_fall_back_to_index(tmp_path):
    client = _make_client(tmp_path)
    for path in ("/views/customer_list", "/views/order_form", "/deeply/nested/route"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.text == client.get("/").text


def test_unknown_api_routes_keep_json_404(tmp_path):
    client = _make_client(tmp_path)
    response = client.get("/api/missing")
    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")
    assert response.json() == {"detail": "Not Found"}

    # Declared API routes still win over the static mount.
    assert client.get("/api/ping").json() == {"ok": True}
