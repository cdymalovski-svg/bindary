"""Iteration-19 regression tests — three targeted bugs:

  Bug 1: render_timings UnboundLocalError on cover-spread path
         (server.py:943 — `render_timings: dict = {}` now defined BEFORE the
         `if cover_spread:` branch).
  Bug 2: new read-only admin diagnostic endpoint
         GET /api/admin/book-diagnose/{book_id}?page_no=&probe_bytes=
         (server.py:2010).
  Bug 3: RecentExportsDialog now uses shared axios `api` client —
         verified separately via UI/network in the frontend test.

Uses the standard `s` (requests.Session) fixture pattern from conftest.py
so every request is auto-authenticated with the admin Bearer token.
"""
from __future__ import annotations

import os
import re
import subprocess
import time

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    raise RuntimeError("REACT_APP_BACKEND_URL not set in env")
API = f"{BASE_URL}/api"

BACKEND_LOG_STDOUT = "/var/log/supervisor/backend.out.log"
BACKEND_LOG_STDERR = "/var/log/supervisor/backend.err.log"


# ---------------- fixtures ----------------
@pytest.fixture(scope="module")
def s() -> requests.Session:
    return requests.Session()


@pytest.fixture(scope="module")
def belinda_book(s: requests.Session) -> dict:
    """Locate the Belinda seed book — 32 pages, has image on page 5."""
    r = s.get(f"{API}/books", timeout=20)
    assert r.status_code == 200, f"/api/books failed: {r.status_code} {r.text[:200]}"
    books = r.json()
    for b in books:
        if "Belinda" in (b.get("title") or ""):
            return b
    pytest.skip(f"Belinda seed book not found. Books: {[b.get('title') for b in books][:5]}")


# ---------------- helpers ----------------
def _poll_job(s: requests.Session, book_id: str, job_id: str, timeout_s: int = 300) -> dict:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        r = s.get(f"{API}/books/{book_id}/pdf-jobs/{job_id}", timeout=20)
        assert r.status_code == 200, f"poll failed: {r.status_code} {r.text[:200]}"
        last = r.json()
        if last.get("status") in ("ready", "failed"):
            return last
        time.sleep(2)
    pytest.fail(f"Job {job_id} did not finish in {timeout_s}s. Last: {last}")


def _tail_log(path: str, n_bytes: int = 200_000) -> str:
    """Read up to the last n_bytes of a log file. Empty string if missing."""
    try:
        sz = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, sz - n_bytes))
            return f.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


# ---------------- BUG 1: cover-spread export succeeds (no UnboundLocalError) ----------------
class TestBug1CoverSpread:
    def test_cover_spread_export_completes(self, s, belinda_book):
        """POST a cover-spread job and confirm it goes to ready — pre-fix this
        crashed with UnboundLocalError on `render_timings`."""
        book_id = belinda_book["id"]
        # Snapshot log tail so we can look at NEW lines only afterwards.
        pre_len = os.path.getsize(BACKEND_LOG_STDERR) if os.path.exists(BACKEND_LOG_STDERR) else 0

        r = s.post(
            f"{API}/books/{book_id}/pdf-jobs",
            json={"cover_spread": True, "binding": "perfect", "spine_width_in": 0.5},
            timeout=30,
        )
        assert r.status_code in (200, 201), f"submit failed: {r.status_code} {r.text[:300]}"
        job = r.json()
        job_id = job.get("id") or job.get("job_id")
        assert job_id, f"no job id in response: {job}"

        final = _poll_job(s, book_id, job_id, timeout_s=420)
        assert final["status"] == "ready", (
            f"cover-spread job did NOT reach ready — status={final.get('status')}, "
            f"error={final.get('error') or final.get('message')}"
        )

        # Confirm NO UnboundLocalError appeared in stderr since we started.
        with open(BACKEND_LOG_STDERR, "rb") as f:
            f.seek(pre_len)
            new_err = f.read().decode("utf-8", errors="replace")
        assert "UnboundLocalError" not in new_err and "render_timings" not in new_err, (
            f"UnboundLocalError leaked into stderr:\n{new_err[-2000:]}"
        )

        # JOB SUMMARY is emitted via logger.info → goes to backend.err.log
        # in this supervisor setup (uvicorn/root logger both write there).
        # For cover-spread, render=n/a and upload=<real> are expected.
        err_tail = _tail_log(BACKEND_LOG_STDERR, 400_000)
        assert "JOB SUMMARY" in err_tail, (
            "No JOB SUMMARY line appeared in backend.err.log after cover-spread job"
        )
        # There must be at least one recent JOB SUMMARY with render=n/a AND
        # upload=<seconds> — the cover-spread signature.
        m = re.search(r"JOB SUMMARY[^\n]*render=n/a[^\n]*upload=\d", err_tail)
        assert m, (
            "Cover-spread JOB SUMMARY (render=n/a, upload=<s>) not found in "
            f"recent stderr. Tail: ...{err_tail[-1500:]}"
        )
        print(f"[cover-spread JOB SUMMARY] {m.group(0)[:400]}")


# ---------------- BUG 1 regression: interior export still emits real render timings ----------------
class TestBug1InteriorRegression:
    def test_regular_interior_export_still_works(self, s, belinda_book):
        book_id = belinda_book["id"]
        r = s.post(f"{API}/books/{book_id}/pdf-jobs", json={}, timeout=30)
        assert r.status_code in (200, 201), f"submit failed: {r.status_code} {r.text[:300]}"
        job_id = r.json().get("id") or r.json().get("job_id")
        assert job_id

        final = _poll_job(s, book_id, job_id, timeout_s=420)
        assert final["status"] == "ready", (
            f"interior export failed: {final.get('status')} / {final.get('error')}"
        )

        # Interior render SHOULD have real timings — JOB SUMMARY appears in
        # backend.err.log with render=<digit>.<digit>s (not n/a).
        err_tail = _tail_log(BACKEND_LOG_STDERR, 400_000)
        assert re.search(r"JOB SUMMARY[^\n]*render=\d", err_tail), (
            "Expected a JOB SUMMARY with render=<seconds> for the interior job; "
            "not found in stderr tail. Recent tail:\n" + err_tail[-2500:]
        )


# ---------------- BUG 2: /api/admin/book-diagnose auth & response contract ----------------
class TestBug2DiagnoseAuth:
    def test_unauth_returns_401(self, belinda_book):
        # Bypass the auth session by using an explicit invalid header —
        # the conftest patch honours an explicit Authorization override.
        r = requests.get(
            f"{API}/admin/book-diagnose/{belinda_book['id']}",
            headers={"Authorization": ""},
            timeout=15,
        )
        assert r.status_code == 401, f"expected 401 unauth, got {r.status_code}: {r.text[:200]}"

    def test_admin_gets_200(self, s, belinda_book):
        r = s.get(f"{API}/admin/book-diagnose/{belinda_book['id']}", timeout=30)
        assert r.status_code == 200, f"admin should get 200, got {r.status_code}: {r.text[:200]}"
        body = r.json()
        # Response contract per spec.
        for k in ("book_id", "title", "total_pages", "probed_bytes", "pages"):
            assert k in body, f"missing key {k!r} in diagnose response: {list(body.keys())}"
        assert body["book_id"] == belinda_book["id"]
        assert body["probed_bytes"] is False
        assert isinstance(body["pages"], list)
        assert body["total_pages"] > 0


class TestBug2DiagnoseFiltering:
    def test_single_page_filter(self, s, belinda_book):
        r = s.get(
            f"{API}/admin/book-diagnose/{belinda_book['id']}",
            params={"page_no": 5},
            timeout=30,
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["pages"]) == 1, f"expected exactly 1 page, got {len(body['pages'])}"
        assert body["pages"][0]["page_no"] == 5
        # Blocks[] should be present with type/geom.
        for blk in body["pages"][0].get("blocks", []):
            assert "type" in blk
            assert "geom" in blk
            if blk["type"] == "image":
                assert "storage_path" in blk
                assert "file_record" in blk  # may be None (orphan)


class TestBug2DiagnoseProbeBytes:
    def test_probe_bytes_true_adds_probe(self, s, belinda_book):
        r = s.get(
            f"{API}/admin/book-diagnose/{belinda_book['id']}",
            params={"page_no": 5, "probe_bytes": "true"},
            timeout=60,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["probed_bytes"] is True
        page = body["pages"][0]
        image_blocks = [b for b in page.get("blocks", []) if b.get("type") == "image"]
        if not image_blocks:
            pytest.skip("Page 5 has no image block — Belinda seed changed?")
        for blk in image_blocks:
            assert "probe" in blk, f"image block missing 'probe' key: {blk}"
            probe = blk["probe"]
            for k in ("reachable", "byte_count", "decode_ok", "actual_width", "actual_height"):
                assert k in probe, f"probe missing {k!r}: {probe}"
            # Belinda page 5 image should be healthy per spec.
            assert probe["reachable"] is True, f"probe not reachable: {probe}"
            assert probe["decode_ok"] is True, f"decode failed: {probe}"
            assert probe["byte_count"] and probe["byte_count"] > 0


class TestBug2DiagnoseReadOnly:
    def test_no_render_or_write_side_effect(self, s, belinda_book):
        """Calling diagnose repeatedly must NOT create any pdf-jobs or mutate
        anything visible via list endpoints."""
        book_id = belinda_book["id"]
        # Snapshot current jobs (if endpoint exists) — otherwise skip that check.
        pre_book = s.get(f"{API}/books/{book_id}", timeout=15).json()
        pre_updated = pre_book.get("updated_at")

        for _ in range(3):
            r = s.get(
                f"{API}/admin/book-diagnose/{book_id}",
                params={"probe_bytes": "true"},
                timeout=60,
            )
            assert r.status_code == 200

        post_book = s.get(f"{API}/books/{book_id}", timeout=15).json()
        # `updated_at` must not have changed as a result of the diagnose calls.
        assert post_book.get("updated_at") == pre_updated, (
            "book.updated_at changed after read-only diagnose calls — endpoint is NOT read-only"
        )
