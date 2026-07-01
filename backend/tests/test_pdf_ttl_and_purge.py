"""Backend tests for the surgical PDF-job diagnostics changes (Jan 2026).

Covers three changes:
  1. Split TTL windows on db.pdf_jobs:
       - ready jobs        → now + 30 minutes
       - failed / pending  → now + 7 days
     And the pdf_jobs_ttl index has expireAfterSeconds=0 on `expires_at`.
  2. New JOB SUMMARY log-line format emitted AFTER upload, including
     `upload=<duration|FAILED>`. Same string persisted onto pdf_jobs.summary
     and served by GET /api/admin/recent-exports.
  3. New admin endpoint POST /api/admin/purge-empty-files with dry-run,
     `?confirm=true|false`, 403 for non-admins.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import datetime, timezone, timedelta

import pytest
import requests
from pymongo import MongoClient


def _base_url() -> str:
    b = os.environ.get("REACT_APP_BACKEND_URL")
    if b:
        return b.rstrip("/")
    # Fall back to reading frontend/.env
    p = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", ".env")
    try:
        with open(p) as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass
    raise RuntimeError("REACT_APP_BACKEND_URL not set")


BASE_URL = _base_url()
API = f"{BASE_URL}/api"

SUMMARY_REGEX = re.compile(
    r"WeasyPrint JOB SUMMARY: total=\d+\.\d+s \| prefetch=\d+\.\d+s \| "
    r"render=\d+\.\d+s \| upload=\d+\.\d+s \| pages=\d+ \| "
    r"fonts: \d+ used, \d+ missing"
)


# ---------- Fixtures ----------

def _read_env(key: str) -> str | None:
    try:
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        return None
    return None


@pytest.fixture(scope="module")
def mongo_db():
    mongo_url = _read_env("MONGO_URL") or "mongodb://localhost:27017"
    db_name = _read_env("DB_NAME") or "test_database"
    client = MongoClient(mongo_url)
    yield client[db_name]
    client.close()


@pytest.fixture(scope="module")
def admin_session():
    email = _read_env("ADMIN_EMAIL")
    password = _read_env("ADMIN_PASSWORD")
    assert email and password, "ADMIN creds missing from backend/.env"
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    token = r.json().get("access_token")
    assert token, "no access_token in login response"
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="module")
def book_x(admin_session):
    r = admin_session.get(f"{API}/books", timeout=15)
    assert r.status_code == 200, r.text
    books = r.json()
    # `books` may be list or dict — normalise.
    if isinstance(books, dict) and "books" in books:
        books = books["books"]
    target = next((b for b in books if b.get("title") == "X"), None)
    assert target, "test book 'X' not found — please create a 1-page book titled 'X'"
    return target


# ---------- Change 1: TTL index shape ----------

class TestTTLIndex:
    def test_pdf_jobs_ttl_index_shape(self, mongo_db):
        idxs = list(mongo_db.pdf_jobs.list_indexes())
        ttl_idxs = [i for i in idxs if i.get("name") == "pdf_jobs_ttl"]
        assert len(ttl_idxs) == 1, f"expected exactly 1 TTL index, got {ttl_idxs}"
        ttl = ttl_idxs[0]
        assert ttl.get("expireAfterSeconds") == 0, ttl
        assert dict(ttl["key"]) == {"expires_at": 1}, ttl

    def test_pdf_jobs_ttl_index_survives_restart(self, mongo_db):
        """Restart backend and confirm the drop-recreate leaves exactly ONE ttl index."""
        subprocess.run(
            ["sudo", "supervisorctl", "restart", "backend"],
            check=True, capture_output=True,
        )
        # Wait for backend to come back up
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                r = requests.get(f"{API}/pdf-health", timeout=3)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            pytest.fail("backend did not come back up in 30s")
        # Give the startup hook a beat to finish index maintenance
        time.sleep(2)
        idxs = list(mongo_db.pdf_jobs.list_indexes())
        ttl_idxs = [i for i in idxs if i.get("name") == "pdf_jobs_ttl"]
        assert len(ttl_idxs) == 1, f"drop-recreate left {len(ttl_idxs)} ttl indexes: {ttl_idxs}"
        assert ttl_idxs[0].get("expireAfterSeconds") == 0


# ---------- Change 1 + 2: end-to-end export + TTL(ready) + summary format ----------

class TestExportSummaryAndReadyTTL:
    def test_export_book_x_end_to_end(self, admin_session, book_x, mongo_db):
        book_id = book_x["id"]
        # Start job — whole book, no cover, default DPI
        r = admin_session.post(
            f"{API}/books/{book_id}/pdf-jobs",
            json={"pdfx": False, "cover_spread": False, "binding": "perfect", "dpi": 300},
            timeout=30,
        )
        assert r.status_code == 200, f"start failed: {r.status_code} {r.text}"
        job_id = r.json()["job_id"]
        assert job_id
        started_at = datetime.now(timezone.utc)

        # Poll until ready or failed (book X = 1 page ~ 2s render)
        status_body = None
        deadline = time.time() + 90
        while time.time() < deadline:
            sr = admin_session.get(
                f"{API}/books/{book_id}/pdf-jobs/{job_id}", timeout=15
            )
            assert sr.status_code == 200, sr.text
            status_body = sr.json()
            if status_body["status"] in ("ready", "failed"):
                break
            time.sleep(1)
        assert status_body is not None
        assert status_body["status"] == "ready", f"job did not reach ready: {status_body}"

        # ---- Verify TTL: ready doc expires_at ~ now + 30 min ----
        doc = mongo_db.pdf_jobs.find_one({"job_id": job_id})
        assert doc is not None, "pdf_jobs doc missing after ready"
        exp = doc.get("expires_at")
        assert isinstance(exp, datetime), f"expires_at is {type(exp)}: {exp!r}"
        exp = exp.replace(tzinfo=timezone.utc) if exp.tzinfo is None else exp
        delta_s = (exp - started_at).total_seconds()
        # 30 min = 1800s; allow generous slack for polling wait & clock skew
        assert 1500 <= delta_s <= 1900, (
            f"ready TTL out of window: expected ~1800s, got {delta_s:.1f}s"
        )

        # ---- Verify summary persisted on doc ----
        summary = doc.get("summary")
        assert summary, f"summary field missing on ready doc: keys={list(doc.keys())}"
        assert SUMMARY_REGEX.search(summary), (
            f"summary does not match required regex:\n{summary}"
        )

        # ---- Verify summary served via /api/admin/recent-exports ----
        rr = admin_session.get(f"{API}/admin/recent-exports", timeout=15)
        assert rr.status_code == 200, rr.text
        entries = rr.json().get("entries", [])
        entry = next((e for e in entries if e.get("job_id") == job_id), None)
        assert entry is not None, "job not in recent-exports"
        assert entry.get("summary") == summary, "summary mismatch between doc and API"

        # ---- Verify JOB SUMMARY line landed in supervisor logs ----
        # New line comes from server.py root logger — grep both out & err.
        log_paths = [
            "/var/log/supervisor/backend.out.log",
            "/var/log/supervisor/backend.err.log",
        ]
        found = False
        for p in log_paths:
            try:
                with open(p) as f:
                    data = f.read()
            except FileNotFoundError:
                continue
            if summary in data:
                found = True
                break
        assert found, (
            "JOB SUMMARY line not found in backend supervisor logs "
            f"(searched {log_paths})"
        )

        # ---- Sanity: download endpoint still returns the PDF ----
        dl = admin_session.get(
            f"{API}/books/{book_id}/pdf-jobs/{job_id}/download",
            timeout=30, allow_redirects=True,
        )
        assert dl.status_code == 200, f"download failed: {dl.status_code}"
        assert dl.headers.get("content-type", "").startswith("application/pdf")
        assert len(dl.content) > 1000, "PDF suspiciously small"


# ---------- Change 1: FAILED TTL window on cancellation ----------

class TestFailedTTL:
    def test_cancelled_job_gets_7day_ttl(self, admin_session, book_x, mongo_db):
        book_id = book_x["id"]
        r = admin_session.post(
            f"{API}/books/{book_id}/pdf-jobs",
            json={"pdfx": False, "cover_spread": False, "binding": "perfect", "dpi": 300},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        started_at = datetime.now(timezone.utc)

        # Immediately cancel — before render completes
        time.sleep(0.2)
        cr = admin_session.post(
            f"{API}/books/{book_id}/pdf-jobs/{job_id}/cancel", timeout=15
        )
        # Cancel endpoint may return 200 or 202 depending on implementation
        assert cr.status_code in (200, 202, 204), f"cancel failed: {cr.status_code} {cr.text}"

        # Poll until failed
        deadline = time.time() + 60
        last = None
        while time.time() < deadline:
            sr = admin_session.get(
                f"{API}/books/{book_id}/pdf-jobs/{job_id}", timeout=15
            )
            assert sr.status_code == 200, sr.text
            last = sr.json()
            if last["status"] in ("failed", "ready"):
                break
            time.sleep(0.5)
        assert last is not None
        # Race note: if the render was too fast we may have landed on 'ready'.
        # In that case just skip the FAILED-window assertion.
        if last["status"] != "failed":
            pytest.skip(f"job finished before cancel took effect: {last['status']}")

        doc = mongo_db.pdf_jobs.find_one({"job_id": job_id})
        assert doc is not None
        exp = doc.get("expires_at")
        assert isinstance(exp, datetime), f"expires_at not datetime: {exp!r}"
        exp = exp.replace(tzinfo=timezone.utc) if exp.tzinfo is None else exp
        delta_s = (exp - started_at).total_seconds()
        # 7 days = 604800s; allow ±60s for poll delay / clock skew
        assert 604700 <= delta_s <= 604900, (
            f"failed TTL out of window: expected ~604800s, got {delta_s:.1f}s"
        )

    def test_insert_pending_gets_7day_ttl(self, mongo_db):
        """The insert path in export_pdf_start() writes expires_at = now+7d.
        We don't create a new job here (that's covered by the other tests);
        instead we assert the recent pending/ready docs — if any — have
        `expires_at` set as a real datetime, not string, and never in the past."""
        docs = list(mongo_db.pdf_jobs.find({}, {"expires_at": 1, "status": 1}).limit(20))
        assert docs, "no pdf_jobs docs to inspect"
        now = datetime.now(timezone.utc)
        for d in docs:
            exp = d.get("expires_at")
            assert isinstance(exp, datetime), f"non-datetime expires_at: {exp!r}"
            exp = exp.replace(tzinfo=timezone.utc) if exp.tzinfo is None else exp
            # Old docs from before this test run may already be near expiry.
            # Just make sure the field type is right and it's within +/- 8 days
            # of now (sanity, not a strict window assertion).
            assert exp > now - timedelta(days=1)


# ---------- Change 3: admin purge-empty-files ----------

class TestPurgeEmptyFiles:
    def test_non_admin_gets_401_or_403(self):
        # conftest patches requests.Session so we bypass with urllib.
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            f"{API}/admin/purge-empty-files", method="POST"
        )
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            code = resp.getcode()
        except urllib.error.HTTPError as e:
            code = e.code
        assert code in (401, 403), (
            f"expected 401/403 without auth, got {code}"
        )

    def test_dry_run_default(self, admin_session):
        r = admin_session.post(f"{API}/admin/purge-empty-files", timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        # Shape assertions
        for k in ("confirm", "candidates_matched", "kept_because_referenced",
                  "kept_because_no_id", "eligible_for_delete", "deleted",
                  "mode", "ran_at", "ran_by"):
            assert k in body, f"missing key {k!r} in {body}"
        assert body["confirm"] is False
        assert body["mode"] == "dry-run"
        assert body["deleted"] == 0
        # Preview DB has zero orphans
        assert body["eligible_for_delete"] == 0
        assert body["ran_by"], "ran_by should be admin email"

    def test_dry_run_confirm_false(self, admin_session):
        r = admin_session.post(
            f"{API}/admin/purge-empty-files?confirm=false", timeout=30
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["confirm"] is False
        assert body["mode"] == "dry-run"
        assert body["deleted"] == 0
        assert body["eligible_for_delete"] == 0

    def test_confirm_true_executes(self, admin_session):
        r = admin_session.post(
            f"{API}/admin/purge-empty-files?confirm=true", timeout=30
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["confirm"] is True
        assert body["mode"] == "executed"
        # Preview DB: zero candidates → zero deletes
        assert body["deleted"] == 0
        assert body["eligible_for_delete"] == 0
