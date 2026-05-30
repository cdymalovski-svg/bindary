"""Admin user-management + password-change end-to-end tests.

These exercise the new endpoints layered onto the auth module in
iteration 21. They run against the real backend + Mongo and rely on the
session-scoped admin auto-login set up in conftest.py.
"""
from __future__ import annotations

import os
import secrets
import time

import pytest
import requests


def _api() -> str:
    base = os.environ.get("REACT_APP_BACKEND_URL") or "https://manuscript-app-2.preview.emergentagent.com"
    return f"{base.rstrip('/')}/api"


def _admin_creds():
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


def _login(email, password):
    """Return an access_token for the given creds, NOT routed through
    conftest's session-scoped patch (which would inject the admin token
    on top)."""
    r = requests.post(
        f"{_api()}/auth/login",
        json={"email": email, "password": password},
        headers={"Authorization": ""},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


@pytest.fixture
def api():
    return _api()


def _make_unique_email(prefix="testuser"):
    return f"{prefix}-{secrets.token_hex(4)}-{int(time.time())}@example.com"


class TestUserManagement:
    """End-to-end admin CRUD for users."""

    def test_admin_can_list_users(self, s, api):
        r = s.get(f"{api}/auth/users")
        assert r.status_code == 200, r.text
        users = r.json()
        assert isinstance(users, list)
        # The seeded admin must always appear.
        admin_email, _ = _admin_creds()
        assert any(u["email"] == admin_email for u in users)
        # Admins must come first in the listing.
        if users:
            assert users[0]["role"] == "admin"

    def test_non_admin_cannot_list_users(self, s, api):
        # Create a user account, then attempt the list call as them.
        email = _make_unique_email("nonadmin")
        password = "TestPwd_123"
        try:
            create = s.post(f"{api}/auth/users", json={
                "email": email, "password": password, "role": "user",
            })
            assert create.status_code == 200, create.text
            user_token = _login(email, password)
            r = requests.get(
                f"{api}/auth/users",
                headers={"Authorization": f"Bearer {user_token}"},
                timeout=10,
            )
            assert r.status_code == 403
        finally:
            # Cleanup
            users = s.get(f"{api}/auth/users").json()
            target = next((u for u in users if u["email"] == email), None)
            if target:
                s.delete(f"{api}/auth/users/{target['id']}")

    def test_create_then_delete_user(self, s, api):
        email = _make_unique_email("crud")
        r = s.post(f"{api}/auth/users", json={
            "email": email, "password": "Pwd_12345", "name": "Crud Tester", "role": "user",
        })
        assert r.status_code == 200, r.text
        user_id = r.json()["id"]
        assert r.json()["email"] == email
        assert r.json()["role"] == "user"
        # Delete + confirm gone.
        d = s.delete(f"{api}/auth/users/{user_id}")
        assert d.status_code == 200, d.text
        users_after = s.get(f"{api}/auth/users").json()
        assert all(u["id"] != user_id for u in users_after)

    def test_create_user_duplicate_email_rejected(self, s, api):
        email = _make_unique_email("dup")
        r1 = s.post(f"{api}/auth/users", json={
            "email": email, "password": "Pwd_12345", "role": "user",
        })
        assert r1.status_code == 200
        try:
            r2 = s.post(f"{api}/auth/users", json={
                "email": email, "password": "Pwd_other", "role": "user",
            })
            assert r2.status_code == 409
        finally:
            s.delete(f"{api}/auth/users/{r1.json()['id']}")

    def test_admin_cant_delete_self(self, s, api):
        me = s.get(f"{api}/auth/me").json()
        r = s.delete(f"{api}/auth/users/{me['id']}")
        assert r.status_code == 400, r.text

    def test_cant_delete_last_admin(self, s, api):
        # The seeded admin is the only admin in this environment by default.
        # If a test left another admin around, skip — we only care about
        # the "last admin" rule, not the exact count.
        users = s.get(f"{api}/auth/users").json()
        admins = [u for u in users if u["role"] == "admin"]
        if len(admins) > 1:
            pytest.skip("Multiple admins exist; can't test 'last admin' rule")
        me = s.get(f"{api}/auth/me").json()
        # Same as "can't delete self" above, but in case other admin existed
        # then was removed, this would catch that path too.
        r = s.delete(f"{api}/auth/users/{me['id']}")
        assert r.status_code == 400


class TestChangePassword:
    def test_user_can_change_own_password(self, s, api):
        # Create a probe user, log in as them, change their password,
        # verify the new password works and the old one doesn't.
        email = _make_unique_email("chgpw")
        old = "Pwd_old_12345"
        new = "Pwd_NEW_67890"
        created = s.post(f"{api}/auth/users", json={
            "email": email, "password": old, "role": "user",
        })
        user_id = created.json()["id"]
        try:
            tok = _login(email, old)
            r = requests.post(
                f"{api}/auth/change-password",
                headers={"Authorization": f"Bearer {tok}"},
                json={"current_password": old, "new_password": new},
                timeout=10,
            )
            assert r.status_code == 200, r.text

            # Old password must now fail.
            r_old = requests.post(
                f"{api}/auth/login",
                json={"email": email, "password": old},
                headers={"Authorization": ""},
                timeout=10,
            )
            assert r_old.status_code == 401

            # New password must work.
            r_new = requests.post(
                f"{api}/auth/login",
                json={"email": email, "password": new},
                headers={"Authorization": ""},
                timeout=10,
            )
            assert r_new.status_code == 200
        finally:
            s.delete(f"{api}/auth/users/{user_id}")

    def test_change_password_wrong_current_rejected(self, s, api):
        email = _make_unique_email("wrongcur")
        created = s.post(f"{api}/auth/users", json={
            "email": email, "password": "good_pw_12345", "role": "user",
        })
        try:
            tok = _login(email, "good_pw_12345")
            r = requests.post(
                f"{api}/auth/change-password",
                headers={"Authorization": f"Bearer {tok}"},
                json={"current_password": "wrong", "new_password": "new_pw_12345"},
                timeout=10,
            )
            assert r.status_code == 401
        finally:
            s.delete(f"{api}/auth/users/{created.json()['id']}")

    def test_admin_can_reset_anothers_password(self, s, api):
        email = _make_unique_email("adminreset")
        new = "AdminReset_99999"
        created = s.post(f"{api}/auth/users", json={
            "email": email, "password": "initial_12345", "role": "user",
        })
        uid = created.json()["id"]
        try:
            r = s.post(f"{api}/auth/users/{uid}/reset-password", json={
                "new_password": new,
            })
            assert r.status_code == 200, r.text
            # User can sign in with the reset password.
            login = requests.post(
                f"{api}/auth/login",
                json={"email": email, "password": new},
                headers={"Authorization": ""},
                timeout=10,
            )
            assert login.status_code == 200
        finally:
            s.delete(f"{api}/auth/users/{uid}")


@pytest.fixture
def s():
    """Plain session — conftest auto-attaches the admin Bearer token."""
    return requests.Session()
