"""App-level access control for the built-in user system.

Three roles work on a generated system:

* ``viewer`` reads the model, data, versions and audit trail;
* ``editor`` additionally changes the model, appends data and publishes;
* ``owner`` additionally manages who else may access the system.

Public links (``/p/:slug``) and the bundled demo app stay anonymous by
design — they never pass through these dependencies.

Bootstrap rule: while the instance has no accounts, requests are treated as
the local owner. The first registered account turns authentication on, so a
single-user deployment keeps working and does not need to log in on day one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.auth import auth_repository as auth_repo
from backend.auth import security
from backend.storage.db import get_db

DEMO_APP_KEY = "default"

ROLE_RANK = {
    auth_repo.ROLE_VIEWER: 1,
    auth_repo.ROLE_EDITOR: 2,
    auth_repo.ROLE_OWNER: 3,
}
ROLE_LABELS = {
    auth_repo.ROLE_VIEWER: "查看",
    auth_repo.ROLE_EDITOR: "编辑",
    auth_repo.ROLE_OWNER: "所有者",
}


@dataclass(frozen=True)
class Actor:
    """Who is performing the request, and with which role on the app."""

    user_id: int | None = None
    email: str | None = None
    display_name: str | None = None
    role: str | None = None

    @property
    def label(self) -> str:
        """Human-readable actor written into the audit trail."""
        return self.email or "本地用户"


def optional_actor(request: Request, db: Session) -> Actor:
    """Cookie user if signed in, otherwise an anonymous actor (never raises)."""
    actor = _actor_from_cookie(request, db)
    return actor if actor is not None else Actor()


def _actor_from_cookie(request: Request, db: Session) -> Actor | None:
    token = request.cookies.get(security.COOKIE_NAME)
    if not token:
        return None
    user = auth_repo.get_session_user(db, token)
    if user is None:
        return None
    return Actor(user.id, user.email, user.display_name)


def resolve_actor(request: Request, db: Session) -> Actor:
    """Current actor; raises 401 once accounts exist and nobody is signed in."""
    actor = _actor_from_cookie(request, db)
    if actor is not None:
        return actor
    if auth_repo.count_users(db) > 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录后再操作",
        )
    return Actor()


def effective_role(db: Session, actor: Actor, app_key: str) -> str | None:
    """Role of the actor on one app, or None when access is not allowed."""
    if app_key == DEMO_APP_KEY:
        # The bundled demo is a public, editable playground.
        return auth_repo.ROLE_OWNER
    if actor.user_id is None:
        return auth_repo.ROLE_OWNER
    member = auth_repo.get_member(db, app_key, actor.user_id)
    if member is not None:
        return member.role
    # Apps created before accounts existed carry no membership rows. Any
    # signed-in account may manage them so they never become unreachable.
    if not auth_repo.has_members(db, app_key):
        return auth_repo.ROLE_OWNER
    return None


def ensure_role(db: Session, actor: Actor, app_key: str, minimum: str) -> Actor:
    """Enforce at least ``minimum`` on ``app_key`` for an already resolved actor."""
    role = effective_role(db, actor, app_key)
    if role is None or ROLE_RANK[role] < ROLE_RANK[minimum]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"当前账号没有该系统的{ROLE_LABELS[minimum]}权限",
        )
    return replace(actor, role=role)


def ensure_access(
    db: Session, request: Request, app_key: str, minimum: str
) -> Actor:
    """Resolve the actor and enforce at least ``minimum`` on ``app_key``."""
    actor = (
        optional_actor(request, db)
        if app_key == DEMO_APP_KEY
        else resolve_actor(request, db)
    )
    return ensure_role(db, actor, app_key, minimum)


def ensure_write_access(
    db: Session, request: Request, app_key: str
) -> Actor:
    """Editor check used when the app may not exist yet (first import)."""
    return ensure_access(db, request, app_key, auth_repo.ROLE_EDITOR)


def _app_role_dependency(minimum: str) -> Callable[..., Actor]:
    def dependency(
        app_key: str,
        request: Request,
        db: Session = Depends(get_db),
    ) -> Actor:
        return ensure_access(db, request, app_key, minimum)

    return dependency


require_viewer = _app_role_dependency(auth_repo.ROLE_VIEWER)
require_editor = _app_role_dependency(auth_repo.ROLE_EDITOR)
require_owner = _app_role_dependency(auth_repo.ROLE_OWNER)


def current_actor(request: Request, db: Session = Depends(get_db)) -> Actor:
    """Actor for endpoints that are not scoped to an existing app."""
    return resolve_actor(request, db)


def accessible_app_keys(db: Session, actor: Actor) -> set[str] | None:
    """App keys the actor may see, or None meaning "every app".

    Unclaimed apps (no membership rows) stay visible to every signed-in
    account, matching :func:`effective_role`.
    """
    if actor.user_id is None:
        return None
    return auth_repo.app_keys_for_user(db, actor.user_id)
