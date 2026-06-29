"""Regression: assert images embedded in exported PDFs render at ≥300 DPI
at the stated trim size.

This catches the silent failure mode where a "scales to inches under the
hood" pipeline ships low-resolution output — e.g. if Cairo were to
downsample every embedded image to the 96-DPI CSS-pixel canvas size, a
full-bleed image on an 8.75" page would print at 96 DPI (840 px long
edge) instead of the print spec's 300 DPI minimum (2625 px long edge).

Strategy:
  1. Upload a 3000×3000 px PNG (high enough to satisfy 300 DPI at any
     trim size up to 10").
  2. Place it as a full-bleed image block on an 8.75" Square page.
  3. Export the PDF (default DPI=300).
  4. Walk the resulting PDF's XObject image inventory with pypdf, find
     the embedded image, and assert its raw pixel dimensions are ≥ the
     trim_inches × 300 threshold.

The assertion is keyed off `trim_inches × MIN_DPI`, NOT off the upload
size, so this test still catches a downsampling regression even if we
later change the upload resolution or page size.
"""
from __future__ import annotations

import io
import os
import time
import uuid

import pytest
import requests
from PIL import Image
from pypdf import PdfReader

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL", "https://manuscript-app-2.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api"

MIN_PRINT_DPI = 300
PX_PER_IN_CANVAS = 96  # mirrors PAGE_MARGIN_PX = 48 (0.5") at 96 DPI


def _auth() -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{API}/auth/login",
        json={"email": "chris@dcsbuilt.com.au", "password": "Redcar01"},
        timeout=15,
    )
    assert r.ok, f"auth failed: {r.status_code} {r.text}"
    return s


def _upload_test_image(s: requests.Session, size_px: int) -> tuple[str, str]:
    """Upload a solid-colour square PNG of `size_px × size_px` and return
    (image_path, image_url) for embedding into a block. Uses /api/upload
    (the canonical endpoint defined in server.py:upload_image)."""
    img = Image.new("RGB", (size_px, size_px), (210, 90, 50))  # terracotta
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    files = {"file": (f"reg_{size_px}.png", buf, "image/png")}
    r = s.post(f"{API}/upload", files=files, timeout=60)
    assert r.ok, r.text
    j = r.json()
    return j["path"], j["url"]


def _make_book_with_fullbleed_image(s: requests.Session, page_size: str,
                                     image_path: str, image_url: str) -> str:
    """Create a 2-page book where page 2 has a single full-bleed image
    block at the page's CSS dimensions. (Page 1 is the cover, which the
    pipeline treats specially — we put the test image on page 2.)"""
    r = s.post(f"{API}/books",
               json={"title": f"TEST_imgres_{uuid.uuid4().hex[:6]}",
                     "page_size": page_size}, timeout=15)
    bid = r.json()["id"]
    book = s.get(f"{API}/books/{bid}", timeout=15).json()

    # Determine page CSS dims from the page_size key (mirrors pageSizes.js).
    page_dims = {
        "a4": (794, 1123), "letter": (816, 1056),
        "square": (840, 840), "book6x9": (576, 864),
    }
    w_css, h_css = page_dims[page_size]

    full_block = {
        "id": str(uuid.uuid4()),
        "type": "image",
        "x": 0, "y": 0, "width": w_css, "height": h_css,
        "z_index": 1,
        "image_path": image_path,
        "image_url": image_url,
    }
    # Pad with a second page so the printable test image isn't on the cover.
    while len(book["pages"]) < 2:
        book["pages"].append({"id": str(uuid.uuid4()), "blocks": []})
    book["pages"][1]["blocks"] = [full_block]
    s.put(f"{API}/books/{bid}", json=book, timeout=15)
    return bid


def _export_pdf(s: requests.Session, bid: str, dpi: int = 300) -> bytes:
    r = s.post(f"{API}/books/{bid}/pdf-jobs", json={"dpi": dpi}, timeout=15)
    assert r.ok, r.text
    jid = r.json()["job_id"]
    last = None
    for _ in range(300):
        time.sleep(0.5)
        st = s.get(f"{API}/books/{bid}/pdf-jobs/{jid}", timeout=15).json()
        last = st
        if st["status"] in {"ready", "failed"}:
            break
    assert last and last.get("status") == "ready", last
    dl = s.get(f"{API}/books/{bid}/pdf-jobs/{jid}/download", timeout=60)
    assert dl.ok
    return dl.content


def _largest_embedded_image_long_edge_px(pdf_bytes: bytes) -> int:
    """Walk every page's image XObjects, return the largest long-edge in
    pixels. We assume the full-bleed test image is the largest image in
    the PDF (true unless WeasyPrint embeds a hidden font glyph atlas as
    a bitmap, which it doesn't in any current version)."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    longest = 0
    for page in reader.pages:
        for img in page.images:
            try:
                pil = Image.open(io.BytesIO(img.data))
                longest = max(longest, max(pil.size))
            except Exception:
                # Some images are CCITTFaxDecode or DCT — pypdf returns
                # raw stream. Try direct PIL decode of the data.
                pass
    return longest


class TestEmbeddedImageResolutionMeets300DPI:
    """For each user-selectable trim size, assert that a full-bleed image
    in the exported PDF retains ≥300 DPI worth of pixels.

    The cap in `pdf_builder.DPI_LONG_EDGE_CAPS[300] = 3300` allows up to
    a 3300-px long edge at 300 DPI — that's ≥300 DPI for any trim up to
    11" long-edge. We assert the embedded image is at LEAST trim×300 px,
    NOT capped below it."""

    @pytest.mark.parametrize("page_size,trim_long_in", [
        ("square", 8.75),  # the size the user just changed to
        ("a4", 297 / 25.4),  # 11.69"
        ("letter", 11.0),
        ("book6x9", 9.0),
    ])
    def test_full_bleed_image_meets_min_dpi(self, page_size, trim_long_in):
        s = _auth()
        # Upload an image just over the cap so we KNOW the pipeline has
        # the option to ship a high-res result. If the test fails, the
        # pipeline silently downsampled below 300 DPI.
        ip, iu = _upload_test_image(s, 4000)
        bid = _make_book_with_fullbleed_image(s, page_size, ip, iu)
        try:
            pdf = _export_pdf(s, bid, dpi=300)
            assert pdf.startswith(b"%PDF"), "not a PDF"
            embedded_px = _largest_embedded_image_long_edge_px(pdf)
            required_px = round(trim_long_in * MIN_PRINT_DPI)
            assert embedded_px >= required_px, (
                f"{page_size}: embedded image long edge = {embedded_px}px, "
                f"required ≥ {required_px}px ({trim_long_in}\" × {MIN_PRINT_DPI} DPI). "
                f"PDF pipeline is downsampling images below print spec."
            )
        finally:
            s.delete(f"{API}/books/{bid}")


class TestSquareTrimIs8_75Inches:
    """Lock in the 8.75" Square dimension change. Catches accidental
    revert to 8.5" (the previous default). 8.75" trim × 72 pt/in =
    630 pt; with 0.125" bleed on every side the MediaBox is 648 pt."""

    def test_pdf_mediabox_is_648pt(self):
        s = _auth()
        r = s.post(f"{API}/books",
                   json={"title": f"TEST_sq_{uuid.uuid4().hex[:6]}",
                         "page_size": "square"}, timeout=15)
        bid = r.json()["id"]
        try:
            pdf = _export_pdf(s, bid)
            reader = PdfReader(io.BytesIO(pdf))
            mb = reader.pages[0].mediabox
            assert float(mb.width) == 648.0, (
                f"square trim MediaBox width = {float(mb.width)}pt; "
                f"expected 648 (8.75\" × 72 + 2 × 9 pt bleed). The 8.5\" "
                f"default may have been re-introduced."
            )
            assert float(mb.height) == 648.0
        finally:
            s.delete(f"{API}/books/{bid}")
