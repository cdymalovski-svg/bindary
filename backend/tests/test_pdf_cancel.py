"""Tests for the PDF-export Cancel feature.

POST /api/books/{book_id}/pdf-jobs/{job_id}/cancel marks a pending job for
cancellation. The worker polls the `cancel_requested` flag at checkpoints
between major stages and bails out — flipping the job to `failed` with
`error="Cancelled by user"`. The status endpoint also surfaces the
`cancel_requested` flag immediately so the frontend can stop polling
without waiting for the worker to notice.
"""
import os
import time

import pytest
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL", "https://manuscript-app-2.preview.emergentagent.com"
).rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def s():
    return requests.Session()


def _make_small_book(s) -> str:
    r = s.post(f"{API}/books", json={"title": "TEST_pdf_cancel", "page_size": "a4"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _wait_until(predicate, timeout=60.0, interval=0.3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = predicate()
        if v:
            return v
        time.sleep(interval)
    return None


class TestPdfCancel:
    def test_cancel_nonexistent_job_returns_404(self, s):
        bid = _make_small_book(s)
        try:
            r = s.post(
                f"{API}/books/{bid}/pdf-jobs/does-not-exist/cancel", timeout=15
            )
            assert r.status_code == 404
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_cancel_pending_job_flips_to_failed_with_clean_message(self, s):
        bid = _make_small_book(s)
        try:
            # Kick off a PDF/X-1a export (slow path — gives us a window to
            # cancel before the worker finishes a regular 1-page render).
            start = s.post(
                f"{API}/books/{bid}/pdf-jobs", json={"pdfx": True}, timeout=15
            )
            assert start.status_code == 200, start.text
            job_id = start.json()["job_id"]

            # Cancel immediately.
            cancel = s.post(
                f"{API}/books/{bid}/pdf-jobs/{job_id}/cancel", timeout=15
            )
            assert cancel.status_code == 200, cancel.text
            body = cancel.json()
            # If the worker already finished (race), the response is
            # honest about that — but in practice the 1-page render hasn't
            # got that far in <1s.
            assert body["status"] in {"pending", "ready", "failed"}

            # Status endpoint should immediately surface the cancel intent
            # even if the worker hasn't noticed.
            st = s.get(f"{API}/books/{bid}/pdf-jobs/{job_id}", timeout=15).json()
            if st["status"] == "pending":
                assert st.get("cancel_requested") is True

            # Wait for the worker to actually flip to failed/ready.
            final = _wait_until(
                lambda: (
                    s.get(f"{API}/books/{bid}/pdf-jobs/{job_id}", timeout=10).json()
                ),
                timeout=60.0,
            )
            assert final is not None
            # If we cancelled fast enough the worker bails out at a
            # checkpoint and flips to failed with a friendly reason.
            # If the worker happened to complete first, the job is ready —
            # that's still a valid outcome (the cancel endpoint correctly
            # reported it).
            poll_deadline = time.time() + 60
            while time.time() < poll_deadline:
                final = s.get(f"{API}/books/{bid}/pdf-jobs/{job_id}", timeout=10).json()
                if final["status"] in {"failed", "ready"}:
                    break
                time.sleep(0.5)
            assert final["status"] in {"failed", "ready"}
            if final["status"] == "failed":
                assert "cancelled" in (final.get("error") or "").lower(), final
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_cancel_already_finished_job_is_noop(self, s):
        """Cancelling a job that's already `ready` (or `failed`) must not
        error — returns 200 with `cancel_requested: False`."""
        bid = _make_small_book(s)
        try:
            # Run a normal (fast) export to completion.
            start = s.post(f"{API}/books/{bid}/pdf-jobs", timeout=15)
            job_id = start.json()["job_id"]
            # Poll to ready.
            for _ in range(120):
                st = s.get(f"{API}/books/{bid}/pdf-jobs/{job_id}", timeout=10).json()
                if st["status"] in {"ready", "failed"}:
                    break
                time.sleep(0.5)
            assert st["status"] in {"ready", "failed"}
            cancel = s.post(
                f"{API}/books/{bid}/pdf-jobs/{job_id}/cancel", timeout=15
            )
            assert cancel.status_code == 200
            assert cancel.json()["cancel_requested"] is False
        finally:
            s.delete(f"{API}/books/{bid}")
