from fastapi.testclient import TestClient

from backend.api.main import app
from backend.config import PROJECT_VERSION

client = TestClient(app)


def test_liveness_probe():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_versioned_health_checks_database():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "version": PROJECT_VERSION, "database": "ok"}


def test_unknown_route_returns_404_json():
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
