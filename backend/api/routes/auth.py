"""Local account endpoints: register, sign in, sign out and current user."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.auth import auth_repository as repo
from backend.auth import permissions, security
from backend.logging_config import get_logger
from backend.storage.db import get_db
from backend.storage.models import UserRecord

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

logger = get_logger("auth")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    created_at: datetime


class MeResponse(BaseModel):
    user: UserOut | None = None
    # True once at least one account exists: the instance then requires login.
    auth_required: bool = False


def _user_out(record: UserRecord) -> UserOut:
    created_at = record.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return UserOut(
        id=record.id,
        email=record.email,
        display_name=record.display_name,
        created_at=created_at,
    )


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=security.COOKIE_NAME,
        value=token,
        max_age=int(security.SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        path="/",
    )


def _validate_registration(body: RegisterRequest) -> tuple[str, str]:
    email = repo.normalize_email(body.email)
    if not _EMAIL_RE.match(email):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="请输入有效的邮箱地址",
        )
    if not MIN_PASSWORD_LENGTH <= len(body.password) <= MAX_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"密码长度需在 {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} 位之间",
        )
    if len(body.display_name.strip()) > 32:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="显示名称不能超过 32 个字符",
        )
    return email, body.display_name.strip()


@router.post(
    "/register", response_model=MeResponse, status_code=status.HTTP_201_CREATED
)
def register(
    body: RegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> MeResponse:
    email, display_name = _validate_registration(body)
    if repo.get_user_by_email(db, email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="该邮箱已注册，请直接登录"
        )
    try:
        user = repo.create_user(
            db,
            email=email,
            password_hash=security.hash_password(body.password),
            display_name=display_name,
        )
    except IntegrityError:
        # Two concurrent registrations for the same address: the unique
        # constraint decides, and the loser gets the normal conflict message.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="该邮箱已注册，请直接登录"
        ) from None
    token = repo.create_session(db, user.id)
    _set_session_cookie(response, token)
    logger.info("注册成功：user=%d email=%s", user.id, user.email)
    return MeResponse(user=_user_out(user), auth_required=True)


@router.post("/login", response_model=MeResponse)
def login(
    body: LoginRequest, response: Response, db: Session = Depends(get_db)
) -> MeResponse:
    user = repo.get_user_by_email(db, body.email)
    if user is None or not security.verify_password(body.password, user.password_hash):
        logger.warning("登录失败：email=%s", repo.normalize_email(body.email))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码不正确",
        )
    token = repo.create_session(db, user.id)
    _set_session_cookie(response, token)
    logger.info("登录成功：user=%d email=%s", user.id, user.email)
    return MeResponse(user=_user_out(user), auth_required=True)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    token = request.cookies.get(security.COOKIE_NAME)
    if token:
        repo.revoke_session(db, token)
    response.delete_cookie(security.COOKIE_NAME, path="/")


@router.get("/me", response_model=MeResponse)
def me(request: Request, db: Session = Depends(get_db)) -> MeResponse:
    """Always 200: the UI uses ``auth_required`` to decide about the login page."""
    actor = permissions.optional_actor(request, db)
    return MeResponse(
        user=None if actor.user_id is None else _me_user(db, actor.user_id),
        auth_required=repo.count_users(db) > 0,
    )


def _me_user(db: Session, user_id: int) -> UserOut | None:
    record = repo.get_user(db, user_id)
    return None if record is None else _user_out(record)
