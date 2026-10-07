"""Health endpoints: liveness probe and versioned readiness probe."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from backend.config import PROJECT_VERSION
from backend.storage.db import database_alive

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness: process is up. No dependency checks."""
    return {"status": "ok"}


@router.get("/api/v1/health")
def api_health(response: Response) -> dict[str, str]:
    """Readiness: API is up and the database is reachable."""
    try:
        database_alive()
    except Exception:  # noqa: BLE001 - any DB failure means not ready
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "error", "version": PROJECT_VERSION, "database": "unavailable"}
    return {"status": "ok", "version": PROJECT_VERSION, "database": "ok"}
