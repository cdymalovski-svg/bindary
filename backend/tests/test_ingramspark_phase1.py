"""Tests for the IngramSpark v5.11.26 Phase 1 compliance work.

Covers:
1. apply_print_boxes() — every page has MediaBox 630×630 pt, BleedBox =
   MediaBox, and per-parity TrimBox positions.
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
        """Every page: MediaBox 630×630, BleedBox = MediaBox, TrimBox alternates."""
        s = _auth()
        bid = _make_book(s, pages=2, page_size="square")
        try:
            reader = _export_and_read(s, bid)
            assert len(reader.pages) == 2
            for i, p in enumerate(reader.pages):
                # MediaBox always 630×630 pt
                mb = p.mediabox
                assert float(mb.width) == 630.0, f"page {i+1} mediabox {float(mb.width)}"
                assert float(mb.height) == 630.0
                # BleedBox = full MediaBox
                bb = p.bleedbox
                assert [float(x) for x in bb] == [0.0, 0.0, 630.0, 630.0]
                # TrimBox: odd (i==0) → [0, 9, 621, 621]; even (i==1) → [9, 9, 630, 621]
                tb = [float(x) for x in p.trimbox]
                if i % 2 == 0:
                    assert tb == [0.0, 9.0, 621.0, 621.0], (i, tb)
                else:
                    assert tb == [9.0, 9.0, 630.0, 621.0], (i, tb)
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
            tb_4 = [float(x) for x in reader.pages[3].trimbox]
            assert tb_4 == [9.0, 9.0, 630.0, 621.0], tb_4
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
