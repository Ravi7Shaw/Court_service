from __future__ import annotations

import uuid
import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.errors import ApiError
from app.models import EmailVerification, Member, User
from app.notifications import notify_and_queue_email
from app.rate_limit import enforce_rate_limit
from app.security import (create_access_token, hash_password, new_verification_token,
                          token_hash, verify_password)
from app.db import run_tx
from app.dependencies import get_current_user

router = APIRouter(prefix="/api/auth", tags=["authentication"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class VerifyIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


def factory(request: Request):
    return request.app.state.factory


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request) -> dict[str, object]:
    enforce_rate_limit(request, "register", 5, 900)
    normalized = str(body.email).strip().lower()
    enforce_rate_limit(request, "register-email-" + hashlib.sha256(normalized.encode()).hexdigest()[:16], 3, 3600)
    if len(body.password) < 12:
        raise ApiError("weak_password", 422, "password must contain at least 12 characters")
    token = new_verification_token()
    settings = request.app.state.settings

    def work(session: Session) -> User:
        if session.scalar(select(User.id).where(User.email == normalized)):
            raise ApiError("email_unavailable", 409, "an account with this email already exists")
        member = Member(id=uuid.uuid4())
        session.add(member)
        session.flush()
        user = User(member_id=member.id, email=normalized, password_hash=hash_password(body.password),
                    role="member", is_verified=False, is_active=True)
        session.add(user)
        session.flush()
        session.add(EmailVerification(user_id=user.id, token_hash=token_hash(token),
                                      expires_at=datetime.now(UTC) + timedelta(hours=24)))
        verify_url = f"{settings.app_base_url.rstrip('/')}/?verify={token}"
        notify_and_queue_email(session, user, "email_verification", "Verify your email",
                               "Your account was created. Check your email to verify it.",
                               "Verify your Court Service account",
                               f"Welcome! Verify your email within 24 hours: {verify_url}")
        return user

    try:
        user = run_tx(factory(request), work)
    except IntegrityError as exc:
        raise ApiError("email_unavailable", 409, "an account with this email already exists") from exc
    result: dict[str, object] = {"id": str(user.id), "email": user.email,
                                 "message": "Registration created. Verify your email before signing in."}
    if settings.debug_email_tokens:
        result["debug_verification_token"] = token
    return result


@router.post("/verify-email")
def verify_email(body: VerifyIn, request: Request) -> dict[str, str]:
    enforce_rate_limit(request, "verify", 10, 900)

    def work(session: Session) -> None:
        row = session.scalar(select(EmailVerification).where(
            EmailVerification.token_hash == token_hash(body.token),
            EmailVerification.used_at.is_(None),
            EmailVerification.expires_at > datetime.now(UTC),
        ).with_for_update())
        if row is None:
            raise ApiError("invalid_verification_token", 400, "verification link is invalid or expired")
        user = session.get(User, row.user_id)
        if user is None:
            raise ApiError("invalid_verification_token", 400, "verification link is invalid or expired")
        row.used_at = datetime.now(UTC)
        user.is_verified = True

    run_tx(factory(request), work)
    return {"status": "verified", "message": "Email verified. You can now sign in."}


@router.post("/login")
def login(body: LoginIn, request: Request) -> dict[str, object]:
    enforce_rate_limit(request, "login", 10, 900)
    normalized = str(body.email).strip().lower()
    enforce_rate_limit(request, "login-email-" + hashlib.sha256(normalized.encode()).hexdigest()[:16], 5, 900)
    settings = request.app.state.settings

    def work(session: Session) -> User | None:
        return session.scalar(select(User).where(User.email == normalized))

    user = run_tx(factory(request), work)
    if user is None or not verify_password(body.password, user.password_hash):
        raise ApiError("invalid_credentials", 401, "email or password is incorrect")
    if not user.is_active:
        raise ApiError("account_disabled", 403, "this account is disabled")
    if not user.is_verified:
        raise ApiError("email_not_verified", 403, "verify your email before signing in")
    return {"access_token": create_access_token(user.id, settings), "token_type": "bearer",
            "expires_in": settings.jwt_minutes * 60,
            "user": {"id": str(user.id), "email": user.email, "role": user.role}}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict[str, str]:
    return {"id": str(user.id), "member_id": str(user.member_id), "email": user.email, "role": user.role}
