"""IngramSpark v5.11.26 Phase 2 — quality / spec adherence tests.

Covers:
1. ISBN-based filename naming (interior `_txt`, cover `_cvr`).
2. Title-slug fallback when no ISBN.
3. Preflight surfaces ink-coverage warnings for >240% TAC colours.
4. Preflight passes a clean book with no warnings.
"""
from __future__ import annotations

import os
import time
import uuid

import requests

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


def _wait_for_job(s, bid, jid, deadline_s: float = 60.0) -> dict:
    end = time.time() + deadline_s
    while time.time() < end:
        st = s.get(f"{API}/books/{bid}/pdf-jobs/{jid}", timeout=15).json()
        if st["status"] in {"ready", "failed"}:
            return st
        time.sleep(0.5)
    raise AssertionError("job did not finish in time")


class TestIsbnNaming:
    def test_isbn_drives_interior_filename(self):
        s = _auth()
        r = s.post(f"{API}/books", json={"title": "My Test Book", "page_size": "square"})
        bid = r.json()["id"]
        try:
            book = s.get(f"{API}/books/{bid}").json()
            s.put(f"{API}/books/{bid}", json={**book, "isbn": "9781234567897"})
            j = s.post(f"{API}/books/{bid}/pdf-jobs", json={"pdfx": True}).json()
            st = _wait_for_job(s, bid, j["job_id"])
            assert st["status"] == "ready"
            assert st["filename"] == "9781234567897_txt_pdfx.pdf"
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_cover_spread_uses_cvr_suffix(self):
        s = _auth()
        r = s.post(f"{API}/books", json={"title": "X", "page_size": "square"})
        bid = r.json()["id"]
        try:
            book = s.get(f"{API}/books/{bid}").json()
            s.put(f"{API}/books/{bid}", json={**book, "isbn": "9781234567897"})
            j = s.post(
                f"{API}/books/{bid}/pdf-jobs", json={"cover_spread": True}
            ).json()
            st = _wait_for_job(s, bid, j["job_id"])
            assert st["status"] == "ready"
            assert st["filename"] == "9781234567897_cvr.pdf"
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_blank_isbn_falls_back_to_title_slug(self):
        s = _auth()
        r = s.post(f"{API}/books", json={"title": "Plain Title", "page_size": "square"})
        bid = r.json()["id"]
        try:
            j = s.post(f"{API}/books/{bid}/pdf-jobs").json()
            st = _wait_for_job(s, bid, j["job_id"])
            assert st["filename"].startswith("Plain_Title")
            # No _txt suffix when no ISBN.
            assert "_txt" not in st["filename"]
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_invalid_isbn_falls_back_to_title_slug(self):
        s = _auth()
        r = s.post(f"{API}/books", json={"title": "Bad ISBN Book", "page_size": "square"})
        bid = r.json()["id"]
        try:
            book = s.get(f"{API}/books/{bid}").json()
            # 5 digits — not a valid ISBN-10 or ISBN-13.
            s.put(f"{API}/books/{bid}", json={**book, "isbn": "12345"})
            j = s.post(f"{API}/books/{bid}/pdf-jobs").json()
            st = _wait_for_job(s, bid, j["job_id"])
            assert st["filename"].startswith("Bad_ISBN_Book")
            assert "12345" not in st["filename"]
        finally:
            s.delete(f"{API}/books/{bid}")


class TestPreflightInkCoverage:
    def test_text_above_240_tac_surfaces_warning(self):
        """A pure-saturated dark-red text (#FF0000 is 200%, #800000 is 260%)
        triggers the >240% TAC warning."""
        s = _auth()
        r = s.post(f"{API}/books", json={"title": "Ink test", "page_size": "square"})
        bid = r.json()["id"]
        try:
            book = s.get(f"{API}/books/{bid}").json()
            heavy_block = {
                "id": str(uuid.uuid4()), "type": "text",
                "x": 60, "y": 60, "width": 200, "height": 80, "z_index": 1,
                "html": "<p>heavy ink</p>",
                "font_family": "Cormorant Garamond", "font_size": 18,
                "text_align": "left",
                # #800000 = (128, 0, 0). K=0.5, M=Y=1, C=0 → TAC = 250% > 240%.
                "color": "#800000",
            }
            book["pages"][0]["blocks"] = [heavy_block]
            # Two pages so we pass the parity check.
            book["pages"].append({
                "id": str(uuid.uuid4()), "blocks": [],
                "background_color": "#FFF8DC",
            })
            s.put(f"{API}/books/{bid}", json=book)
            pf = s.get(f"{API}/books/{bid}/preflight", timeout=15).json()
            checks = [w["check"] for w in pf["warnings"]]
            assert "ink_coverage" in checks, pf
            ink_row = next(w for w in pf["warnings"] if w["check"] == "ink_coverage")
            assert ink_row["items"][0]["page"] == 1
            assert ink_row["items"][0]["total_ink_pct"] > 240
        finally:
            s.delete(f"{API}/books/{bid}")

    def test_safe_colour_passes(self):
        s = _auth()
        r = s.post(f"{API}/books", json={"title": "Clean", "page_size": "square"})
        bid = r.json()["id"]
        try:
            book = s.get(f"{API}/books/{bid}").json()
            block = {
                "id": str(uuid.uuid4()), "type": "text",
                "x": 60, "y": 60, "width": 200, "height": 80, "z_index": 1,
                "html": "<p>OK ink</p>",
                "font_family": "Cormorant Garamond", "font_size": 18,
                "text_align": "left",
                "color": "#222222",  # ~85% TAC — well under 240
            }
            book["pages"][0]["blocks"] = [block]
            book["pages"].append({
                "id": str(uuid.uuid4()), "blocks": [],
                "background_color": "#FFF8DC",
            })
            s.put(f"{API}/books/{bid}", json=book)
            pf = s.get(f"{API}/books/{bid}/preflight", timeout=15).json()
            assert pf["status"] == "ok", pf
            assert "ink_coverage" in pf["passed"]
        finally:
            s.delete(f"{API}/books/{bid}")
