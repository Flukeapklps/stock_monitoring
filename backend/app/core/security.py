"""Password hashing, JWT sessions and access control dependencies.

Sessions: a signed JWT stored in an HttpOnly, SameSite=Lax cookie (Secure in
production). An `Authorization: Bearer` header is also accepted for API clients.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models import User

COOKIE_NAME = "sm_session"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_access_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {"sub": str(user.id), "role": user.role, "iat": now, "exp": now + timedelta(minutes=s.access_token_minutes), "jti": secrets.token_hex(8)}
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])


def _token_from_request(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _token_from_request(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session") from None
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active or user.role == "line":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required")
    return user


def ensure_admin_user(db: Session) -> User | None:
    """Create the bootstrap admin from ADMIN_EMAIL / ADMIN_PASSWORD if absent."""
    from sqlalchemy import select

    s = get_settings()
    if not s.admin_email or not s.admin_password:
        return None
    user = db.scalar(select(User).where(User.email == s.admin_email.lower()))
    if user:
        return user
    user = User(email=s.admin_email.lower(), password_hash=hash_password(s.admin_password), role="admin", display_name="Administrator")
    db.add(user)
    db.flush()
    return user
