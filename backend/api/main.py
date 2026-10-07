"""Z-Sheet FastAPI application entrypoint.

Run locally: uv run uvicorn backend.api.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import health
from backend.config import PROJECT_VERSION, get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Z-Sheet API", version=PROJECT_VERSION)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    return app


app = create_app()
