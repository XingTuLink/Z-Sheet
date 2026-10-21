"""Manage who can access one generated system (owner only)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import audit
from backend.auth import auth_repository as repo
from backend.auth.permissions import Actor, require_owner
from backend.logging_config import get_logger
from backend.storage.db import get_db
from backend.storage.models import AppMemberRecord, UserRecord
from backend.storage.repositories import model_repository

router = APIRouter(prefix="/api/v1/apps", tags=["members"])

logger = get_logger("members")


class MemberOut(BaseModel):
    user_id: int
    email: str
    display_name: str
    role: str
    added_at: datetime


class MemberList(BaseModel):
    app_key: str
    members: list[MemberOut]
    # True for systems created before accounts existed: they have no rows and
    # stay open to every signed-in account until an owner adds members.
    unclaimed: bool


class MemberAddRequest(BaseModel):
    email: str
    role: str = repo.ROLE_VIEWER


class MemberRoleRequest(BaseModel):
    role: str


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _app_exists(db: Session, app_key: str) -> bool:
    try:
        return (
            model_repository.get_current(db, app_key, raise_if_missing=False)
            is not None
        )
    except ValueError:
        return False


def _member_out(member: AppMemberRecord, user: UserRecord) -> MemberOut:
    return MemberOut(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=member.role,
        added_at=_as_utc(member.created_at),
    )


@router.get("/{app_key}/members", response_model=MemberList)
def list_members(
    app_key: str,
    actor: Actor = Depends(require_owner),
    db: Session = Depends(get_db),
) -> MemberList | JSONResponse:
    if not _app_exists(db, app_key):
        return _error(status.HTTP_404_NOT_FOUND, f"系统 {app_key} 不存在")
    rows = repo.list_members(db, app_key)
    return MemberList(
        app_key=app_key,
        members=[_member_out(member, user) for member, user in rows],
        unclaimed=not rows,
    )


@router.post(
    "/{app_key}/members",
    response_model=MemberList,
    status_code=status.HTTP_201_CREATED,
)
def add_member(
    app_key: str,
    body: MemberAddRequest,
    actor: Actor = Depends(require_owner),
    db: Session = Depends(get_db),
) -> MemberList | JSONResponse:
    if not _app_exists(db, app_key):
        return _error(status.HTTP_404_NOT_FOUND, f"系统 {app_key} 不存在")
    try:
        repo.validate_role(body.role)
    except ValueError as exc:
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    user = repo.get_user_by_email(db, body.email)
    if user is None:
        return _error(status.HTTP_404_NOT_FOUND, "该邮箱尚未注册，请让对方先注册账号")
    try:
        repo.add_member(db, app_key=app_key, user_id=user.id, role=body.role)
    except repo.MemberExistsError as exc:
        return _error(status.HTTP_409_CONFLICT, str(exc))
    logger.info(
        "成员已添加：app=%s user=%d role=%s（操作人=%s）",
        app_key,
        user.id,
        body.role,
        actor.label,
    )
    audit.record(
        db,
        app_key=app_key,
        actor=actor,
        action=audit.ACTION_MEMBER_ADD,
        summary=f"添加成员 {user.email}（{body.role}）",
        detail={"user_id": user.id, "role": body.role},
    )
    return list_members(app_key, actor, db)


@router.patch("/{app_key}/members/{user_id}", response_model=MemberList)
def update_member(
    app_key: str,
    user_id: int,
    body: MemberRoleRequest,
    actor: Actor = Depends(require_owner),
    db: Session = Depends(get_db),
) -> MemberList | JSONResponse:
    member = repo.get_member(db, app_key, user_id)
    if member is None:
        return _error(status.HTTP_404_NOT_FOUND, "该成员不在系统中")
    try:
        repo.validate_role(body.role)
    except ValueError as exc:
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    if member.role == repo.ROLE_OWNER and body.role != repo.ROLE_OWNER:
        if repo.count_owners(db, app_key) <= 1:
            return _error(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "系统至少需要保留一位所有者",
            )
    previous = member.role
    repo.update_member_role(db, member, body.role)
    user = repo.get_user(db, user_id)
    logger.info(
        "成员角色已更新：app=%s user=%d %s→%s（操作人=%s）",
        app_key,
        user_id,
        previous,
        body.role,
        actor.label,
    )
    audit.record(
        db,
        app_key=app_key,
        actor=actor,
        action=audit.ACTION_MEMBER_UPDATE,
        summary=f"调整成员角色 {user.email if user else user_id}：{previous}→{body.role}",
        detail={"user_id": user_id, "from": previous, "to": body.role},
    )
    return list_members(app_key, actor, db)


@router.delete("/{app_key}/members/{user_id}", response_model=MemberList)
def remove_member(
    app_key: str,
    user_id: int,
    actor: Actor = Depends(require_owner),
    db: Session = Depends(get_db),
) -> MemberList | JSONResponse:
    member = repo.get_member(db, app_key, user_id)
    if member is None:
        return _error(status.HTTP_404_NOT_FOUND, "该成员不在系统中")
    if member.role == repo.ROLE_OWNER and repo.count_owners(db, app_key) <= 1:
        return _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "系统至少需要保留一位所有者"
        )
    user = repo.get_user(db, user_id)
    repo.remove_member(db, member)
    logger.info(
        "成员已移除：app=%s user=%d（操作人=%s）",
        app_key,
        user_id,
        actor.label,
    )
    audit.record(
        db,
        app_key=app_key,
        actor=actor,
        action=audit.ACTION_MEMBER_REMOVE,
        summary=f"移除成员 {user.email if user else user_id}",
        detail={"user_id": user_id, "role": member.role},
    )
    return list_members(app_key, actor, db)
