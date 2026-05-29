"""End-to-end tests for the JWT auth flow.

These run against the real API. Auth itself can't rely on the global
"auto-authenticate every request" fixture in conftest.py — many of these
tests need to make UNAUTHENTICATED requests, so they pass an explicit
Authorization header (the conftest patch only inserts a header if one
isn't already present).
"""
from __future__ import annotations

import os
import re
import time

import pytest
import requests


def _api() -> str:
    base = os.environ.get("REACT_APP_BACKEND_URL") or "https://manuscript-app-2.preview.emergentagent.com"
    return f"{base.rstrip('/')}/api"


def _admin_creds() -> tuple[str, str]:
    """Read admin creds from .env so tests track real seed values."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    email = os.environ.get("ADMIN_EMAIL")
    password = os.environ.get("ADMIN_PASSWORD")
    if not (email and password):
        with open(env_path) as f:
            for line in f:
                if line.startswith("ADMIN_EMAIL="):
                    email = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("ADMIN_PASSWORD="):
                    password = line.split("=", 1)[1].strip().strip('"')
    return email, password


@pytest.fixture
def api():
    return _api()


def test_login_returns_jwt_and_user_info(api):
    email, password = _admin_creds()
    r = requests.post(
        f"{api}/auth/login",
        json={"email": email, "password": password},
        headers={"Authorization": ""},  # force-empty so conftest doesn't inject
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == email
    assert body["role"] == "admin"
    assert isinstance(body["access_token"], str)
    # JWT shape: header.payload.signature (three base64 segments).
    assert re.match(r"^[\w-]+\.[\w-]+\.[\w-]+$", body["access_token"])


def test_protected_route_rejects_unauthenticated(api):
    r = requests.get(
        f"{api}/books",
        headers={"Authorization": ""},  # explicit empty defeats conftest patch
        timeout=10,
    )
    assert r.status_code == 401
    assert "auth" in r.json()["detail"].lower() or "session" in r.json()["detail"].lower()


def test_protected_route_accepts_bearer_token(api):
    email, password = _admin_creds()
    tok = requests.post(
        f"{api}/auth/login",
        json={"email": email, "password": password},
        headers={"Authorization": ""},
        timeout=15,
    ).json()["access_token"]
    r = requests.get(f"{api}/books", headers={"Authorization": f"Bearer {tok}"}, timeout=15)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_me_returns_current_user(api):
    email, password = _admin_creds()
    tok = requests.post(
        f"{api}/auth/login",
        json={"email": email, "password": password},
        headers={"Authorization": ""},
        timeout=15,
    ).json()["access_token"]
    r = requests.get(f"{api}/auth/me", headers={"Authorization": f"Bearer {tok}"}, timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == email
    assert body["role"] == "admin"
    assert "password_hash" not in body


def test_invalid_password_returns_401(api):
    email, _ = _admin_creds()
    r = requests.post(
        f"{api}/auth/login",
        json={"email": email, "password": "definitely-wrong"},
        headers={"Authorization": ""},
        timeout=10,
    )
    assert r.status_code == 401
    # Generic message — must not reveal whether the email exists (timing or text).
    assert "Invalid email or password" in r.json()["detail"]


def test_unknown_email_returns_401(api):
    r = requests.post(
        f"{api}/auth/login",
        json={"email": "nobody@example.com", "password": "whatever"},
        headers={"Authorization": ""},
        timeout=10,
    )
    assert r.status_code == 401
    assert "Invalid email or password" in r.json()["detail"]


def test_files_endpoint_is_public(api):
    """The /api/files/* path serves image bytes used by both the editor and
    the PDF renderer. Auth on that path would break <img src> tags. A request
    for a non-existent path returns 404/500 from upstream — NOT 401."""
    r = requests.get(
        f"{api}/files/booktemplate/uploads/does-not-exist.png",
        headers={"Authorization": ""},
        timeout=10,
    )
    assert r.status_code != 401, f"/api/files/ should be public; got {r.status_code}"


def test_pdf_health_is_public(api):
    """Operator diagnostic endpoint — accessible without a session."""
    r = requests.get(f"{api}/pdf-health", headers={"Authorization": ""}, timeout=15)
    assert r.status_code == 200
    assert "chromium_launchable" in r.json()


def test_brute_force_lockout_after_repeated_failures(api):
    """5 failed attempts → 429 lockout. Uses a unique email so it doesn't
    poison the real admin's attempt window."""
    bogus_email = f"lockout-{int(time.time())}@example.com"
    for _ in range(5):
        requests.post(
            f"{api}/auth/login",
            json={"email": bogus_email, "password": "wrong"},
            headers={"Authorization": ""},
            timeout=10,
        )
    # 6th attempt should be locked out with 429.
    r = requests.post(
        f"{api}/auth/login",
        json={"email": bogus_email, "password": "wrong"},
        headers={"Authorization": ""},
        timeout=10,
    )
    assert r.status_code == 429
    assert "Too many" in r.json()["detail"]
