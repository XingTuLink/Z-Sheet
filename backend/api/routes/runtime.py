"""Runtime bootstrap endpoint for the deterministic renderer (Day 4).

Returns model + data in one round trip. The renderer never calls an AI model
(design doc 13.1 / 28.5); it only renders what this endpoint hands it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend.api.schemas import RuntimeBootstrapResponse
from backend.domain.models import BusinessModel
from backend.runtime import seed
from backend.storage.db import get_db
from backend.storage.repositories import app_data_repository
from backend.storage.repositories import model_repository as repo

router = APIRouter(prefix="/api/v1/runtime", tags=["runtime"])


@router.get(
    "/{app_key}/bootstrap",
    response_model=RuntimeBootstrapResponse,
)
def bootstrap(app_key: str, db: Session = Depends(get_db)) -> Any:
    try:
        record = repo.get_current(db, app_key, raise_if_missing=False)
        if record is None:
            # Only the bundled demo app auto-seeds; unknown apps stay 404.
            if app_key != seed.DEMO_APP_KEY:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"detail": f"app {app_key!r} not found"},
                )
            record = seed.ensure_demo_model(db)

        model = BusinessModel.model_validate(record.snapshot)
        if app_key == seed.DEMO_APP_KEY:
            # The bundled demo app always serves its checked-in seed rows.
            records = seed.load_demo_records(model)
        else:
            # Day 15: confirmed uploads serve the user's own workbook rows.
            records = app_data_repository.get_records(db, app_key) or {}
    except ValueError as exc:  # malformed app_key or invalid seed file
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    return {
        "app_key": app_key,
        "version": record.version,
        "model": record.snapshot,
        "records": records,
    }
