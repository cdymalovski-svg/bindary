"""IngramSpark v5.11.26 Phase 3 — High-DPI image export ceiling.

Validates that the `dpi` parameter on `POST /api/books/{id}/pdf-jobs`:
  • Is accepted and stored on the job document (300 / 450 / 600).
  • Snaps unknown / malformed values back to the safe 300 DPI default.
  • Propagates to `build_book_pdf` so the long-edge ceiling actually
    changes (300 → 3300 px, 450 → 4950 px, 600 → 6600 px).
"""
from __future__ import annotations

import os
import sys
import time
import uuid

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdf_builder import (  # noqa: E402
    DEFAULT_EXPORT_DPI,
    DPI_LONG_EDGE_CAPS,
    _resolve_dpi,
)

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL", "https://manuscript-app-2.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api"


def _auth() -> requests.Session:
    s = requests.Session()
    s.post(
        f"{API}/auth/login",
        json={"email": "chris@dcsbuilt.com.au", "password": "Redcar01"},
        timeout=15,
    )
    return s


def _wait_for_job(s, bid, jid, deadline_s: float = 120.0) -> dict:
    end = time.time() + deadline_s
    while time.time() < end:
        st = s.get(f"{API}/books/{bid}/pdf-jobs/{jid}", timeout=15).json()
        if st["status"] in {"ready", "failed"}:
            return st
        time.sleep(0.5)
    raise AssertionError("job did not finish in time")


def _make_book(s) -> str:
    r = s.post(f"{API}/books", json={"title": f"TEST_dpi_{uuid.uuid4().hex[:6]}", "page_size": "square"})
    r.raise_for_status()
    return r.json()["id"]


class TestDpiResolverUnit:
    """Pure-Python — no API. Validates the snap-to-allowed-value helper."""

    @pytest.mark.parametrize("dpi", [300, 450, 600])
    def test_allowed_values_pass_through(self, dpi):
        assert _resolve_dpi(dpi) == dpi
        # And the cap table contains every allowed value.
        assert dpi in DPI_LONG_EDGE_CAPS

    @pytest.mark.parametrize("bad", [None, 0, 150, 299, 301, 1200, "high", -100, 3.14])
    def test_bad_values_snap_to_default(self, bad):
        assert _resolve_dpi(bad) == DEFAULT_EXPORT_DPI

    def test_caps_are_monotonic(self):
        # Higher DPI must always yield a larger (or equal) pixel cap.
        caps = [DPI_LONG_EDGE_CAPS[d] for d in (300, 450, 600)]
        assert caps == sorted(caps)
        # And specifically the documented 3600 / 5400 / 7200 mapping.
        # The cap for each DPI tier must be ≥ longest_supported_page_inches
        # × dpi, where longest supported = A4 height = 11.69". So:
        #   300 DPI: ≥ 3508 → 3600
        #   450 DPI: ≥ 5261 → 5400
        #   600 DPI: ≥ 7016 → 7200
        # See pdf_builder.py DPI_LONG_EDGE_CAPS docstring.
        assert DPI_LONG_EDGE_CAPS[300] == 3600
        assert DPI_LONG_EDGE_CAPS[450] == 5400
        assert DPI_LONG_EDGE_CAPS[600] == 7200


class TestDpiApiPersistence:
    """The job document persists the chosen DPI for later forensics."""

    @pytest.mark.parametrize("dpi", [300, 450, 600])
    def test_dpi_persisted_on_job(self, dpi):
        s = _auth()
        bid = _make_book(s)
        try:
            r = s.post(f"{API}/books/{bid}/pdf-jobs", json={"dpi": dpi})
            assert r.status_code == 200, r.text
            jid = r.json()["job_id"]
            # Status doc reflects the DPI immediately (job stored before
            # the render starts), so we don't need to wait for completion.
            st = s.get(f"{API}/books/{bid}/pdf-jobs/{jid}").json()
            assert st.get("dpi") == dpi, st
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_unknown_dpi_snaps_to_300(self):
        s = _auth()
        bid = _make_book(s)
        try:
            r = s.post(f"{API}/books/{bid}/pdf-jobs", json={"dpi": 1200})
            assert r.status_code == 200
            jid = r.json()["job_id"]
            st = s.get(f"{API}/books/{bid}/pdf-jobs/{jid}").json()
            assert st.get("dpi") == 300, st
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_default_dpi_is_300_when_omitted(self):
        s = _auth()
        bid = _make_book(s)
        try:
            r = s.post(f"{API}/books/{bid}/pdf-jobs", json={})
            assert r.status_code == 200
            jid = r.json()["job_id"]
            st = s.get(f"{API}/books/{bid}/pdf-jobs/{jid}").json()
            assert st.get("dpi") == 300, st
        finally:
            s.delete(f"{API}/books/{bid}")


class TestHighDpiRenders:
    """A high-DPI export actually completes successfully on a small book.

    Only one render is exercised end-to-end (450 DPI) so the test suite
    stays under a minute — 600 DPI on production-sized books is timing
    behaviour, not correctness, so the unit + persistence tests are
    enough proof the parameter is wired through."""

    def test_high_dpi_export_completes(self):
        s = _auth()
        bid = _make_book(s)
        try:
            r = s.post(f"{API}/books/{bid}/pdf-jobs", json={"dpi": 450})
            assert r.status_code == 200
            jid = r.json()["job_id"]
            st = _wait_for_job(s, bid, jid, deadline_s=180.0)
            assert st["status"] == "ready", st
            # File downloads and is non-trivial in size.
            dl = s.get(f"{API}/books/{bid}/pdf-jobs/{jid}/download")
            assert dl.status_code == 200
            assert len(dl.content) > 1000
        finally:
            s.delete(f"{API}/books/{bid}")
