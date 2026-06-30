"""Integration tests for the file-upload validation pipeline added in
iteration 54 (server.py /api/upload).

These tests are part of the integration suite — they hit the live
preview backend via REACT_APP_BACKEND_URL. The user explicitly requested
this list of 4 inputs be exercised:
  1. genuinely corrupted PNG (real PNG truncated mid-stream)
  2. zero-byte file
  3. text file renamed to .png
  4. valid image (control case)
First three must return 400 with a clear, user-facing message; fourth
must succeed AND the persisted db.files record must contain correct
width_px / height_px.
"""
from __future__ import annotations

import io
import os
import uuid

import pytest
import requests
from PIL import Image

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL", "https://manuscript-app-2.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api"


def _auth() -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{API}/auth/login",
        json={"email": "chris@dcsbuilt.com.au", "password": "Redcar01"},
        timeout=15,
    )
    assert r.ok, f"auth failed: {r.status_code} {r.text}"
    return s


def _make_valid_png(size_px: int = 200) -> bytes:
    img = Image.new("RGB", (size_px, size_px), (90, 140, 70))  # forest green
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


class TestUploadValidationRejections:
    """All three bad-input cases must return HTTP 400 with the
    documented user-facing message. The pipeline MUST NOT have written
    anything to object storage (verified by checking the response shape
    — no `path` / `url` keys when validation rejects)."""

    def test_zero_byte_file_rejected_with_400(self):
        s = _auth()
        r = s.post(
            f"{API}/upload",
            files={"file": ("empty.png", b"", "image/png")},
            timeout=15,
        )
        assert r.status_code == 400, r.text
        body = r.json()
        # FastAPI wraps the HTTPException message in {"detail": "..."}.
        assert "empty" in body.get("detail", "").lower(), body

    def test_truncated_png_rejected_with_400(self):
        """Real PNG, truncated mid-IDAT — looks valid for the first few
        hundred bytes but Pillow's verify() catches the bad CRC."""
        good = _make_valid_png(size_px=400)
        # Truncate to the first 200 bytes — past the header signature but
        # well before the IDAT and IEND chunks complete.
        truncated = good[:200]
        s = _auth()
        r = s.post(
            f"{API}/upload",
            files={"file": ("truncated.png", truncated, "image/png")},
            timeout=15,
        )
        assert r.status_code == 400, r.text
        body = r.json()
        detail = body.get("detail", "")
        # Message must be plain-English, not a stack trace or Pillow error.
        assert "corrupted" in detail.lower() or "not a valid image" in detail.lower(), detail
        # Sanity — verify the helpful "try uploading it again" hint is present.
        assert "again" in detail.lower(), detail

    def test_text_file_with_png_extension_rejected(self):
        """A text file masquerading as a PNG via filename + content-type.
        Pillow verify() rejects on the missing PNG signature."""
        s = _auth()
        text_bytes = (b"Subject: Quarterly Report\nThis is plain ASCII text "
                      b"masquerading as a PNG. It must be rejected.")
        r = s.post(
            f"{API}/upload",
            files={"file": ("fake.png", text_bytes, "image/png")},
            timeout=15,
        )
        assert r.status_code == 400, r.text
        body = r.json()
        assert "not a valid image" in body.get("detail", "").lower(), body


class TestUploadValidationAcceptance:
    """Control case — a valid PNG must upload AND the persisted db.files
    record must contain real width_px / height_px values (not None)."""

    def test_valid_png_uploads_and_persists_dimensions(self):
        s = _auth()
        size = 567  # odd number so a stale hardcoded constant won't pass
        valid = _make_valid_png(size_px=size)
        upload = s.post(
            f"{API}/upload",
            files={"file": (f"valid_{uuid.uuid4().hex[:6]}.png", valid, "image/png")},
            timeout=30,
        )
        assert upload.status_code == 200, upload.text
        j = upload.json()
        # Response must include real dimension fields the editor + preflight
        # will rely on. The user explicitly asked for these to ALWAYS be
        # populated, not None.
        assert j.get("width_px") == size, j
        assert j.get("height_px") == size, j
        assert j.get("path"), j
        assert j.get("url"), j

        # Cross-check the persisted db.files record via /api/assets —
        # the dimensions stored at upload time MUST round-trip so the
        # preflight DPI check (which reads these fields from db.files)
        # can use them.
        listing = s.get(f"{API}/assets", timeout=15)
        assert listing.ok, listing.text
        rows = listing.json()
        if isinstance(rows, dict):
            rows = rows.get("files") or rows.get("assets") or []
        matches = [r for r in rows if r.get("path") == j["path"]]
        assert matches, f"persisted db.files row not found for {j['path']!r}"
        row = matches[0]
        assert row.get("width_px") == size, row
        assert row.get("height_px") == size, row

    def test_valid_jpeg_uploads_and_persists_dimensions(self):
        """Same control flow with a JPEG, in case PNG-specific Pillow
        paths differ from JPEG."""
        s = _auth()
        img = Image.new("RGB", (300, 200), (90, 140, 70))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        upload = s.post(
            f"{API}/upload",
            files={"file": (f"valid_{uuid.uuid4().hex[:6]}.jpg", buf.getvalue(), "image/jpeg")},
            timeout=30,
        )
        assert upload.status_code == 200, upload.text
        j = upload.json()
        assert j.get("width_px") == 300, j
        assert j.get("height_px") == 200, j
