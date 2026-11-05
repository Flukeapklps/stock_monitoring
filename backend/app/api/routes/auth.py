from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.scoring import DEFAULT_WEIGHTS, normalize_weights
from app.core.config import get_settings
from app.core.security import COOKIE_NAME, create_access_token, get_current_user, hash_password, verify_password
from app.db.session import get_db
from app.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=1, max_length=200)


class RegisterIn(BaseModel):
    email: str = Field(max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=10, max_length=200)
    display_name: str | None = Field(None, max_length=100)


class ProfileIn(BaseModel):
    display_name: str | None = Field(None, max_length=100)
    timezone: str | None = None
    score_weights: dict[str, float] | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        if v is not None:
            ZoneInfo(v)
        return v

    @field_validator("score_weights")
    @classmethod
    def _w(cls, v):
        if v is None:
            return v
        if set(v) - set(DEFAULT_WEIGHTS):
            raise ValueError(f"keys must be among {sorted(DEFAULT_WEIGHTS)}")
        if any(x < 0 for x in v.values()) or sum(v.values()) <= 0:
            raise ValueError("weights must be non-negative and sum > 0")
        return v


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


def user_out(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role,
        "timezone": u.timezone,
        "score_weights": normalize_weights(u.score_weights),
        "default_weights": DEFAULT_WEIGHTS,
    }


def _set_cookie(resp: Response, token: str) -> None:
    s = get_settings()
    resp.set_cookie(COOKIE_NAME, token, httponly=True, secure=s.cookie_secure, samesite="lax", max_age=s.access_token_minutes * 60, path="/")


@router.post("/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    user.last_login_at = datetime.now(UTC)
    db.commit()
    token = create_access_token(user)
    _set_cookie(response, token)
    return {"user": user_out(user), "access_token": token, "token_type": "bearer"}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/register")
def register(body: RegisterIn, response: Response, db: Session = Depends(get_db)):
    if not get_settings().allow_registration:
        raise HTTPException(403, "Registration is disabled")
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "Email already registered")
    user = User(email=body.email.lower(), password_hash=hash_password(body.password), display_name=body.display_name, role="user")
    db.add(user)
    db.commit()
    _set_cookie(response, create_access_token(user))
    return {"user": user_out(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_out(user)


@router.put("/me")
def update_me(body: ProfileIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.timezone is not None:
        user.timezone = body.timezone
    if body.score_weights is not None:
        user.score_weights = body.score_weights
    db.commit()
    return user_out(user)


@router.post("/password")
def change_password(body: PasswordIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}
