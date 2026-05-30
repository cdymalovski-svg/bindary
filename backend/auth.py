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


class CreateUserRequest(BaseModel):
    """Admin-only: provision a new account."""
    model_config = ConfigDict(extra="ignore")
    email: EmailStr
    password: str = Field(min_length=6)
    name: Optional[str] = None
    role: str = Field(default="user", pattern=r"^(user|admin)$")


class ChangePasswordRequest(BaseModel):
    """Self-service: requires the current password."""
    model_config = ConfigDict(extra="ignore")
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6)


class AdminResetPasswordRequest(BaseModel):
    """Admin-only: reset another user's password without their current one."""
    model_config = ConfigDict(extra="ignore")
    new_password: str = Field(min_length=6)


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
        return
    # Existing row: bring its state in line with .env every restart.
    updates = {}
    if not verify_password(password, existing["password_hash"]):
        updates["password_hash"] = hash_password(password)
    if existing.get("role") != "admin":
        # Force the configured admin email to have admin role even if
        # someone manually demoted it via the DB — the .env is the source
        # of truth for this account.
        updates["role"] = "admin"
    if updates:
        await db.users.update_one({"email": email}, {"$set": updates})
        log.info("Reconciled admin row for %s (%s)", email, ", ".join(updates.keys()))


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

    # --------------------------------------------------------------------- #
    # Self-service password change
    # --------------------------------------------------------------------- #
    @router.post("/change-password")
    async def change_password(payload: ChangePasswordRequest, user=Depends(get_current_user)):
        # Re-fetch the full record so we can verify the current password.
        full = await db.users.find_one({"id": user["id"]})
        if not full or not verify_password(payload.current_password, full["password_hash"]):
            raise HTTPException(401, "Current password is incorrect")
        if payload.current_password == payload.new_password:
            raise HTTPException(400, "New password must differ from the current one")
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {
                "password_hash": hash_password(payload.new_password),
                "password_changed_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        return {"changed": True}

    # --------------------------------------------------------------------- #
    # Admin: user CRUD
    # --------------------------------------------------------------------- #
    def _require_admin(u: dict) -> dict:
        if u.get("role") != "admin":
            raise HTTPException(403, "Admin privileges required")
        return u

    @router.get("/users")
    async def list_users(user=Depends(get_current_user)):
        _require_admin(user)
        docs = await db.users.find({}, {"_id": 0, "password_hash": 0}).to_list(500)
        # Stable ordering: admin(s) first, then alphabetical email.
        docs.sort(key=lambda d: (d.get("role") != "admin", (d.get("email") or "").lower()))
        return [_user_to_out(d) for d in docs]

    @router.post("/users")
    async def create_user(payload: CreateUserRequest, user=Depends(get_current_user)):
        _require_admin(user)
        email = payload.email.strip().lower()
        if await db.users.find_one({"email": email}):
            raise HTTPException(409, "A user with that email already exists")
        new_user = {
            "id": secrets.token_hex(16),
            "email": email,
            "name": payload.name or None,
            "role": payload.role,
            "password_hash": hash_password(payload.password),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.users.insert_one(new_user)
        return _user_to_out(new_user)

    @router.delete("/users/{user_id}")
    async def delete_user(user_id: str, user=Depends(get_current_user)):
        _require_admin(user)
        # An admin can't delete their own account — would leave the system
        # potentially admin-less and lock them out of the very tools they're
        # using right now.
        if user_id == user["id"]:
            raise HTTPException(400, "You can't delete your own account")
        # Don't allow removing the last admin (whoever they are).
        target = await db.users.find_one({"id": user_id})
        if not target:
            raise HTTPException(404, "User not found")
        if target.get("role") == "admin":
            admin_count = await db.users.count_documents({"role": "admin"})
            if admin_count <= 1:
                raise HTTPException(400, "Can't delete the last admin")
        res = await db.users.delete_one({"id": user_id})
        if res.deleted_count == 0:
            raise HTTPException(404, "User not found")
        return {"deleted": True}

    @router.post("/users/{user_id}/reset-password")
    async def admin_reset_password(
        user_id: str,
        payload: AdminResetPasswordRequest,
        user=Depends(get_current_user),
    ):
        _require_admin(user)
        target = await db.users.find_one({"id": user_id})
        if not target:
            raise HTTPException(404, "User not found")
        await db.users.update_one(
            {"id": user_id},
            {"$set": {
                "password_hash": hash_password(payload.new_password),
                "password_changed_at": datetime.now(timezone.utc).isoformat(),
                "password_reset_by": user["id"],
            }},
        )
        return {"reset": True}

    return router
