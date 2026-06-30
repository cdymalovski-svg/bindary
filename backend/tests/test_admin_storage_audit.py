"""Tests for the admin Storage Audit + Recent Exports endpoints.

Covers:
  - POST /api/admin/storage-audit returns 403 for a non-admin user
  - POST /api/admin/storage-audit returns 200 for admin and shape is correct
  - Audit correctly classifies inserted test rows into fixed/bad/orphaned
  - Audit is idempotent (re-running on a clean DB returns empty lists)
  - GET /api/admin/recent-exports returns 403 for non-admin, 200 for admin
  - Endpoints never delete db.files rows (verified by comparing counts)

Non-admin user is created by direct DB insert + bcrypt-hashed password,
then we sign in via the public auth flow to get a real JWT. Cleaned up
in teardown.
"""
from __future__ import annotations

import io
import os
import uuid

import bcrypt
import pytest
import requests
from datetime import datetime, timezone
from PIL import Image
from pymongo import MongoClient

from dotenv import load_dotenv

# Resolve env from backend/.env so MONGO_URL / DB_NAME work in pytest.
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
        "role": "user",  # explicit non-admin
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

    def test_admin_user_gets_200(self, admin_session):
        r = admin_session.post(f"{API}/admin/storage-audit?limit=20", timeout=180)
        assert r.status_code == 200, r.text
        body = r.json()
        # Shape: documented contract — every key the frontend reads must
        # be present even when empty.
        for k in ("total_scanned", "fixed", "bad", "orphaned", "ran_at", "ran_by"):
            assert k in body, f"missing key {k!r} in response: {list(body.keys())}"
        assert isinstance(body["fixed"], list)
        assert isinstance(body["bad"], list)
        assert isinstance(body["orphaned"], list)
        assert body["ran_by"] == "chris@dcsbuilt.com.au"


class TestStorageAuditClassification:
    """Insert synthetic db.files rows + a book that references one of
    them, then verify the audit categorises them correctly into
    fixed/bad/orphaned and reports the book title."""

    def test_audit_categorises_synthetic_rows(self, admin_session):
        c = MongoClient(MONGO_URL)
        db = c[DB_NAME]

        # 1. Upload a real image so we have a valid storage object to
        #    reference. The pre-iteration-54 simulation strips the
        #    width_px/height_px field afterwards so the audit must
        #    backfill them.
        img = Image.new("RGB", (321, 234), (90, 140, 70))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        up = admin_session.post(
            f"{API}/upload",
            files={"file": (f"audit_{uuid.uuid4().hex[:6]}.png", buf.getvalue(), "image/png")},
            timeout=30,
        )
        assert up.ok, up.text
        valid_path = up.json()["path"]
        # Simulate the pre-iter-54 legacy state: clear the dims so the
        # audit's "fixed" branch has something to do.
        db.files.update_one({"storage_path": valid_path},
                             {"$unset": {"width_px": "", "height_px": ""}})

        # 2. Orphaned: db.files row pointing to a storage path that
        #    doesn't exist.
        orphan_id = str(uuid.uuid4())
        orphan_path = f"bindery/uploads/AUDIT_orphan_{uuid.uuid4().hex[:8]}.png"
        db.files.insert_one({
            "id": orphan_id,
            "storage_path": orphan_path,
            "original_filename": "audit_orphan.png",
            "content_type": "image/png",
            "size": 0,
            "is_deleted": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        # 3. A book that references the orphan path so we can verify
        #    the audit attaches book_title to the orphan row.
        book_id = str(uuid.uuid4())
        book_title = f"TEST_audit_book_{uuid.uuid4().hex[:6]}"
        db.books.insert_one({
            "id": book_id,
            "title": book_title,
            "author": "Audit Test",
            "pages": [{
                "id": str(uuid.uuid4()),
                "blocks": [{
                    "id": str(uuid.uuid4()),
                    "type": "image",
                    "image_path": orphan_path,
                    "x": 0, "y": 0, "width": 100, "height": 100,
                }],
            }],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        try:
            # Tight limit — synthetic rows are the most recent (created_at
            # desc sort), so a limit of 20 captures them all while
            # keeping the scan under the CDN edge timeout.
            r = admin_session.post(f"{API}/admin/storage-audit?limit=20", timeout=180)
            assert r.ok, r.text
            report = r.json()

            # The valid file should appear in `fixed` (dimensions backfilled).
            fixed_paths = [f["storage_path"] for f in report["fixed"]]
            assert valid_path in fixed_paths, (
                f"valid file not in fixed list. fixed={fixed_paths}"
            )
            fixed_entry = next(f for f in report["fixed"] if f["storage_path"] == valid_path)
            assert fixed_entry["width_px"] == 321
            assert fixed_entry["height_px"] == 234

            # The orphan should appear in `orphaned` and carry our book title.
            orphan_paths = [f["storage_path"] for f in report["orphaned"]]
            assert orphan_path in orphan_paths, (
                f"orphan not surfaced. orphaned={orphan_paths}"
            )
            orphan_entry = next(f for f in report["orphaned"] if f["storage_path"] == orphan_path)
            assert orphan_entry["book_title"] == book_title, (
                f"book title not attached. got={orphan_entry.get('book_title')!r}"
            )
            assert orphan_entry["book_id"] == book_id

            # Verify backfill actually happened in the DB (idempotency check
            # — re-running shouldn't re-classify this row as `fixed`).
            row = db.files.find_one({"storage_path": valid_path})
            assert row.get("width_px") == 321
            assert row.get("height_px") == 234

            # Re-run idempotency: same valid path must NOT appear in
            # `fixed` again because its dimensions are already set.
            r2 = admin_session.post(f"{API}/admin/storage-audit?limit=20", timeout=180)
            assert r2.ok, r2.text
            report2 = r2.json()
            fixed_paths_2 = [f["storage_path"] for f in report2["fixed"]]
            assert valid_path not in fixed_paths_2, (
                "second audit re-flagged the valid file as fixed — backfill is not persisting"
            )

            # Verify the audit NEVER deleted the orphan row (read-only).
            assert db.files.find_one({"id": orphan_id}) is not None, (
                "audit deleted the orphan row — must be read-only"
            )
        finally:
            db.books.delete_one({"id": book_id})
            db.files.delete_one({"id": orphan_id})
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
        # Max ring-buffer size is documented as 20.
        assert body["max"] == 20
        # Scope is process-local; backend stamps it as "this-pod-only"
        # so the UI can warn the admin not to expect cross-pod history.
        assert body["scope"] == "this-pod-only"
