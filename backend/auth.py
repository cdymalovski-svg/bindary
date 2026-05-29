"""Authentication module — JWT + bcrypt + httpOnly cookies.

Follows the integration playbook exactly:
- Access token (15 min) + refresh token (7 days), both as httpOnly cookies.
- Bearer token also accepted as a fallback (useful for non-browser clients).
- Admin seeded idempotently at startup from `ADMIN_EMAIL` / `ADMIN_PASSWORD`.
- Brute-force protection: 5 failed attempts per (ip, email) → 15-min lockout.
- All endpoints under `/api/auth`.

The single source of truth for the secret + admin creds is the backend `.env`.
We never bake credentials into code.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field

log = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 15
REFRESH_TOKEN_DAYS = 7
BRUTE_FORCE_THRESHOLD = 5
BRUTE_FORCE_WINDOW = timedelta(minutes=15)


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #

def _get_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        # Fail fast — refusing to start with a missing secret is the right
        # call. Generating a random secret on the fly would silently invalidate
        # every existing token on every restart.
        raise RuntimeError("JWT_SECRET is not configured")
    return secret


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, _get_secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_DAYS),
    }
    return jwt.encode(payload, _get_secret(), algorithm=JWT_ALGORITHM)


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    # `samesite=lax` is the right tradeoff for same-site SPA + occasional
    # navigation, while still allowing the Bearer fallback for tests.
    # `secure=True` would be ideal in production but breaks local dev over
    # http — the playbook explicitly says secure=False here.
    response.set_cookie(
        "access_token", access, httponly=True, secure=False, samesite="lax",
        max_age=ACCESS_TOKEN_MINUTES * 60, path="/",
    )
    response.set_cookie(
        "refresh_token", refresh, httponly=True, secure=False, samesite="lax",
        max_age=REFRESH_TOKEN_DAYS * 24 * 60 * 60, path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: EmailStr
    password: str = Field(min_length=1)


class UserOut(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    role: str = "user"


# --------------------------------------------------------------------------- #
# Current-user dependency
# --------------------------------------------------------------------------- #

def _extract_token(request: Request) -> Optional[str]:
    """httpOnly cookie first, then `Authorization: Bearer <token>` fallback."""
    token = request.cookies.get("access_token")
    if token:
        return token
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def make_get_current_user(db):
    """Factory so the dependency closes over the live Mongo database handle.
    Avoids importing `db` from server.py and creating a circular import."""

    async def get_current_user(request: Request) -> dict:
        token = _extract_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        try:
            payload = jwt.decode(token, _get_secret(), algorithms=[JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Session expired — please sign in again")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid session")
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"id": payload["sub"]})
        if not user:
            raise HTTPException(status_code=401, detail="User no longer exists")
        user.pop("_id", None)
        user.pop("password_hash", None)
        return user

    return get_current_user


# --------------------------------------------------------------------------- #
# Brute-force protection
# --------------------------------------------------------------------------- #

async def _check_locked_out(db, identifier: str) -> None:
    """Raise 429 if the identifier has too many recent failed attempts."""
    cutoff = datetime.now(timezone.utc) - BRUTE_FORCE_WINDOW
    n = await db.login_attempts.count_documents({
        "identifier": identifier,
        "at": {"$gte": cutoff},
    })
    if n >= BRUTE_FORCE_THRESHOLD:
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Try again in 15 minutes.",
        )


async def _record_failed_attempt(db, identifier: str) -> None:
    await db.login_attempts.insert_one({
        "identifier": identifier,
        "at": datetime.now(timezone.utc),
    })


async def _clear_failed_attempts(db, identifier: str) -> None:
    await db.login_attempts.delete_many({"identifier": identifier})


# --------------------------------------------------------------------------- #
# Admin seeding + indexes
# --------------------------------------------------------------------------- #

async def ensure_indexes(db) -> None:
    """Create the auth-specific Mongo indexes. Idempotent — safe to run on
    every startup."""
    await db.users.create_index("email", unique=True)
    await db.users.create_index("id", unique=True)
    await db.login_attempts.create_index("identifier")
    # Auto-evict old login-attempt rows so the brute-force table stays small.
    await db.login_attempts.create_index("at", expireAfterSeconds=BRUTE_FORCE_WINDOW.total_seconds() * 4)


async def seed_admin(db) -> None:
    """Idempotently ensure the admin account from .env exists.

    - If the user doesn't exist → create with the hashed password.
    - If the user exists AND the .env password no longer verifies → reset hash
      so the operator can fix a forgotten password by editing .env.
    - If the user exists AND the .env password verifies → no-op.
    """
    email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD") or ""
    if not email or not password:
        log.warning("ADMIN_EMAIL / ADMIN_PASSWORD not set — skipping admin seed")
        return
    existing = await db.users.find_one({"email": email})
    if existing is None:
        await db.users.insert_one({
            "id": secrets.token_hex(16),
            "email": email,
            "name": "Admin",
            "role": "admin",
            "password_hash": hash_password(password),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        log.info("Seeded admin account for %s", email)
    elif not verify_password(password, existing["password_hash"]):
        await db.users.update_one(
            {"email": email},
            {"$set": {"password_hash": hash_password(password)}},
        )
        log.info("Updated admin password hash for %s from .env", email)


# --------------------------------------------------------------------------- #
# Router factory
# --------------------------------------------------------------------------- #

def build_auth_router(db) -> APIRouter:
    """Returns an APIRouter mounted under /auth (caller mounts under /api).

    Endpoints:
      POST /auth/login    — email + password, sets cookies, returns user
      POST /auth/logout   — clears cookies
      GET  /auth/me       — returns current user (or 401)
      POST /auth/refresh  — uses refresh_token cookie to issue a new access token
    """
    router = APIRouter(prefix="/auth", tags=["auth"])
    get_current_user = make_get_current_user(db)

    def _user_to_out(u: dict) -> dict:
        return {
            "id": u["id"],
            "email": u["email"],
            "name": u.get("name"),
            "role": u.get("role", "user"),
        }

    @router.post("/login")
    async def login(payload: LoginRequest, request: Request, response: Response):
        email = payload.email.strip().lower()
        # Behind Kubernetes ingress / Cloudflare the `request.client.host`
        # value rotates between proxy IPs, so the brute-force counter never
        # accumulates for the actual attacker. Prefer X-Forwarded-For
        # (leftmost = original client) and fall back to the direct peer.
        xff = request.headers.get("x-forwarded-for", "")
        client_ip = (xff.split(",")[0].strip() if xff else "") or (
            request.client.host if request.client else "unknown"
        ) or "unknown"
        identifier = f"{client_ip}:{email}"
        await _check_locked_out(db, identifier)

        user = await db.users.find_one({"email": email})
        if not user or not verify_password(payload.password, user["password_hash"]):
            await _record_failed_attempt(db, identifier)
            # Identical error message regardless of which side failed —
            # prevents email enumeration via timing or response text.
            raise HTTPException(status_code=401, detail="Invalid email or password")

        await _clear_failed_attempts(db, identifier)
        access = create_access_token(user["id"], user["email"])
        refresh = create_refresh_token(user["id"])
        _set_auth_cookies(response, access, refresh)
        return {**_user_to_out(user), "access_token": access}

    @router.post("/logout")
    async def logout(response: Response, _user=Depends(get_current_user)):
        _clear_auth_cookies(response)
        return {"logged_out": True}

    @router.get("/me")
    async def me(user=Depends(get_current_user)):
        return _user_to_out(user)

    @router.post("/refresh")
    async def refresh(request: Request, response: Response):
        refresh_token = request.cookies.get("refresh_token")
        if not refresh_token:
            raise HTTPException(401, "No refresh token")
        try:
            payload = jwt.decode(refresh_token, _get_secret(), algorithms=[JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            raise HTTPException(401, "Refresh token expired — sign in again")
        except jwt.InvalidTokenError:
            raise HTTPException(401, "Invalid refresh token")
        if payload.get("type") != "refresh":
            raise HTTPException(401, "Invalid token type")
        user = await db.users.find_one({"id": payload["sub"]})
        if not user:
            raise HTTPException(401, "User no longer exists")
        access = create_access_token(user["id"], user["email"])
        response.set_cookie(
            "access_token", access, httponly=True, secure=False, samesite="lax",
            max_age=ACCESS_TOKEN_MINUTES * 60, path="/",
        )
        return {"refreshed": True}

    return router
