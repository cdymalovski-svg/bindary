"""Tests for the IngramSpark v5.11.26 Phase 1 compliance work.

Covers:
1. apply_print_boxes() — every page has MediaBox = trim+2×bleed pt,
   BleedBox = MediaBox, and per-parity TrimBox positions.
2. ensure_even_page_count() — odd-paged PDFs get a blank appended with
   the correct parity boxes; even-paged PDFs pass through unchanged.
3. /api/books/{id}/preflight — returns structured warnings for odd page
   counts and low-DPI images, plus a `passed` list for clean books.
"""
from __future__ import annotations

import io
import os
import time
import uuid

import requests
from pypdf import PdfReader

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


def _make_book(s, *, pages: int = 1, page_size: str = "square") -> str:
    r = s.post(
        f"{API}/books",
        json={"title": f"TEST_pf_{uuid.uuid4().hex[:6]}", "page_size": page_size},
        timeout=15,
    )
    bid = r.json()["id"]
    if pages > 1:
        book = s.get(f"{API}/books/{bid}", timeout=15).json()
        # Top up to N pages with blank pages.
        while len(book["pages"]) < pages:
            book["pages"].append(
                {"id": str(uuid.uuid4()), "blocks": [], "background_color": "#FFF8DC"}
            )
        s.put(f"{API}/books/{bid}", json=book, timeout=15)
    return bid


def _export_and_read(s, bid: str) -> PdfReader:
    j = s.post(f"{API}/books/{bid}/pdf-jobs", timeout=15).json()
    jid = j["job_id"]
    for _ in range(180):
        time.sleep(0.5)
        st = s.get(f"{API}/books/{bid}/pdf-jobs/{jid}", timeout=15).json()
        if st["status"] in {"ready", "failed"}:
            break
    assert st["status"] == "ready", st
    dl = s.get(f"{API}/books/{bid}/pdf-jobs/{jid}/download", timeout=30)
    return PdfReader(io.BytesIO(dl.content))


class TestPrintBoxes:
    def test_square_book_has_canonical_ingramspark_boxes(self):
        """Every page: MediaBox = trim+2×bleed, BleedBox = MediaBox, TrimBox alternates."""
        s = _auth()
        bid = _make_book(s, pages=2, page_size="square")
        try:
            reader = _export_and_read(s, bid)
            assert len(reader.pages) == 2
            # Square trim = page_size_px × 0.75 (96 DPI px → pt).
            # 8.75" → 840 px → 630 pt trim. +0.125" bleed (=9 pt) all
            # round → MediaBox 648×648 pt.
            from pdf_builder import PAGE_SIZES_PX, INTERIOR_BLEED_PX
            trim_w_pt = PAGE_SIZES_PX["square"][0] * 0.75
            trim_h_pt = PAGE_SIZES_PX["square"][1] * 0.75
            bleed_pt = INTERIOR_BLEED_PX * 0.75
            media_w_pt = trim_w_pt + 2 * bleed_pt
            media_h_pt = trim_h_pt + 2 * bleed_pt
            for i, p in enumerate(reader.pages):
                # MediaBox always trim+2×bleed (symmetric IngramSpark MediaBox).
                mb = p.mediabox
                assert float(mb.width) == media_w_pt, f"page {i+1} mediabox width {float(mb.width)} vs {media_w_pt}"
                assert float(mb.height) == media_h_pt, f"page {i+1} mediabox height {float(mb.height)} vs {media_h_pt}"
                # BleedBox = full MediaBox.
                bb = p.bleedbox
                assert [float(x) for x in bb] == [0.0, 0.0, media_w_pt, media_h_pt]
                # TrimBox alternates per parity. The cutter cuts here.
                tb = [float(x) for x in p.trimbox]
                if i % 2 == 0:
                    # Recto (right-hand): spine LEFT, bleed extends RIGHT past trim.
                    expected = [0.0, bleed_pt, trim_w_pt + bleed_pt, trim_h_pt + bleed_pt]
                else:
                    # Verso (left-hand): spine RIGHT, bleed extends LEFT of trim.
                    expected = [bleed_pt, bleed_pt, bleed_pt + trim_w_pt + bleed_pt, trim_h_pt + bleed_pt]
                assert tb == expected, (i, tb, expected)
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_odd_paged_book_is_padded_with_correct_parity_blank(self):
        """3-page input → 4-page output where page 4 has even-parity TrimBox."""
        s = _auth()
        bid = _make_book(s, pages=3, page_size="square")
        try:
            reader = _export_and_read(s, bid)
            assert len(reader.pages) == 4, "should auto-pad odd → even"
            # Page 4 must be verso (spine right) since page 3 was recto.
            from pdf_builder import PAGE_SIZES_PX, INTERIOR_BLEED_PX
            trim_w_pt = PAGE_SIZES_PX["square"][0] * 0.75
            trim_h_pt = PAGE_SIZES_PX["square"][1] * 0.75
            bleed_pt = INTERIOR_BLEED_PX * 0.75
            expected = [bleed_pt, bleed_pt, bleed_pt + trim_w_pt + bleed_pt, trim_h_pt + bleed_pt]
            tb_4 = [float(x) for x in reader.pages[3].trimbox]
            assert tb_4 == expected, tb_4
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_even_paged_book_is_unchanged(self):
        s = _auth()
        bid = _make_book(s, pages=4, page_size="square")
        try:
            reader = _export_and_read(s, bid)
            assert len(reader.pages) == 4, "no padding when already even"
        finally:
            s.delete(f"{API}/books/{bid}")


class TestPreflightEndpoint:
    def test_odd_paged_book_returns_parity_warning(self):
        s = _auth()
        bid = _make_book(s, pages=3, page_size="square")
        try:
            pf = s.get(f"{API}/books/{bid}/preflight", timeout=15).json()
            assert pf["status"] == "warnings"
            checks = [w["check"] for w in pf["warnings"]]
            assert "page_count_parity" in checks
            assert pf["page_count"] == 3
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_even_paged_book_returns_ok(self):
        s = _auth()
        bid = _make_book(s, pages=4, page_size="square")
        try:
            pf = s.get(f"{API}/books/{bid}/preflight", timeout=15).json()
            assert pf["status"] == "ok"
            assert pf["warnings"] == []
            assert "inner_margin_0_5in" in pf["passed"]
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_nonexistent_book_404(self):
        s = _auth()
        r = s.get(f"{API}/books/does-not-exist/preflight", timeout=10)
        assert r.status_code == 404
