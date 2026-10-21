"""Persistence for accounts, login sessions and app memberships."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.auth import security
from backend.storage.models import AppMemberRecord, AuthSessionRecord, UserRecord

ROLE_OWNER = "owner"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"
ROLES = (ROLE_OWNER, ROLE_EDITOR, ROLE_VIEWER)


class MemberExistsError(Exception):
    """The account already has a role on this app."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_role(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"未知角色 {role!r}，可选：{'、'.join(ROLES)}")
    return role


def create_user(
    db: Session, *, email: str, password_hash: str, display_name: str = ""
) -> UserRecord:
    record = UserRecord(
        email=normalize_email(email),
        password_hash=password_hash,
        display_name=display_name.strip(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_user_by_email(db: Session, email: str) -> UserRecord | None:
    return db.scalar(
        select(UserRecord).where(UserRecord.email == normalize_email(email))
    )


def get_user(db: Session, user_id: int) -> UserRecord | None:
    return db.get(UserRecord, user_id)


def count_users(db: Session) -> int:
    return int(db.scalar(select(func.count(UserRecord.id))) or 0)


def create_session(db: Session, user_id: int) -> str:
    """Persist a new login session and return the raw cookie token."""
    token = security.new_session_token()
    db.add(
        AuthSessionRecord(
            token_hash=security.hash_token(token),
            user_id=user_id,
            expires_at=security.session_expiry(),
        )
    )
    db.commit()
    return token


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def get_session_user(db: Session, token: str) -> UserRecord | None:
    """Resolve a cookie token to its (still valid) account."""
    if not token:
        return None
    record = db.scalar(
        select(AuthSessionRecord).where(
            AuthSessionRecord.token_hash == security.hash_token(token)
        )
    )
    if record is None or record.revoked_at is not None:
        return None
    if _as_utc(record.expires_at) <= datetime.now(UTC):
        return None
    return get_user(db, record.user_id)


def revoke_session(db: Session, token: str) -> None:
    record = db.scalar(
        select(AuthSessionRecord).where(
            AuthSessionRecord.token_hash == security.hash_token(token)
        )
    )
    if record is None or record.revoked_at is not None:
        return
    record.revoked_at = datetime.now(UTC)
    db.commit()


def get_member(db: Session, app_key: str, user_id: int) -> AppMemberRecord | None:
    return db.scalar(
        select(AppMemberRecord).where(
            AppMemberRecord.app_key == app_key,
            AppMemberRecord.user_id == user_id,
        )
    )


def has_members(db: Session, app_key: str) -> bool:
    return (
        db.scalar(
            select(func.count(AppMemberRecord.id)).where(
                AppMemberRecord.app_key == app_key
            )
        )
        or 0
    ) > 0


def add_member(
    db: Session, *, app_key: str, user_id: int, role: str
) -> AppMemberRecord:
    validate_role(role)
    if get_member(db, app_key, user_id) is not None:
        raise MemberExistsError("该账号已在系统中，无需重复添加")
    record = AppMemberRecord(app_key=app_key, user_id=user_id, role=role)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def update_member_role(db: Session, member: AppMemberRecord, role: str) -> None:
    validate_role(role)
    member.role = role
    member.updated_at = datetime.now(UTC)
    db.commit()


def remove_member(db: Session, member: AppMemberRecord) -> None:
    db.delete(member)
    db.commit()


def ensure_owner(db: Session, app_key: str, user_id: int) -> AppMemberRecord | None:
    """Make ``user_id`` the owner of a freshly created app (idempotent)."""
    if get_member(db, app_key, user_id) is not None:
        return None
    return add_member(db, app_key=app_key, user_id=user_id, role=ROLE_OWNER)


def count_owners(db: Session, app_key: str) -> int:
    return int(
        db.scalar(
            select(func.count(AppMemberRecord.id)).where(
                AppMemberRecord.app_key == app_key,
                AppMemberRecord.role == ROLE_OWNER,
            )
        )
        or 0
    )


def list_members(db: Session, app_key: str) -> list[tuple[AppMemberRecord, UserRecord]]:
    rows = db.execute(
        select(AppMemberRecord, UserRecord)
        .join(UserRecord, UserRecord.id == AppMemberRecord.user_id)
        .where(AppMemberRecord.app_key == app_key)
        .order_by(AppMemberRecord.created_at)
    ).all()
    return [(member, user) for member, user in rows]


def app_keys_for_user(db: Session, user_id: int) -> set[str]:
    return {
        str(key)
        for key in db.scalars(
            select(AppMemberRecord.app_key).where(
                AppMemberRecord.user_id == user_id
            )
        ).all()
    }


def claimed_app_keys(db: Session) -> set[str]:
    """Apps that have at least one membership row."""
    return {
        str(key)
        for key in db.scalars(select(AppMemberRecord.app_key).distinct()).all()
    }
