from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import Settings

_passwords = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)


def hash_password(password: str) -> str:
    return _passwords.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return _passwords.verify(encoded, password)
    except VerifyMismatchError:
        return False


def create_access_token(user_id: uuid.UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_minutes),
            "jti": secrets.token_urlsafe(12),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer=settings.jwt_issuer,
                      options={"require": ["sub", "iss", "iat", "exp", "jti"]})


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_verification_token() -> str:
    return secrets.token_urlsafe(32)
