"""Backend API tests for the job-based PDF export flow.

The browser flow uses three short-lived requests rather than one long-lived
one so custom-domain proxies/CDNs (e.g. Cloudflare on bindery.au) never
see a request that runs longer than their idle/response timeout.

   POST /api/books/{id}/pdf-jobs                → { job_id, status: "pending" }
   GET  /api/books/{id}/pdf-jobs/{j}            → { status: "pending|ready|failed", … }
   GET  /api/books/{id}/pdf-jobs/{j}/download   → application/pdf

URL deliberately avoids `.pdf` because some CDNs treat dot-pdf URLs as
static-file requests and short-circuit them with 404.
"""
import os
import time

import pytest
import requests

BASE_URL = os.environ.get(
    'REACT_APP_BACKEND_URL', 'https://manuscript-app-2.preview.emergentagent.com'
).rstrip('/')
API = f"{BASE_URL}/api"

SMALL_BOOK_ID = "664b1f40-e4d2-40df-874c-2b0ebe6ad645"


@pytest.fixture(scope="module")
def s():
    return requests.Session()


def _wait_for_ready(session, book_id: str, job_id: str, timeout: float = 60.0) -> dict:
    """Poll until status is `ready` or `failed`. Returns the final body."""
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        r = session.get(f"{API}/books/{book_id}/pdf-jobs/{job_id}", timeout=15)
        assert r.status_code == 200, r.text
        last = r.json()
        if last.get("status") in {"ready", "failed"}:
            return last
        time.sleep(0.7)
    pytest.fail(f"Job did not finish in {timeout}s; last status: {last}")


class TestPdfExportJobs:
    def test_start_returns_pending_job(self, s):
        r = s.post(f"{API}/books/{SMALL_BOOK_ID}/pdf-jobs", timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "job_id" in body and len(body["job_id"]) > 10
        assert body["status"] == "pending"

    def test_start_404_for_unknown_book(self, s):
        r = s.post(f"{API}/books/does-not-exist/pdf-jobs", timeout=15)
        assert r.status_code == 404

    def test_status_404_for_unknown_job(self, s):
        r = s.get(f"{API}/books/{SMALL_BOOK_ID}/pdf-jobs/not-a-real-job", timeout=15)
        assert r.status_code == 404

    def test_full_flow_start_poll_download(self, s):
        # 1) start
        start = s.post(f"{API}/books/{SMALL_BOOK_ID}/pdf-jobs", timeout=15)
        assert start.status_code == 200
        job_id = start.json()["job_id"]
        # 2) poll until ready
        final = _wait_for_ready(s, SMALL_BOOK_ID, job_id, timeout=90)
        assert final["status"] == "ready", final
        assert final["size"] > 1000  # any real PDF is well over 1 KB
        assert final["filename"].endswith(".pdf")
        # 3) download
        dl = s.get(f"{API}/books/{SMALL_BOOK_ID}/pdf-jobs/{job_id}/download", timeout=30)
        assert dl.status_code == 200
        assert dl.headers["content-type"].startswith("application/pdf")
        assert dl.content[:5] == b"%PDF-"
        # Content-Disposition uses a safe filename derived from the book title.
        assert "attachment" in dl.headers.get("content-disposition", "")
        # 4) downloading consumes the job — second download should 404
        again = s.get(f"{API}/books/{SMALL_BOOK_ID}/pdf-jobs/{job_id}/download", timeout=10)
        assert again.status_code == 404

    def test_download_409_when_still_pending(self, s):
        # Start a job and immediately try to download — race: it should
        # respond 409 (Conflict) until the worker marks it ready.
        start = s.post(f"{API}/books/{SMALL_BOOK_ID}/pdf-jobs", timeout=15)
        job_id = start.json()["job_id"]
        try:
            dl = s.get(f"{API}/books/{SMALL_BOOK_ID}/pdf-jobs/{job_id}/download", timeout=10)
            # Race-conditional: either still building (409) or already done (200).
            assert dl.status_code in {200, 409}
        finally:
            # Drain the job so it doesn't sit in memory.
            _wait_for_ready(s, SMALL_BOOK_ID, job_id, timeout=90)
            s.get(f"{API}/books/{SMALL_BOOK_ID}/pdf-jobs/{job_id}/download", timeout=30)
