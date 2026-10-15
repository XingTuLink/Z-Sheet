"""Persistence for app publications (Day 33 publish feature).

A publication is the *public face* of a confirmed app: one row per app maps a
custom slug to a pinned model version. Rules:

* one publication row per app (re-publishing / slug change update that row);
* slugs are globally unique, 2-40 chars of ``[a-z0-9-]`` and may not start or
  end with a hyphen; reserved words are rejected;
* an unpublished row keeps owning its slug, so a previously shared link can be
  restored and a stranger cannot squat it;
* ``creator_id`` is carried through as None today; the future users system
  fills it without a data-model change.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.logging_config import get_logger
from backend.storage.models import AppPublicationRecord
from backend.storage.repositories import model_repository as model_repo

logger = get_logger("storage.publication")

STATUS_PUBLISHED = "published"
STATUS_UNPUBLISHED = "unpublished"

SLUG_MIN_LENGTH = 2
SLUG_MAX_LENGTH = 40
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$")

# Paths the SPA / API / static serving already claim, plus product words that
# would confuse users. A published app may never occupy these slugs.
RESERVED_SLUGS = frozenset(
    {
        "api",
        "app",
        "apps",
        "p",
        "pub",
        "public",
        "admin",
        "login",
        "logout",
        "signup",
        "register",
        "health",
        "healthz",
        "ready",
        "assets",
        "static",
        "docs",
        "doc",
        "demo",
        "workspace",
        "default",
        "favicon",
        "robots",
        "sitemap",
        "ingestion",
        "models",
        "runtime",
        "v1",
    }
)


class SlugError(ValueError):
    """The requested slug is malformed or reserved (maps to 422)."""


class PublicationExistsError(Exception):
    """The app already has a publication row (maps to 409)."""


class SlugConflictError(Exception):
    """Another publication already owns this slug (maps to 409)."""


class PublicationNotFoundError(Exception):
    """No publication exists for this app (maps to 404)."""


def normalize_slug(raw: str) -> str:
    return str(raw or "").strip().lower()


def validate_slug(raw: str) -> str:
    """Return the normalized slug or raise SlugError with a Chinese message."""
    slug = normalize_slug(raw)
    if not slug:
        raise SlugError("链接标识不能为空")
    if len(slug) < SLUG_MIN_LENGTH or len(slug) > SLUG_MAX_LENGTH:
        raise SlugError(
            f"链接标识长度需为 {SLUG_MIN_LENGTH}-{SLUG_MAX_LENGTH} 个字符"
        )
    if not _SLUG_RE.fullmatch(slug):
        raise SlugError("链接标识只能包含小写字母、数字和连字符（-），且不能以连字符开头或结尾")
    if slug in RESERVED_SLUGS:
        raise SlugError(f"链接标识 {slug!r} 为系统保留字，请换一个")
    return slug


def get_for_app(db: Session, app_key: str) -> AppPublicationRecord | None:
    return db.scalar(
        select(AppPublicationRecord).where(
            AppPublicationRecord.app_key == app_key
        )
    )


def get_published_by_slug(db: Session, slug: str) -> AppPublicationRecord | None:
    """Return the publication for a slug only while it is published."""
    return db.scalar(
        select(AppPublicationRecord).where(
            AppPublicationRecord.slug == normalize_slug(slug),
            AppPublicationRecord.status == STATUS_PUBLISHED,
        )
    )


def _slug_owned_elsewhere(
    db: Session, slug: str, *, app_key: str
) -> AppPublicationRecord | None:
    return db.scalar(
        select(AppPublicationRecord).where(
            AppPublicationRecord.slug == slug,
            AppPublicationRecord.app_key != app_key,
        )
    )


def create_publication(db: Session, app_key: str, raw_slug: str) -> AppPublicationRecord:
    """Publish an existing app's CURRENT model version under a fresh slug."""
    slug = validate_slug(raw_slug)
    if get_for_app(db, app_key) is not None:
        raise PublicationExistsError(f"应用 {app_key!r} 已发布，请使用更新接口")
    current = model_repo.get_current(db, app_key, raise_if_missing=False)
    if current is None:
        raise model_repo.AppNotFoundError(f"应用 {app_key!r} 不存在，无法发布")
    if _slug_owned_elsewhere(db, slug, app_key=app_key) is not None:
        raise SlugConflictError(f"链接标识 {slug!r} 已被占用")

    now = datetime.now(UTC)
    record = AppPublicationRecord(
        app_key=app_key,
        slug=slug,
        model_version=current.version,
        status=STATUS_PUBLISHED,
        creator_id=None,
        published_at=now,
    )
    db.add(record)
    db.flush()
    logger.info(
        "应用已发布：app=%s slug=%s version=%d", app_key, slug, current.version
    )
    return record


def change_slug(db: Session, record: AppPublicationRecord, raw_slug: str) -> None:
    """Replace the slug; the previous URL stops working immediately."""
    slug = validate_slug(raw_slug)
    if slug == record.slug:
        return
    if _slug_owned_elsewhere(db, slug, app_key=record.app_key) is not None:
        raise SlugConflictError(f"链接标识 {slug!r} 已被占用")
    old = record.slug
    record.slug = slug
    record.updated_at = datetime.now(UTC)
    db.flush()
    logger.info("发布链接已修改：app=%s %s -> %s", record.app_key, old, slug)


def republish(db: Session, record: AppPublicationRecord) -> None:
    """Pin the app's current model version and (re)enable the public link."""
    current = model_repo.get_current(
        db, record.app_key, raise_if_missing=False
    )
    if current is None:
        # Extremely defensive: the publication references versions of an app
        # whose whole history vanished (manual DB surgery).
        raise model_repo.AppNotFoundError(
            f"应用 {record.app_key!r} 不存在，无法更新发布"
        )
    if record.status != STATUS_PUBLISHED or record.model_version != current.version:
        record.model_version = current.version
    now = datetime.now(UTC)
    record.status = STATUS_PUBLISHED
    record.published_at = now
    record.updated_at = now
    db.flush()
    logger.info(
        "发布已更新：app=%s slug=%s version=%d",
        record.app_key,
        record.slug,
        current.version,
    )


def unpublish(db: Session, record: AppPublicationRecord) -> None:
    """Take the public link down. Slug ownership is retained."""
    record.status = STATUS_UNPUBLISHED
    record.published_at = None
    record.updated_at = datetime.now(UTC)
    db.flush()
    logger.info("应用已取消发布：app=%s slug=%s", record.app_key, record.slug)
