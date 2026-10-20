"""Read endpoints shared by the owner runtime and the public /p surface.

Both surfaces serve the same deterministic views; only how they resolve the
model differs (current version vs. pinned published version). The handlers
here translate service results and domain errors into HTTP responses.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend.domain.models import BusinessModel
from backend.runtime import query, record_io
from backend.runtime.query import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    parse_filters,
    parse_sorts,
)

ContextLoader = Callable[[Session, str], "tuple[str, BusinessModel]"]


@dataclass(frozen=True)
class ListParams:
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    search: str = ""
    search_fields: str = ""
    sorts: str | None = None
    filters: str | None = None
    facets: str = ""


def list_params(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    search: str = "",
    search_fields: str = "",
    sorts: str | None = None,
    filters: str | None = None,
    facets: str = "",
) -> ListParams:
    return ListParams(
        page=page,
        page_size=page_size,
        search=search,
        search_fields=search_fields,
        sorts=sorts,
        filters=filters,
        facets=facets,
    )


def owner_context(db: Session, app_key: str) -> tuple[str, BusinessModel]:
    record, model = record_io.load_current_model(db, app_key)
    return record.app_key, model


def error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _guarded(action: Callable[[], Any]) -> Any:
    try:
        return action()
    except record_io.RecordConflict as exc:
        return error(409, str(exc))
    except record_io.RecordNotFound as exc:
        return error(404, str(exc))
    except ValueError as exc:
        # Bad query parameters and malformed persisted models are both 422.
        return error(422, str(exc))


def list_rows(
    db: Session,
    loader: ContextLoader,
    key: str,
    entity_key: str,
    params: ListParams,
) -> Any:
    def action() -> Any:
        app_key, model = loader(db, key)
        return query.query_rows(
            db,
            model,
            app_key,
            entity_key,
            page=params.page,
            page_size=params.page_size,
            search=params.search,
            search_fields=params.search_fields,
            filters=parse_filters(params.filters),
            sorts=parse_sorts(params.sorts),
            facets=params.facets,
        )

    return _guarded(action)


def get_row(
    db: Session, loader: ContextLoader, key: str, entity_key: str, row_key: str
) -> Any:
    def action() -> Any:
        app_key, model = loader(db, key)
        return {"row": query.fetch_row(db, model, app_key, entity_key, row_key)}

    return _guarded(action)


def related(
    db: Session,
    loader: ContextLoader,
    key: str,
    *,
    parent_entity: str,
    link: str,
    parent: str,
    limit: int,
    offset: int,
) -> Any:
    def action() -> Any:
        app_key, model = loader(db, key)
        return query.related_rows(
            db,
            model,
            app_key,
            parent_entity=parent_entity,
            link_key=link,
            parent_row_key=parent,
            limit=limit,
            offset=offset,
        )

    return _guarded(action)


def dashboard(db: Session, loader: ContextLoader, key: str) -> Any:
    def action() -> Any:
        app_key, model = loader(db, key)
        return query.dashboard(db, model, app_key)

    return _guarded(action)


__all__ = [
    "ListParams",
    "dashboard",
    "error",
    "get_row",
    "list_params",
    "list_rows",
    "owner_context",
    "related",
]
