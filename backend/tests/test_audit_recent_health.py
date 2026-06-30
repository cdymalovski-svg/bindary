"""Smoke tests for the new admin storage-audit / recent-exports / per-book
file-health endpoints (iteration 14).

Hits the public REACT_APP_BACKEND_URL so we test the same surface the UI does.
"""
import os
import time
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://manuscript-app-2.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "chris@dcsbuilt.com.au"
ADMIN_PASSWORD = "Redcar01"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
               timeout=20)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    tok = r.json().get("access_token")
    assert tok, "no access_token in login response"
    s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


@pytest.fixture(scope="module")
def unauth_session():
    # The session-wide conftest monkey-patches requests.Session.request to
    # inject the admin Bearer token. It respects an EXPLICIT Authorization
    # header though, so passing an empty one disables the auto-auth and
    # lets us hit endpoints anonymously.
    s = requests.Session()
    s.headers.update({"Authorization": ""})
    return s


# -------- Admin Storage Audit --------
class TestStorageAudit:
    def test_audit_returns_fast_and_correct_shape(self, admin_session):
        t0 = time.time()
        r = admin_session.post(f"{BASE_URL}/api/admin/storage-audit", timeout=10)
        elapsed = time.time() - t0
        assert r.status_code == 200, r.text
        assert elapsed < 5.0, f"audit took {elapsed:.2f}s (must be <5s, never timeout)"
        data = r.json()
        # Required keys
        for k in ("needs_reupload", "orphaned", "healthy_count",
                  "total_referenced_paths", "total_file_records", "ran_at", "ran_by"):
            assert k in data, f"missing key {k}"
        assert isinstance(data["needs_reupload"], list)
        assert isinstance(data["orphaned"], list)
        assert isinstance(data["healthy_count"], int)
        assert isinstance(data["total_file_records"], int)
        assert isinstance(data["total_referenced_paths"], int)
        assert data["ran_by"] == ADMIN_EMAIL

    def test_audit_is_idempotent(self, admin_session):
        r1 = admin_session.post(f"{BASE_URL}/api/admin/storage-audit", timeout=10).json()
        r2 = admin_session.post(f"{BASE_URL}/api/admin/storage-audit", timeout=10).json()
        # The counts are deterministic for the same DB state.
        assert r1["total_file_records"] == r2["total_file_records"]
        assert r1["total_referenced_paths"] == r2["total_referenced_paths"]
        assert r1["healthy_count"] == r2["healthy_count"]
        assert len(r1["needs_reupload"]) == len(r2["needs_reupload"])
        assert len(r1["orphaned"]) == len(r2["orphaned"])

    def test_audit_forbidden_for_anonymous(self, unauth_session):
        r = unauth_session.post(f"{BASE_URL}/api/admin/storage-audit",
                                headers={"Authorization": ""}, timeout=10)
        # 401 (no auth) or 403 (auth gate) are both acceptable security boundaries.
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


# -------- Recent Exports --------
class TestRecentExports:
    def test_recent_exports_shape(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/recent-exports", timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("scope") == "cluster-wide"
        assert data.get("max") == 20
        assert isinstance(data.get("entries"), list)
        assert len(data["entries"]) <= 20
        # Each entry should expose the documented fields when present.
        for e in data["entries"]:
            # job_id and status are always present in pdf_jobs
            assert "job_id" in e
            assert "status" in e
            assert e["status"] in ("ready", "failed")

    def test_recent_exports_forbidden_for_anonymous(self, unauth_session):
        r = unauth_session.get(f"{BASE_URL}/api/admin/recent-exports",
                               headers={"Authorization": ""}, timeout=10)
        assert r.status_code in (401, 403)


# -------- Per-book File-Health --------
class TestFileHealth:
    def _pick_book(self, s):
        r = s.get(f"{BASE_URL}/api/books", timeout=10)
        assert r.status_code == 200, r.text
        books = r.json()
        assert isinstance(books, list) and books, "need at least one book"
        return books[0]["id"]

    def test_file_health_clean_book(self, admin_session):
        book_id = self._pick_book(admin_session)
        r = admin_session.get(f"{BASE_URL}/api/books/{book_id}/file-health", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "problems" in data and isinstance(data["problems"], list)
        assert "checked" in data and isinstance(data["checked"], int)
        # Each problem entry has well-defined shape
        for p in data["problems"]:
            assert "issue" in p and p["issue"] in ("missing", "no_dimensions")
            assert "page_no" in p
            assert "filename" in p

    def test_file_health_404_for_unknown_book(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/books/__no_such_book__/file-health", timeout=10)
        assert r.status_code == 404

    def test_file_health_requires_auth(self, unauth_session):
        r = unauth_session.get(f"{BASE_URL}/api/books/anything/file-health",
                               headers={"Authorization": ""}, timeout=10)
        assert r.status_code in (401, 403)
