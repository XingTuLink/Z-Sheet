"""HTTP helpers for the runtime query endpoints shared by tests."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def rows(client: TestClient, app_key: str, entity: str) -> list[dict[str, Any]]:
    """Every row of one entity, read through the paged list endpoint."""
    response = client.get(
        f"/api/v1/runtime/{app_key}/entities/{entity}/rows",
        params={"page_size": 200},
    )
    assert response.status_code == 200, response.text
    return response.json()["rows"]


def public_rows(client: TestClient, slug: str, entity: str) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/v1/p/{slug}/entities/{entity}/rows",
        params={"page_size": 200},
    )
    assert response.status_code == 200, response.text
    return response.json()["rows"]
