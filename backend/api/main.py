"""Z-Sheet FastAPI application entrypoint.

Run locally: uv run uvicorn backend.api.main:app --reload
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.ai.provider import mask_key
from backend.ai.runtime_config import resolve_llm_config_safely
from backend.api.routes import (
    appends,
    apps,
    health,
    ingestion,
    llm,
    models,
    patch,
    publications,
    runtime,
)
from backend.config import PROJECT_VERSION, get_settings
from backend.logging_config import configure_logging, get_logger

logger = get_logger(__name__)


class SPASinglePageFiles(StaticFiles):
    """Static files with client-side route fallback to index.html.

    Starlette raises a 404 HTTPException (it does not return a 404 response)
    when a path matches neither a file nor a directory, so the fallback must
    catch the exception instead of inspecting the response.
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Mount passes paths with a leading slash. Unknown API routes keep
            # their JSON 404; only browser routes fall back to the SPA shell.
            request_path = scope["path"].lstrip("/")
            if exc.status_code != 404 or request_path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


def _find_spa_dist() -> Path | None:
    candidates = (
        Path("frontend_dist"),  # container layout (/app/frontend_dist)
        Path(__file__).resolve().parents[2] / "frontend" / "dist",  # local build
    )
    return next((p for p in candidates if (p / "index.html").is_file()), None)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="Z-Sheet API", version=PROJECT_VERSION)

    llm_config = resolve_llm_config_safely()
    logger.info(
        "Z-Sheet %s 启动；LLM endpoint=%s model=%s timeout=%.0fs retries=%d "
        "部署=%s key=%s（模型设置中的启用配置优先，未配置时使用 .env）",
        PROJECT_VERSION,
        llm_config.endpoint,
        llm_config.model or "(未配置)",
        llm_config.timeout,
        llm_config.max_retries,
        "本地" if llm_config.is_local else "公有云",
        mask_key(llm_config.api_key),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(apps.router)
    app.include_router(llm.router)
    app.include_router(models.router)
    app.include_router(runtime.router)
    app.include_router(ingestion.router)
    app.include_router(appends.router)
    app.include_router(patch.router)
    app.include_router(publications.management_router)
    app.include_router(publications.public_router)

    # Production-style single-container delivery: serve the built SPA from the
    # backend. Absent during `vite dev`, where the Vite proxy owns the UI.
    spa_dist = _find_spa_dist()
    if spa_dist is not None:
        app.mount(
            "/",
            SPASinglePageFiles(directory=str(spa_dist), html=True),
            name="spa",
        )
    return app


app = create_app()
