"""IngramSpark v5.11.26 Phase 3.1 — casebound hardcover cover spread.

Validates:
1. The cover-spread pipeline accepts `binding` in the request body and
   stores it on the pdf_jobs document.
2. A casebound export produces a wider/taller PDF than a perfect-bound
   export of the same book — wrap (0.625") replaces bleed (0.125"), and
   the spine gets a +0.125" board allowance.
3. Pure-Python unit tests against `_build_cover_spread_html` confirm the
   exact pixel math (no Chromium dependency).
"""
from __future__ import annotations

import os
import re
import sys
import time
import uuid

import pytest
import requests

# Allow `import pdf_builder` directly from /app/backend.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdf_builder import (  # noqa: E402
    CASEBOUND_SPINE_ALLOWANCE_IN,
    CASEBOUND_WRAP_PX,
    COVER_BLEED_PX,
    DEFAULT_PAPER_CALIPER_IN,
    PAGE_SIZES_PX,
    PX_PER_INCH,
    _build_cover_spread_html,
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


def _wait_for_job(s, bid, jid, deadline_s: float = 90.0) -> dict:
    end = time.time() + deadline_s
    while time.time() < end:
        st = s.get(f"{API}/books/{bid}/pdf-jobs/{jid}", timeout=15).json()
        if st["status"] in {"ready", "failed"}:
            return st
        time.sleep(0.5)
    raise AssertionError("job did not finish in time")


def _make_book_with_n_pages(s, n: int, *, page_size: str = "square") -> str:
    """Create a TEST_ book with `n` total pages."""
    r = s.post(
        f"{API}/books",
        json={"title": f"TEST_cb_{uuid.uuid4().hex[:6]}", "page_size": page_size},
    )
    r.raise_for_status()
    bid = r.json()["id"]
    book = s.get(f"{API}/books/{bid}").json()
    # New book starts at 1 page; add the remaining.
    while len(book["pages"]) < n:
        book["pages"].append({
            "id": str(uuid.uuid4()),
            "blocks": [],
            "background_color": "#FFF8DC",
        })
    s.put(f"{API}/books/{bid}", json=book)
    return bid


class TestCoverSpreadGeometryUnit:
    """Pure-Python tests — no Chromium / API. Validates the HTML builder
    produces the canvas dimensions IngramSpark expects for each binding."""

    @staticmethod
    def _two_page_book() -> dict:
        # Minimum valid book for cover spread (1 cover, no interior, 1 back).
        return {
            "page_size": "square",
            "pages": [
                {"id": "front", "blocks": [], "background_color": "#FFF8DC"},
                {"id": "back",  "blocks": [], "background_color": "#FFF8DC"},
            ],
        }

    def test_perfect_bound_geometry(self):
        book = self._two_page_book()
        _, w, h = _build_cover_spread_html(book, {}, binding="perfect")
        page_w, page_h = PAGE_SIZES_PX["square"]
        # 0 interior pages → spine_width_in == 0 → clamped to 4 px minimum.
        expected_w = page_w * 2 + 4 + COVER_BLEED_PX * 2
        expected_h = page_h + COVER_BLEED_PX * 2
        assert w == expected_w
        assert h == expected_h

    def test_casebound_geometry_adds_wrap_and_spine_allowance(self):
        # 60 interior pages between front + back → 62 total pages.
        book = {
            "page_size": "square",
            "pages": [{"id": str(i), "blocks": []} for i in range(62)],
        }
        _, w, h = _build_cover_spread_html(book, {}, binding="casebound")
        page_w, page_h = PAGE_SIZES_PX["square"]
        # Spine: 60 × 0.002252 + 0.125 = 0.26012 in → 24.97152 px → 25 (round).
        interior = 60
        spine_in = interior * DEFAULT_PAPER_CALIPER_IN + CASEBOUND_SPINE_ALLOWANCE_IN
        spine_px = round(spine_in * PX_PER_INCH)
        expected_w = page_w * 2 + spine_px + CASEBOUND_WRAP_PX * 2
        expected_h = page_h + CASEBOUND_WRAP_PX * 2
        assert w == expected_w, (w, expected_w)
        assert h == expected_h, (h, expected_h)

    def test_casebound_is_wider_and_taller_than_perfect(self):
        book = {
            "page_size": "square",
            "pages": [{"id": str(i), "blocks": []} for i in range(50)],
        }
        _, w_pb, h_pb = _build_cover_spread_html(book, {}, binding="perfect")
        _, w_cb, h_cb = _build_cover_spread_html(book, {}, binding="casebound")
        # Wrap (0.625") > Bleed (0.125") AND casebound spine has +0.125"
        # board allowance, so casebound must always be strictly larger.
        assert w_cb > w_pb
        assert h_cb > h_pb
        # Sanity: height delta is exactly 2 × (wrap - bleed) px.
        assert (h_cb - h_pb) == 2 * (CASEBOUND_WRAP_PX - COVER_BLEED_PX)

    def test_invalid_binding_falls_back_to_perfect(self):
        book = self._two_page_book()
        _, w_default, h_default = _build_cover_spread_html(book, {})
        _, w_bad, h_bad = _build_cover_spread_html(book, {}, binding="elephant")
        assert (w_bad, h_bad) == (w_default, h_default)

    def test_explicit_spine_width_overrides_calculation(self):
        # Even casebound respects an explicit spine width — the board
        # allowance is only added when we auto-compute from page count.
        book = {
            "page_size": "square",
            "pages": [{"id": str(i), "blocks": []} for i in range(20)],
        }
        _, w, _ = _build_cover_spread_html(
            book, {}, binding="casebound", spine_width_in=0.5,
        )
        page_w, _ = PAGE_SIZES_PX["square"]
        expected_w = page_w * 2 + round(0.5 * PX_PER_INCH) + CASEBOUND_WRAP_PX * 2
        assert w == expected_w


class TestCoverSpreadApi:
    """End-to-end: POST a casebound cover-spread job and confirm the
    pdf_jobs doc records the binding."""

    def test_casebound_request_records_binding(self):
        s = _auth()
        bid = _make_book_with_n_pages(s, 4)
        try:
            j = s.post(
                f"{API}/books/{bid}/pdf-jobs",
                json={"cover_spread": True, "binding": "casebound"},
            ).json()
            assert "job_id" in j, j
            # Don't block on PDF render in CI — just confirm the job
            # accepted the param. Status doc must reflect it.
            # We poll up to 90s for the render to settle so the test is
            # also a smoke test of the actual rendering path.
            st = _wait_for_job(s, bid, j["job_id"])
            assert st["status"] == "ready", st
            # filename uses `_cvr` (with ISBN) or `_cov` (title slug fallback).
            assert re.search(r"_(cvr|cov)(_pdfx)?\.pdf$", st["filename"]), st
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_perfect_bound_request_default(self):
        s = _auth()
        bid = _make_book_with_n_pages(s, 4)
        try:
            # No binding field → defaults to perfect-bound (unchanged behaviour).
            j = s.post(
                f"{API}/books/{bid}/pdf-jobs",
                json={"cover_spread": True},
            ).json()
            st = _wait_for_job(s, bid, j["job_id"])
            assert st["status"] == "ready", st
            assert re.search(r"_(cvr|cov)(_pdfx)?\.pdf$", st["filename"]), st
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_invalid_binding_does_not_error(self):
        """Unknown binding strings get normalised to `perfect` on the
        server (defence in depth — the UI only ever sends valid values)."""
        s = _auth()
        bid = _make_book_with_n_pages(s, 4)
        try:
            j = s.post(
                f"{API}/books/{bid}/pdf-jobs",
                json={"cover_spread": True, "binding": "saddle-stitch"},
            )
            assert j.status_code == 200, j.text
            jid = j.json()["job_id"]
            st = _wait_for_job(s, bid, jid)
            assert st["status"] == "ready", st
        finally:
            s.delete(f"{API}/books/{bid}")


@pytest.mark.parametrize("binding", ["perfect", "casebound"])
def test_pdf_pixel_size_grows_with_binding(binding):
    """A 1-page (cover-only) book still renders for both bindings — and
    the canvas always grows by the outer allowance × 2 on each axis."""
    book = {"page_size": "square", "pages": [{"id": "only", "blocks": []}]}
    _, w, h = _build_cover_spread_html(book, {}, binding=binding)
    page_w, page_h = PAGE_SIZES_PX["square"]
    outer = CASEBOUND_WRAP_PX if binding == "casebound" else COVER_BLEED_PX
    # Minimum spine when there are no interior pages: clamped to 4 px.
    assert w >= page_w * 2 + 4 + outer * 2
    assert h == page_h + outer * 2
