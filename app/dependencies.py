from __future__ import annotations

import uuid
from fastapi import Header, Request
from sqlalchemy import select
from app.errors import ApiError
from app.models import User
from app.security import decode_access_token


def get_current_user(request: Request, authorization: str | None = Header(default=None)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError("unauthorized", 401, "bearer access token required")
    try:
        claims = decode_access_token(authorization.split(" ", 1)[1], request.app.state.settings)
        user_id = uuid.UUID(claims["sub"])
    except Exception as exc:
        raise ApiError("invalid_token", 401, "access token is invalid or expired") from exc
    with request.app.state.factory() as session:
        user = session.scalar(select(User).where(User.id == user_id))
        if user is None or not user.is_active or not user.is_verified:
            raise ApiError("unauthorized", 401, "account is unavailable or not verified")
        return user


def get_member_id(
    request: Request,
    authorization: str | None = Header(default=None),
    x_member_id: str | None = Header(default=None, alias="X-Member-Id"),
) -> uuid.UUID:
    if authorization and authorization.lower().startswith("bearer "):
        try:
            claims = decode_access_token(authorization.split(" ", 1)[1], request.app.state.settings)
            user_id = uuid.UUID(claims["sub"])
        except Exception as exc:
            raise ApiError("invalid_token", 401, "access token is invalid or expired") from exc
        with request.app.state.factory() as session:
            user = session.get(User, user_id)
            if user is None or not user.is_active or not user.is_verified:
                raise ApiError("unauthorized", 401, "account is unavailable")
            return user.member_id
    if request.app.state.settings.allow_legacy_headers and x_member_id:
        try:
            return uuid.UUID(x_member_id)
        except ValueError as exc:
            raise ApiError("invalid_member", 401, "member identity is invalid") from exc
    raise ApiError("unauthorized", 401, "bearer access token required")
