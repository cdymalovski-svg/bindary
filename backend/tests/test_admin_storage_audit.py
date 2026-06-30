"""Tests for the admin Storage Audit + Recent Exports endpoints.

The Storage Audit was rewritten to be **pure MongoDB queries** —
no object-storage I/O, no Pillow decode. The old test that exercised
the Pillow + S3 path is obsolete and has been replaced by tests for
the new DB-only categorisation (`needs_reupload` / `orphaned` /
`healthy_count`).

Covers:
  - POST /api/admin/storage-audit returns 403 for a non-admin user.
  - POST /api/admin/storage-audit returns 200 for admin with the
    documented response shape.
  - The audit correctly categorises synthetic rows into
    needs_reupload (no width_px/height_px) and orphaned (referenced
    by a book but no db.files row).
  - The audit is read-only — no rows are deleted.
  - GET /api/admin/recent-exports returns 403 for non-admin, 200 for
    admin, with the documented shape (cluster-wide via db.pdf_jobs).

Non-admin user is created by direct DB insert + bcrypt-hashed
password, then we sign in via the public auth flow. Cleaned up in
teardown.
"""
from __future__ import annotations

import os
import uuid

import bcrypt
import pytest
import requests
from datetime import datetime, timezone
from pymongo import MongoClient

from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL", "https://manuscript-app-2.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api"

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.ok, f"login failed for {email}: {r.status_code} {r.text}"
    token = r.json().get("access_token")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


@pytest.fixture(scope="module")
def admin_session() -> requests.Session:
    return _login("chris@dcsbuilt.com.au", "Redcar01")


@pytest.fixture
def non_admin_user():
    """Create a temporary non-admin user via direct DB insert (the API
    doesn't expose public registration). Yields the credentials; teardown
    removes the user from db.users."""
    email = f"test_nonadmin_{uuid.uuid4().hex[:6]}@example.com"
    password = "TestPass!2026"
    user_id = str(uuid.uuid4())

    c = MongoClient(MONGO_URL)
    db = c[DB_NAME]
    db.users.insert_one({
        "id": user_id,
        "email": email,
        "name": "Test Non-Admin",
        "role": "user",
        "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        "created_at": datetime.now(timezone.utc),
    })
    yield {"email": email, "password": password, "id": user_id}
    db.users.delete_one({"id": user_id})
    c.close()


class TestStorageAuditAccessControl:
    def test_non_admin_user_gets_403(self, non_admin_user):
        s = _login(non_admin_user["email"], non_admin_user["password"])
        r = s.post(f"{API}/admin/storage-audit", timeout=20)
        assert r.status_code == 403, f"expected 403 for non-admin, got {r.status_code}: {r.text}"

    def test_admin_user_gets_200_with_documented_shape(self, admin_session):
        r = admin_session.post(f"{API}/admin/storage-audit", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        # Documented contract — every key the frontend reads must be
        # present even when empty.
        for k in (
            "needs_reupload", "orphaned", "healthy_count",
            "total_referenced_paths", "total_file_records", "ran_at", "ran_by",
        ):
            assert k in body, f"missing key {k!r} in response: {list(body.keys())}"
        assert isinstance(body["needs_reupload"], list)
        assert isinstance(body["orphaned"], list)
        assert isinstance(body["healthy_count"], int)
        assert body["ran_by"] == "chris@dcsbuilt.com.au"


class TestStorageAuditClassification:
    """Insert synthetic db.files rows + a book that references them,
    then verify the audit categorises them into needs_reupload /
    orphaned correctly. Pure DB — no S3 traffic — so the test is fast
    and deterministic."""

    def test_audit_categorises_synthetic_rows(self, admin_session):
        c = MongoClient(MONGO_URL)
        db = c[DB_NAME]

        # 1. needs_reupload — a db.files row with NO width_px/height_px.
        no_dim_id = str(uuid.uuid4())
        no_dim_path = f"bindery/uploads/AUDIT_no_dim_{uuid.uuid4().hex[:8]}.png"
        db.files.insert_one({
            "id": no_dim_id,
            "storage_path": no_dim_path,
            "original_filename": "audit_no_dim.png",
            "content_type": "image/png",
            "size": 0,
            "is_deleted": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
            # width_px / height_px deliberately omitted.
        })

        # 2. orphaned — a book references a path with NO db.files row.
        orphan_path = f"bindery/uploads/AUDIT_orphan_{uuid.uuid4().hex[:8]}.png"

        book_id = str(uuid.uuid4())
        book_title = f"TEST_audit_book_{uuid.uuid4().hex[:6]}"
        db.books.insert_one({
            "id": book_id,
            "title": book_title,
            "author": "Audit Test",
            "pages": [
                {
                    "id": str(uuid.uuid4()),
                    "blocks": [
                        {
                            "id": str(uuid.uuid4()),
                            "type": "image",
                            "image_path": no_dim_path,
                            "x": 0, "y": 0, "width": 100, "height": 100,
                        },
                        {
                            "id": str(uuid.uuid4()),
                            "type": "image",
                            "image_path": orphan_path,
                            "x": 0, "y": 0, "width": 100, "height": 100,
                        },
                    ],
                },
            ],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        try:
            r = admin_session.post(f"{API}/admin/storage-audit", timeout=30)
            assert r.ok, r.text
            report = r.json()

            # needs_reupload — our no_dim file should be there with the
            # book title + page_no cross-reference attached.
            needs_paths = [f["storage_path"] for f in report["needs_reupload"]]
            assert no_dim_path in needs_paths, (
                f"no-dim file not in needs_reupload. paths={needs_paths[:5]}…"
            )
            entry = next(f for f in report["needs_reupload"] if f["storage_path"] == no_dim_path)
            assert entry["book_title"] == book_title
            assert entry["page_no"] == 1

            # orphaned — our orphan path should be flagged.
            orphan_paths = [f["storage_path"] for f in report["orphaned"]]
            assert orphan_path in orphan_paths, (
                f"orphan path not surfaced. orphaned={orphan_paths[:5]}…"
            )
            orphan_entry = next(f for f in report["orphaned"] if f["storage_path"] == orphan_path)
            assert orphan_entry["book_title"] == book_title
            assert orphan_entry["page_no"] == 1

            # Read-only invariant — never deletes a row.
            assert db.files.find_one({"id": no_dim_id}) is not None, (
                "audit deleted a db.files row — must be read-only"
            )
        finally:
            db.books.delete_one({"id": book_id})
            db.files.delete_one({"id": no_dim_id})
            c.close()


class TestRecentExportsAccessControl:
    def test_non_admin_gets_403(self, non_admin_user):
        s = _login(non_admin_user["email"], non_admin_user["password"])
        r = s.get(f"{API}/admin/recent-exports", timeout=15)
        assert r.status_code == 403, r.text

    def test_admin_gets_200_with_documented_shape(self, admin_session):
        r = admin_session.get(f"{API}/admin/recent-exports", timeout=15)
        assert r.ok, r.text
        body = r.json()
        for k in ("scope", "max", "entries"):
            assert k in body, f"missing {k!r}: {list(body.keys())}"
        assert isinstance(body["entries"], list)
        # Backed by db.pdf_jobs — cluster-wide visibility.
        assert body["scope"] == "cluster-wide"
        assert body["max"] == 20


class TestFileHealthEndpoint:
    """Per-book pre-export check — accessible to any logged-in user.
    Returns the same `{problems, checked}` shape the export popover
    consumes to drive the warning dialog."""

    def test_unknown_book_returns_404(self, admin_session):
        r = admin_session.get(
            f"{API}/books/does-not-exist-{uuid.uuid4().hex}/file-health",
            timeout=15,
        )
        assert r.status_code == 404, r.text

    def test_book_with_no_image_blocks_returns_empty(self, admin_session):
        c = MongoClient(MONGO_URL)
        db = c[DB_NAME]
        book_id = str(uuid.uuid4())
        db.books.insert_one({
            "id": book_id,
            "title": f"TEST_filehealth_empty_{uuid.uuid4().hex[:6]}",
            "author": "FH",
            "pages": [{"id": str(uuid.uuid4()), "blocks": []}],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        try:
            r = admin_session.get(f"{API}/books/{book_id}/file-health", timeout=15)
            assert r.ok, r.text
            body = r.json()
            assert body["problems"] == []
            assert body["checked"] == 0
        finally:
            db.books.delete_one({"id": book_id})
            c.close()

    def test_book_with_missing_and_no_dim_images(self, admin_session):
        c = MongoClient(MONGO_URL)
        db = c[DB_NAME]

        missing_path = f"bindery/uploads/TEST_fh_missing_{uuid.uuid4().hex[:8]}.png"
        no_dim_path = f"bindery/uploads/TEST_fh_no_dim_{uuid.uuid4().hex[:8]}.png"
        no_dim_id = str(uuid.uuid4())
        db.files.insert_one({
            "id": no_dim_id,
            "storage_path": no_dim_path,
            "original_filename": "fh_no_dim.png",
            "content_type": "image/png",
            "size": 0,
            "is_deleted": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        book_id = str(uuid.uuid4())
        db.books.insert_one({
            "id": book_id,
            "title": f"TEST_filehealth_{uuid.uuid4().hex[:6]}",
            "author": "FH",
            "pages": [
                {
                    "id": str(uuid.uuid4()),
                    "blocks": [
                        {
                            "id": str(uuid.uuid4()),
                            "type": "image",
                            "image_path": missing_path,
                            "x": 0, "y": 0, "width": 100, "height": 100,
                        },
                    ],
                },
                {
                    "id": str(uuid.uuid4()),
                    "blocks": [
                        {
                            "id": str(uuid.uuid4()),
                            "type": "image",
                            "image_path": no_dim_path,
                            "x": 0, "y": 0, "width": 100, "height": 100,
                        },
                    ],
                },
            ],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        try:
            r = admin_session.get(f"{API}/books/{book_id}/file-health", timeout=15)
            assert r.ok, r.text
            body = r.json()
            assert body["checked"] == 2
            issues_by_path = {p["storage_path"]: p["issue"] for p in body["problems"]}
            assert issues_by_path.get(missing_path) == "missing"
            assert issues_by_path.get(no_dim_path) == "no_dimensions"
            # page_no is 1-indexed; we placed missing on page 1, no_dim on page 2.
            by_path_pageno = {p["storage_path"]: p["page_no"] for p in body["problems"]}
            assert by_path_pageno.get(missing_path) == 1
            assert by_path_pageno.get(no_dim_path) == 2
        finally:
            db.books.delete_one({"id": book_id})
            db.files.delete_one({"id": no_dim_id})
            c.close()
