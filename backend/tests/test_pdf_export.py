"""Backend API tests for the new server-side PDF export endpoint.

GET /api/books/{id}/export.pdf — replaces the old client-side html2canvas+jsPDF
exporter. Verifies:
 - 200 + valid PDF magic header for seeded multi-page books with text + images
 - Content-Type and Content-Disposition headers
 - 404 for an unknown book id
 - Reasonable performance (< 15s end-to-end for a 4-page book)
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get(
    'REACT_APP_BACKEND_URL', 'https://manuscript-app-2.preview.emergentagent.com'
).rstrip('/')
API = f"{BASE_URL}/api"

# Books pre-seeded in MongoDB (per main agent's review note).
SMALL_BOOK_ID = "664b1f40-e4d2-40df-874c-2b0ebe6ad645"   # 4 pages, text + images
LARGE_BOOK_ID = "6f8a91b5-a4a3-407b-bd55-1409f335f48b"   # 16 pages — heavier


@pytest.fixture(scope="module")
def s():
    return requests.Session()


class TestPdfExport:
    def test_small_book_pdf_export(self, s):
        """4-page book: valid PDF returned in < 15s with proper headers."""
        # Confirm the book actually exists before asserting PDF behaviour.
        meta = s.get(f"{API}/books/{SMALL_BOOK_ID}")
        if meta.status_code == 404:
            pytest.skip(f"Seed book {SMALL_BOOK_ID} not present in DB")
        assert meta.status_code == 200, meta.text

        t0 = time.time()
        r = s.get(f"{API}/books/{SMALL_BOOK_ID}/export.pdf", timeout=30)
        elapsed = time.time() - t0

        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("application/pdf"), \
            f"Wrong content-type: {r.headers.get('content-type')}"
        # Content-Disposition should mark as attachment with .pdf filename
        cd = r.headers.get("content-disposition", "")
        assert "attachment" in cd.lower(), f"Missing attachment disposition: {cd}"
        assert ".pdf" in cd.lower(), f"Missing .pdf filename: {cd}"
        # PDF magic header
        assert r.content[:5] == b"%PDF-", f"Not a PDF: first bytes={r.content[:8]!r}"
        # Non-empty body
        assert len(r.content) > 1000, f"PDF too small ({len(r.content)} bytes)"
        # PDF should also include the EOF marker
        assert b"%%EOF" in r.content[-1024:], "PDF missing %%EOF trailer"
        # Performance — must be under 15s per the new server-side export SLA
        assert elapsed < 15, f"PDF export too slow: {elapsed:.2f}s"
        print(f"Small-book PDF: {len(r.content)} bytes in {elapsed:.2f}s")

    def test_large_book_pdf_export(self, s):
        """16-page book: stresses image embedding + page-number rules."""
        meta = s.get(f"{API}/books/{LARGE_BOOK_ID}")
        if meta.status_code == 404:
            pytest.skip(f"Seed book {LARGE_BOOK_ID} not present in DB")
        assert meta.status_code == 200

        t0 = time.time()
        r = s.get(f"{API}/books/{LARGE_BOOK_ID}/export.pdf", timeout=60)
        elapsed = time.time() - t0

        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert r.content[:5] == b"%PDF-"
        assert b"%%EOF" in r.content[-1024:]
        # 16 pages with images — should still be reasonable
        assert len(r.content) > 5000
        print(f"Large-book PDF: {len(r.content)} bytes in {elapsed:.2f}s")

    def test_pdf_export_unknown_book_returns_404(self, s):
        bogus = str(uuid.uuid4())
        r = s.get(f"{API}/books/{bogus}/export.pdf")
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"

    def test_pdf_export_for_freshly_created_book(self, s):
        """End-to-end: create → export → delete. Confirms the endpoint works
        for newly minted books (no stale state)."""
        # Create
        c = s.post(f"{API}/books", json={"title": "TEST_PDF_Export", "author": "T", "page_size": "a4"})
        assert c.status_code == 200, c.text
        bid = c.json()["id"]
        try:
            # Add a text block + page-number config via PUT so the exporter has
            # something non-trivial to render.
            book = c.json()
            page = book["pages"][0]
            page["blocks"] = [{
                "id": str(uuid.uuid4()),
                "type": "text",
                "x": 60, "y": 80, "width": 400, "height": 100, "z_index": 1,
                "html": "<p><b>Hello</b> PDF world &mdash; with <i>italics</i>.</p>",
                "font_family": "Cormorant Garamond",
                "font_size": 24,
                "text_align": "center",
                "color": "#222222",
            }]
            page["show_page_number"] = True
            page["full_bleed"] = False
            u = s.put(f"{API}/books/{bid}", json={"pages": [page]})
            assert u.status_code == 200, u.text

            r = s.get(f"{API}/books/{bid}/export.pdf", timeout=30)
            assert r.status_code == 200, r.text
            assert r.content[:5] == b"%PDF-"
            assert b"%%EOF" in r.content[-1024:]
            assert "TEST_PDF_Export" in r.headers.get("content-disposition", "")
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_pdf_export_handles_full_bleed_page(self, s):
        """A full-bleed page must still render (no margin) without erroring."""
        c = s.post(f"{API}/books", json={"title": "TEST_FullBleed", "page_size": "square"})
        assert c.status_code == 200
        bid = c.json()["id"]
        try:
            book = c.json()
            book["pages"][0]["full_bleed"] = True
            book["pages"][0]["background_color"] = "#1a1a1a"  # dark — exercises is_dark_hex path
            book["pages"][0]["show_page_number"] = True
            u = s.put(f"{API}/books/{bid}", json={"pages": book["pages"]})
            assert u.status_code == 200

            r = s.get(f"{API}/books/{bid}/export.pdf", timeout=30)
            assert r.status_code == 200
            assert r.content[:5] == b"%PDF-"
        finally:
            s.delete(f"{API}/books/{bid}")


    def test_pdf_export_embeds_image_blocks(self, s):
        """REGRESSION: the PDF must actually contain image data when the book
        has image blocks. The 2026-05-27 export refactor that switched
        Chromium's `wait_until` from `load` to `domcontentloaded` accidentally
        skipped image loading entirely — chunks finished in ~10s each but the
        resulting PDF contained zero artwork. This test guards that path.

        Strategy: upload a real PNG, create a book that uses it on the cover,
        export to PDF, and assert the PDF body contains BOTH the PDF magic
        header AND at least one embedded image stream (PDF uses the `/Image`
        XObject subtype marker for raster images).
        """
        import io
        # A distinctive 2x2 RGB PNG — small but real bytes that Chromium will
        # actually decode and embed.
        PNG = bytes.fromhex(
            "89504E470D0A1A0A0000000D49484452000000020000000208060000007274"
            "5E8800000016494441547801635CCBC0F03F0303030303030383B0E801001A"
            "0306013F01E1A0F50000000049454E44AE426082"
        )
        # 1) upload
        upload = s.post(
            f"{API}/upload",
            files={"file": ("TEST_pdf_image_regression.png", io.BytesIO(PNG), "image/png")},
        )
        assert upload.status_code == 200, upload.text
        img = upload.json()
        # 2) create a book using the image on page 1
        c = s.post(f"{API}/books", json={"title": "TEST_PDF_Image_Regression", "page_size": "a4"})
        assert c.status_code == 200
        bid = c.json()["id"]
        try:
            book = c.json()
            page = book["pages"][0]
            page["blocks"] = [{
                "id": str(uuid.uuid4()),
                "type": "image",
                "x": 100, "y": 100, "width": 400, "height": 400, "z_index": 1,
                "image_url": img["url"],
                "image_path": img["path"],
            }]
            u = s.put(f"{API}/books/{bid}", json={"pages": [page]})
            assert u.status_code == 200, u.text

            r = s.get(f"{API}/books/{bid}/export.pdf", timeout=60)
            assert r.status_code == 200, r.text
            assert r.content[:5] == b"%PDF-"
            # The actual regression guard: a PDF that rendered with no images
            # would have ZERO `/Subtype /Image` markers. If even one image
            # was successfully fetched + embedded, this matches.
            assert b"/Image" in r.content, (
                "PDF contains no embedded image XObjects — Chromium probably "
                "called page.pdf() before <img> elements finished loading."
            )
        finally:
            s.delete(f"{API}/books/{bid}")
