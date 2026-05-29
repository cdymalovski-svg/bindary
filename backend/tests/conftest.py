"""Session-wide test fixtures.

Two responsibilities:

1. **Auth**: the API now requires a Bearer token on all `/api/*` endpoints
   except `/api/auth/*`, `/api/files/*`, and `/api/pdf-health`. Every test
   module already creates a plain `requests.Session()` and would start
   getting 401s — we don't want to rewrite all of them. Instead we sign in
   ONCE per test session and monkey-patch `requests.Session.request` so
   every outbound request automatically carries the Bearer header.

2. **Cleanup**: after the whole session ends, delete any book with a
   `TEST_` / `TEST ` prefix so the user's library doesn't accumulate
   orphans when tests crash mid-fixture.
"""
from __future__ import annotations

import os

import pytest
import requests


def _api_base() -> str:
    base = os.environ.get("REACT_APP_BACKEND_URL")
    if not base:
        base = "https://manuscript-app-2.preview.emergentagent.com"
    return f"{base.rstrip('/')}/api"


def _login_for_token() -> str | None:
    """Fetch a Bearer token for the seeded admin. Returns None on failure so
    tests still run (they'll just see 401s — easier to debug than a confusing
    fixture error). Reads creds from `.env` directly (preferred) with a
    sensible fallback for local dev."""
    api = _api_base()
    # Read credentials from backend/.env so the tests never lag behind the
    # actual seed values.
    email = os.environ.get("ADMIN_EMAIL")
    password = os.environ.get("ADMIN_PASSWORD")
    if not email or not password:
        try:
            env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("ADMIN_EMAIL="):
                        email = line.split("=", 1)[1].strip().strip('"')
                    elif line.startswith("ADMIN_PASSWORD="):
                        password = line.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass
    if not email or not password:
        return None
    try:
        r = requests.post(
            f"{api}/auth/login",
            json={"email": email, "password": password},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        return r.json().get("access_token")
    except Exception:
        return None


@pytest.fixture(scope="session", autouse=True)
def _authenticate_all_requests():
    """Monkey-patch `requests.Session.request` so every request issued by the
    test suite carries an `Authorization: Bearer <admin-token>` header.

    Why this approach: each test module already defines its own `s` fixture
    that calls `requests.Session()`. Rewriting all nine would be churn and
    invite drift. Patching at the library level keeps tests pristine and
    means new tests "just work" without thinking about auth.
    """
    token = _login_for_token()
    if not token:
        # Test suite runs unauthenticated. Most tests will fail with 401 —
        # that's a clear, actionable signal rather than a silent skip.
        yield
        return

    original_request = requests.Session.request

    def patched_request(self, method, url, **kwargs):
        headers = kwargs.pop("headers", None) or {}
        # Don't trample an explicit override (some tests deliberately send
        # an empty/invalid token to assert 401 behaviour).
        if "Authorization" not in {k.title() for k in headers}:
            headers["Authorization"] = f"Bearer {token}"
        kwargs["headers"] = headers
        return original_request(self, method, url, **kwargs)

    requests.Session.request = patched_request
    try:
        yield
    finally:
        requests.Session.request = original_request


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_books_after_session(_authenticate_all_requests):
    """Defensive teardown: drop any book whose title starts with `TEST_` /
    `TEST ` after the test session ends. Depends on `_authenticate_all_requests`
    so the cleanup DELETE calls still go through after auth is in place."""
    yield
    api = _api_base()
    try:
        # Use a fresh Session so the auth-patched request still applies.
        resp = requests.get(f"{api}/books", timeout=15)
        if resp.status_code != 200:
            return
        for book in resp.json():
            title = (book.get("title") or "").strip()
            if title.startswith("TEST_") or title.startswith("TEST "):
                try:
                    requests.delete(f"{api}/books/{book['id']}", timeout=10)
                except Exception:
                    pass
    except Exception:
        pass
