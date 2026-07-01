"""Backend tests for iteration 21 — admin_book_diagnose gains
`?include_text_content=true` plus new response fields:
  - top-level:    cached_font_families, book_text_presets, include_text_content
  - per-page:     background_color, full_bleed, show_page_number,
                  page_number_font, page_number_size, fonts_referenced,
                  fonts_missing_from_cache
  - text blocks:  font_family, font_size, text_align, color, background_color,
                  text_role, is_chapter, is_toc  (always present)
                  html + html_truncated  (only when include_text_content=true)

Auth is handled by conftest.py — every request auto-carries the admin bearer.
Belinda seed book id is discovered dynamically, so tests survive re-seeds.
"""
from __future__ import annotations

import os

import pytest
import requests

BASE = os.environ.get(
    "REACT_APP_BACKEND_URL", "https://manuscript-app-2.preview.emergentagent.com"
).rstrip("/")
API = f"{BASE}/api"


# ---- Fixtures -----------------------------------------------------------------


@pytest.fixture(scope="module")
def s() -> requests.Session:
    return requests.Session()


@pytest.fixture(scope="module")
def belinda_book_id(s) -> str:
    """Find the seeded Belinda book id (may drift across environments)."""
    r = s.get(f"{API}/books", timeout=15)
    assert r.status_code == 200, r.text
    for b in r.json():
        if "Belinda" in (b.get("title") or ""):
            return b["id"]
    pytest.skip("Belinda seed book not found in this environment")


@pytest.fixture(scope="module")
def temp_book_with_arial(s):
    """A TEST_ book whose page 1 references 'Arial' (NOT cached).
    Yields (book_id, page_id). Cleaned up in teardown."""
    r = s.post(
        f"{API}/books",
        json={"title": "TEST_ArialFont", "author": "TEST_Author", "page_size": "a4"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    book = r.json()
    bid = book["id"]
    page_id = book["pages"][0]["id"]

    long_text = "A" * 25_000  # > 20 KB so we can prove truncation is exact.
    pages = [
        {
            "id": page_id,
            "blocks": [
                {
                    "id": "b_arial",
                    "type": "text",
                    "x": 10,
                    "y": 10,
                    "width": 400,
                    "height": 300,
                    "html": long_text,
                    "font_family": "Arial",
                    "font_size": 14,
                    "text_align": "left",
                    "color": "#111111",
                    "background_color": "#FFFFFF",
                    "text_role": "body",
                    "is_chapter": False,
                    "is_toc": False,
                },
            ],
            "background_color": "#FAFAFA",
            "show_page_number": True,
            "page_number_font": "Helvetica",  # also NOT cached
            "page_number_size": 10,
            "full_bleed": False,
        }
    ]
    r = s.put(
        f"{API}/books/{bid}",
        json={"title": "TEST_ArialFont", "page_size": "a4", "pages": pages},
        timeout=15,
    )
    assert r.status_code == 200, r.text

    yield bid, page_id

    try:
        s.delete(f"{API}/books/{bid}", timeout=10)
    except Exception:
        pass


# ---- Tests: default (include_text_content omitted / false) ----------------


class TestBookDiagnoseDefault:
    """When include_text_content is NOT set, the new html fields must be
    absent on text blocks but every other new key must still be present."""

    def test_top_level_shape(self, s, belinda_book_id):
        r = s.get(f"{API}/admin/book-diagnose/{belinda_book_id}", timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()

        # New top-level keys
        assert d["include_text_content"] is False
        assert isinstance(d["cached_font_families"], list)
        assert len(d["cached_font_families"]) == 27
        # Sanity-check a few well-known families
        for fam in ("Playfair Display", "Poppins", "Cormorant Garamond",
                    "Bebas Neue", "Work Sans"):
            assert fam in d["cached_font_families"], f"missing {fam}"
        # Deduped + sorted
        assert d["cached_font_families"] == sorted(set(d["cached_font_families"]))
        # book_text_presets is a passthrough of book.text_presets
        assert "book_text_presets" in d
        # Regression: old top-level keys still there
        for k in ("book_id", "title", "total_pages", "probed_bytes",
                  "requested_page_no", "pages"):
            assert k in d, f"regression: missing top-level key {k}"

    def test_page_level_new_fields(self, s, belinda_book_id):
        r = s.get(
            f"{API}/admin/book-diagnose/{belinda_book_id}?page_no=1", timeout=30
        )
        assert r.status_code == 200
        d = r.json()
        p = d["pages"][0]
        # New per-page keys — must be present (may be None if unset in db)
        for k in ("background_color", "full_bleed", "show_page_number",
                  "page_number_font", "page_number_size",
                  "fonts_referenced", "fonts_missing_from_cache"):
            assert k in p, f"page missing key {k}"
        assert isinstance(p["fonts_referenced"], list)
        assert isinstance(p["fonts_missing_from_cache"], list)
        # Belinda page 1 uses only cached families — nothing missing.
        assert p["fonts_missing_from_cache"] == [], p["fonts_missing_from_cache"]
        # And the ones referenced are indeed a subset of cached_font_families
        for fam in p["fonts_referenced"]:
            assert fam in d["cached_font_families"], f"{fam} not in cache set"
        # Lists are sorted
        assert p["fonts_referenced"] == sorted(p["fonts_referenced"])
        assert p["fonts_missing_from_cache"] == sorted(p["fonts_missing_from_cache"])

    def test_text_blocks_have_metadata_but_no_html(self, s, belinda_book_id):
        r = s.get(
            f"{API}/admin/book-diagnose/{belinda_book_id}?page_no=1", timeout=30
        )
        d = r.json()
        text_blocks = [b for b in d["pages"][0]["blocks"] if b["type"] == "text"]
        assert text_blocks, "expected at least one text block on Belinda page 1"
        for b in text_blocks:
            # New per-text-block metadata fields — present even when default
            for k in ("font_family", "font_size", "text_align", "color",
                      "background_color", "text_role", "is_chapter", "is_toc"):
                assert k in b, f"text block missing {k}"
            # html gate: MUST be absent when include_text_content is off
            assert "html" not in b, "html leaked into default response"
            assert "html_truncated" not in b, "html_truncated leaked into default response"


# ---- Tests: include_text_content=true --------------------------------------


class TestBookDiagnoseWithText:
    """When include_text_content=true, text blocks must carry `html` +
    `html_truncated`, and the 20 KB cap must be enforced exactly."""

    def test_include_text_true_echoed(self, s, belinda_book_id):
        r = s.get(
            f"{API}/admin/book-diagnose/{belinda_book_id}?include_text_content=true",
            timeout=30,
        )
        assert r.status_code == 200, r.text
        assert r.json()["include_text_content"] is True

    def test_belinda_html_present_and_short(self, s, belinda_book_id):
        r = s.get(
            f"{API}/admin/book-diagnose/{belinda_book_id}?page_no=1&include_text_content=true",
            timeout=30,
        )
        d = r.json()
        text_blocks = [b for b in d["pages"][0]["blocks"] if b["type"] == "text"]
        assert text_blocks
        for b in text_blocks:
            assert "html" in b
            assert "html_truncated" in b
            assert isinstance(b["html"], str)
            # Belinda's text is short — nowhere near 20 KB
            assert b["html_truncated"] is False
            assert len(b["html"]) <= 20_000

    def test_truncation_exact_20kb(self, s, temp_book_with_arial):
        bid, _ = temp_book_with_arial
        r = s.get(
            f"{API}/admin/book-diagnose/{bid}?page_no=1&include_text_content=true",
            timeout=30,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        text_blocks = [b for b in d["pages"][0]["blocks"] if b["type"] == "text"]
        assert len(text_blocks) == 1
        blk = text_blocks[0]
        assert blk["html_truncated"] is True
        assert len(blk["html"]) == 20_000, (
            f"expected exactly 20000 chars, got {len(blk['html'])}"
        )
        # text_length_chars must reflect the FULL (untruncated) length
        assert blk["text_length_chars"] == 25_000

    def test_missing_fonts_detected(self, s, temp_book_with_arial):
        """The temp book uses 'Arial' (block) and 'Helvetica' (page number),
        neither of which is in the 27-family cache."""
        bid, _ = temp_book_with_arial
        r = s.get(
            f"{API}/admin/book-diagnose/{bid}?page_no=1", timeout=30
        )
        d = r.json()
        p = d["pages"][0]
        assert "Arial" in p["fonts_referenced"]
        assert "Helvetica" in p["fonts_referenced"]
        assert "Arial" in p["fonts_missing_from_cache"]
        assert "Helvetica" in p["fonts_missing_from_cache"]
        # And Arial/Helvetica must NOT appear in the cached set
        assert "Arial" not in d["cached_font_families"]
        assert "Helvetica" not in d["cached_font_families"]


# ---- Tests: read-only invariant -------------------------------------------


class TestReadOnlyInvariant:
    """The whole point of book-diagnose is zero side effects. Even the new
    include_text_content path must not touch book.updated_at."""

    def test_updated_at_unchanged(self, s, belinda_book_id):
        r0 = s.get(f"{API}/books/{belinda_book_id}", timeout=15)
        assert r0.status_code == 200
        before = r0.json().get("updated_at")

        # Fire both variants
        r1 = s.get(
            f"{API}/admin/book-diagnose/{belinda_book_id}?include_text_content=true",
            timeout=30,
        )
        assert r1.status_code == 200
        r2 = s.get(
            f"{API}/admin/book-diagnose/{belinda_book_id}?page_no=1&include_text_content=true",
            timeout=30,
        )
        assert r2.status_code == 200

        r3 = s.get(f"{API}/books/{belinda_book_id}", timeout=15)
        after = r3.json().get("updated_at")
        assert before == after, f"read-only invariant broken: {before} -> {after}"


# ---- Tests: regression — old response shape still intact -------------------


class TestRegressionOldConsumers:
    """BookDiagnoseDialog reads block.type, block.file_record, block.probe,
    block.geom. None of those keys should have shifted."""

    def test_block_geom_still_present(self, s, belinda_book_id):
        r = s.get(
            f"{API}/admin/book-diagnose/{belinda_book_id}?page_no=1", timeout=30
        )
        d = r.json()
        for b in d["pages"][0]["blocks"]:
            assert "type" in b
            assert "geom" in b
            for k in ("x", "y", "width", "height", "z_index"):
                assert k in b["geom"], f"block.geom missing {k}"

    def test_image_blocks_still_have_file_record(self, s, belinda_book_id):
        """If Belinda page 1 has image blocks, file_record key must be present
        (value may be None for orphans)."""
        r = s.get(
            f"{API}/admin/book-diagnose/{belinda_book_id}?page_no=1", timeout=30
        )
        d = r.json()
        for b in d["pages"][0]["blocks"]:
            if b["type"] == "image":
                assert "storage_path" in b
                assert "file_record" in b
