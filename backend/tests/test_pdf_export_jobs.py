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



class TestPdfExportRangeAndHistory:
    """Range exports + history bookkeeping. Confirms that a partial export
    actually produces a PDF for just those pages, that the history endpoint
    records what was downloaded, and that bad ranges are rejected with 4xx."""

    def _make_book_with_pages(self, s, n_pages: int):
        """Create a book and grow it to `n_pages` pages via duplicate-add."""
        c = s.post(f"{API}/books", json={"title": "TEST_range_export", "page_size": "a4"})
        assert c.status_code == 200, c.text
        book = c.json()
        bid = book["id"]
        # The book starts with 1 page; append until we hit n_pages.
        pages = book["pages"]
        while len(pages) < n_pages:
            import uuid as _uuid
            pages.append({
                "id": str(_uuid.uuid4()),
                "blocks": [{
                    "id": str(_uuid.uuid4()),
                    "type": "text",
                    "x": 60, "y": 60, "width": 400, "height": 80, "z_index": 1,
                    "html": f"<p>Page {len(pages) + 1}</p>",
                    "font_family": "Cormorant Garamond",
                    "font_size": 24, "text_align": "left", "color": "#222",
                }],
                "background_color": "#FFF8DC",
                "show_page_number": True,
            })
        u = s.put(f"{API}/books/{bid}", json={"pages": pages})
        assert u.status_code == 200, u.text
        return bid

    def test_range_export_writes_only_those_pages(self, s):
        bid = self._make_book_with_pages(s, 6)
        try:
            # Export pages 2-4 (3 pages total)
            start = s.post(
                f"{API}/books/{bid}/pdf-jobs",
                json={"start_page": 2, "end_page": 4},
                timeout=15,
            )
            assert start.status_code == 200, start.text
            job_id = start.json()["job_id"]
            final = _wait_for_ready(s, bid, job_id, timeout=90)
            assert final["status"] == "ready", final
            assert final["start_page"] == 2
            assert final["end_page"] == 4
            # Filename should include the range so disks don't collide.
            assert "_pp_2-4" in final["filename"], final["filename"]
            dl = s.get(f"{API}/books/{bid}/pdf-jobs/{job_id}/download", timeout=30)
            assert dl.status_code == 200
            assert dl.content[:5] == b"%PDF-"
            # Authoritative page count via pypdf (already a backend dep).
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(dl.content))
            assert len(reader.pages) == 3, f"Expected 3 pages, got {len(reader.pages)}"
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_range_export_preserves_original_page_numbers(self, s):
        """Critical: when exporting pages 3-5 of a 7-page book, the rendered
        page numbers must show 3, 4, 5 — NOT 1, 2, 3. This is what users
        need when stitching big books in chunks; they want the page numbers
        to line up with the original book numbering across all the chunks.
        """
        bid = self._make_book_with_pages(s, 7)
        try:
            start = s.post(
                f"{API}/books/{bid}/pdf-jobs",
                json={"start_page": 3, "end_page": 5},
                timeout=15,
            )
            assert start.status_code == 200, start.text
            job_id = start.json()["job_id"]
            _wait_for_ready(s, bid, job_id, timeout=90)
            dl = s.get(f"{API}/books/{bid}/pdf-jobs/{job_id}/download", timeout=30)
            assert dl.status_code == 200

            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(dl.content))
            assert len(reader.pages) == 3, f"Expected 3 pages, got {len(reader.pages)}"

            # Extract text from each rendered PDF page and check the printed
            # page number matches the ORIGINAL book position (3, 4, 5) — not
            # the within-slice position (1, 2, 3). `extract_text` is best-
            # effort but reliable enough for short numeric strings.
            extracted = [p.extract_text() or "" for p in reader.pages]
            expected_numbers = ["3", "4", "5"]
            for idx, (page_text, expected) in enumerate(zip(extracted, expected_numbers)):
                assert expected in page_text, (
                    f"Rendered PDF page {idx + 1} should show original page "
                    f"number {expected!r}, got text: {page_text!r}"
                )
            # Sanity: 1 and 2 must NOT appear as standalone tokens on the
            # rendered pages (they'd indicate slice-relative numbering).
            for page_text in extracted:
                tokens = page_text.split()
                # The "Page N" body text on page 3 says "Page 3" — '3' appears.
                # We just check that NEITHER '1' NOR '2' is a standalone token.
                assert "1" not in tokens, (
                    f"Slice-relative numbering leaked: '1' appeared in {page_text!r}"
                )
                assert "2" not in tokens, (
                    f"Slice-relative numbering leaked: '2' appeared in {page_text!r}"
                )
        finally:
            s.delete(f"{API}/books/{bid}")


    def test_range_export_appears_in_history(self, s):
        bid = self._make_book_with_pages(s, 4)
        try:
            # Empty history to start.
            r0 = s.get(f"{API}/books/{bid}/exports", timeout=10)
            assert r0.status_code == 200
            assert r0.json() == []

            # Export pages 1-2 and download to record history.
            start = s.post(
                f"{API}/books/{bid}/pdf-jobs",
                json={"start_page": 1, "end_page": 2},
                timeout=15,
            )
            job_id = start.json()["job_id"]
            _wait_for_ready(s, bid, job_id, timeout=90)
            dl = s.get(f"{API}/books/{bid}/pdf-jobs/{job_id}/download", timeout=30)
            assert dl.status_code == 200

            # History should now have exactly one entry for 1-2.
            r1 = s.get(f"{API}/books/{bid}/exports", timeout=10)
            assert r1.status_code == 200
            entries = r1.json()
            assert len(entries) == 1, entries
            entry = entries[0]
            assert entry["start_page"] == 1
            assert entry["end_page"] == 2
            assert entry["filename"].endswith(".pdf")
            assert "id" in entry and "exported_at" in entry

            # Delete the history record and confirm it's gone.
            d = s.delete(f"{API}/books/{bid}/exports/{entry['id']}", timeout=10)
            assert d.status_code == 200
            assert s.get(f"{API}/books/{bid}/exports").json() == []
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_full_export_records_unbounded_range(self, s):
        """A full-book export (no start/end_page) records the full coverage
        so the popover can mark every page as 'already exported'."""
        bid = self._make_book_with_pages(s, 3)
        try:
            start = s.post(f"{API}/books/{bid}/pdf-jobs", timeout=15)
            job_id = start.json()["job_id"]
            _wait_for_ready(s, bid, job_id, timeout=90)
            s.get(f"{API}/books/{bid}/pdf-jobs/{job_id}/download", timeout=30)
            entries = s.get(f"{API}/books/{bid}/exports").json()
            assert len(entries) == 1
            # The worker clamps to actual book length when no range is given,
            # so we expect 1..3 (the full book).
            assert entries[0]["start_page"] == 1
            assert entries[0]["end_page"] == 3
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_invalid_ranges_are_rejected(self, s):
        bid = self._make_book_with_pages(s, 3)
        try:
            # Start without end_page → 400
            r = s.post(f"{API}/books/{bid}/pdf-jobs", json={"start_page": 1}, timeout=10)
            assert r.status_code == 400, r.text
            # start > end → 400
            r = s.post(f"{API}/books/{bid}/pdf-jobs",
                       json={"start_page": 3, "end_page": 1}, timeout=10)
            assert r.status_code == 400
            # 0-indexed attempt → 400
            r = s.post(f"{API}/books/{bid}/pdf-jobs",
                       json={"start_page": 0, "end_page": 2}, timeout=10)
            assert r.status_code == 400
            # start beyond book length → 400
            r = s.post(f"{API}/books/{bid}/pdf-jobs",
                       json={"start_page": 99, "end_page": 100}, timeout=10)
            assert r.status_code == 400
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_delete_book_clears_export_history(self, s):
        bid = self._make_book_with_pages(s, 2)
        # Make one export to populate history.
        start = s.post(f"{API}/books/{bid}/pdf-jobs", timeout=15)
        job_id = start.json()["job_id"]
        _wait_for_ready(s, bid, job_id, timeout=90)
        s.get(f"{API}/books/{bid}/pdf-jobs/{job_id}/download", timeout=30)
        # Sanity: history non-empty.
        assert len(s.get(f"{API}/books/{bid}/exports").json()) == 1
        # Delete the book and verify history is gone too.
        s.delete(f"{API}/books/{bid}")
        # After deletion the book is gone; the exports listing now applies to
        # an orphan book_id and must be empty.
        r = s.get(f"{API}/books/{bid}/exports")
        assert r.status_code == 200
        assert r.json() == []

    def test_pdfx_export_produces_pdfx1a_compliant_file(self, s):
        """When `pdfx: true` is passed, Ghostscript post-processes the
        rendered PDF and the output should declare PDF/X-1a conformance
        with a CMYK OutputIntent baked in."""
        bid = self._make_book_with_pages(s, 2)
        try:
            start = s.post(
                f"{API}/books/{bid}/pdf-jobs",
                json={"pdfx": True}, timeout=15,
            )
            assert start.status_code == 200, start.text
            job_id = start.json()["job_id"]
            final = _wait_for_ready(s, bid, job_id, timeout=120)
            assert final["status"] == "ready", final
            # PDF/X conversion produces a "_pdfx" suffix on the filename.
            assert final["filename"].endswith("_pdfx.pdf"), final["filename"]
            dl = s.get(f"{API}/books/{bid}/pdf-jobs/{job_id}/download", timeout=30)
            assert dl.status_code == 200
            data = dl.content
            # PDF/X-1a:2001 mandates PDF 1.3.
            assert data[:8] == b"%PDF-1.3"
            # Conformance markers must be present in the file body.
            assert b"/GTS_PDFX" in data, "PDF/X conformance marker missing"
            assert b"/OutputIntent" in data, "OutputIntent missing"
            assert b"/DeviceCMYK" in data, "CMYK color space missing"
            # And RGB color profiles must NOT be present — every colour must
            # have been converted down to CMYK.
            assert b"/DeviceRGB" not in data, "DeviceRGB leaked into PDF/X output"
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_cover_spread_export_produces_single_wide_page_with_bleed(self, s):
        """Cover spread builds a single wide PDF: back + spine + front
        with 0.125" bleed on every outside edge. Verifies the trim math
        and that the filename gets the `_cov` suffix IngramSpark expects."""
        bid = self._make_book_with_pages(s, 3)
        try:
            start = s.post(
                f"{API}/books/{bid}/pdf-jobs",
                json={"cover_spread": True, "spine_width_in": 0.5},
                timeout=15,
            )
            assert start.status_code == 200, start.text
            job_id = start.json()["job_id"]
            final = _wait_for_ready(s, bid, job_id, timeout=120)
            assert final["status"] == "ready", final
            assert final["filename"].endswith("_cov.pdf"), final["filename"]
            dl = s.get(f"{API}/books/{bid}/pdf-jobs/{job_id}/download", timeout=30)
            assert dl.status_code == 200
            from io import BytesIO
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(dl.content))
            assert len(reader.pages) == 1, "Cover spread must be a single page"
            box = reader.pages[0].mediabox
            w_pt, h_pt = float(box.width), float(box.height)
            # The test book uses a4 pages (794x1123 px → 8.27"x11.70").
            # Trim = back (8.27) + spine (0.5) + front (8.27) = 17.04"
            # + 0.125" bleed each outside edge = 17.29" wide x 11.95" tall.
            w_in, h_in = w_pt / 72.0, h_pt / 72.0
            assert 17.0 < w_in < 17.6, f"unexpected spread width {w_in:.2f}\""
            assert 11.8 < h_in < 12.1, f"unexpected spread height {h_in:.2f}\""
        finally:
            s.delete(f"{API}/books/{bid}")
