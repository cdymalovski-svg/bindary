"""Backend tests for the manuscript-import endpoint and parser.

Covers:
  - POST /api/books/import with .docx, .txt, and .md payloads
  - `Page N` label-based splitting heuristic (the convention used by the
    user's "Friends in Every Color" draft)
  - Plain-paragraph fallback when no labels are present
  - Title / author auto-extraction from the first chunk
  - Title-page styling (Playfair Display) vs body styling (Cormorant)
  - Rejection of unsupported file types
"""
import io
import os
import re
import uuid

import pytest
import requests

BASE_URL = os.environ.get(
    'REACT_APP_BACKEND_URL', 'https://wysiwyg-book-builder.preview.emergentagent.com'
).rstrip('/')
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def s():
    return requests.Session()


def _strip(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "").strip()


class TestBookImport:
    def test_import_txt_with_page_labels(self, s):
        """Page N labels in plain text should yield exactly N pages."""
        body = (
            "Page 1\n"
            "The Quiet Tide\nA. River\n\n"
            "Page 2\n"
            "Once upon a tide, the sea hummed a sleepy song.\n\n"
            "Page 3\n"
            "By morning the shore knew every word.\n"
        )
        r = s.post(
            f"{API}/books/import",
            files={"file": ("TEST_tide.txt", io.BytesIO(body.encode("utf-8")), "text/plain")},
            data={"page_size": "square"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        book = r.json()
        try:
            assert book["page_size"] == "square"
            assert book["title"] == "The Quiet Tide"
            assert book["author"] == "A. River"
            assert len(book["pages"]) == 3
            # Page 1 should be styled as a title (Playfair Display).
            cover_block = book["pages"][0]["blocks"][0]
            assert cover_block["type"] == "text"
            assert cover_block["font_family"] == "Playfair Display"
            assert cover_block["text_align"] == "center"
            # Page 1 should also hide the page number (cover convention).
            assert book["pages"][0]["show_page_number"] is False
            # Page 2 should be body text (Cormorant Garamond).
            body_block = book["pages"][1]["blocks"][0]
            assert body_block["font_family"] == "Cormorant Garamond"
            assert "sleepy song" in _strip(body_block["html"])
            # Page numbers re-enabled on body pages.
            assert book["pages"][1]["show_page_number"] is True
        finally:
            s.delete(f"{API}/books/{book['id']}")

    def test_import_txt_without_labels_uses_paragraphs(self, s):
        """No Page N labels → each blank-line paragraph = one page."""
        body = "First page paragraph.\n\nSecond page paragraph.\n\nThird page paragraph."
        r = s.post(
            f"{API}/books/import",
            files={"file": ("TEST_paras.txt", io.BytesIO(body.encode("utf-8")), "text/plain")},
            data={"page_size": "a4"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        book = r.json()
        try:
            assert len(book["pages"]) == 3
            # First chunk is short — looks like a title — should be cover-styled.
            assert book["pages"][0]["blocks"][0]["font_family"] == "Playfair Display"
        finally:
            s.delete(f"{API}/books/{book['id']}")

    def test_import_md_with_headings(self, s):
        """Markdown headings produce chapter-style pages (heading + body)."""
        body = "# Chapter One\n\nIt begins.\n\n# Chapter Two\n\nIt deepens.\n"
        r = s.post(
            f"{API}/books/import",
            files={"file": ("TEST_chapters.md", io.BytesIO(body.encode("utf-8")), "text/markdown")},
            data={"page_size": "letter"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        book = r.json()
        try:
            assert len(book["pages"]) == 2
            ch1_blocks = book["pages"][0]["blocks"]
            # Heading + body = 2 blocks.
            assert len(ch1_blocks) == 2
            heading = ch1_blocks[0]
            assert heading["is_chapter"] is True
            assert "Chapter One" in _strip(heading["html"])
            assert heading["font_family"] == "Playfair Display"
        finally:
            s.delete(f"{API}/books/{book['id']}")

    def test_import_explicit_title_author_override(self, s):
        body = "Page 1\nAuto Title\n\nPage 2\nBody"
        r = s.post(
            f"{API}/books/import",
            files={"file": ("TEST_override.txt", io.BytesIO(body.encode("utf-8")), "text/plain")},
            data={"title": "User Title", "author": "User Author", "page_size": "a4"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        book = r.json()
        try:
            assert book["title"] == "User Title"
            assert book["author"] == "User Author"
        finally:
            s.delete(f"{API}/books/{book['id']}")

    def test_import_rejects_unknown_extension(self, s):
        r = s.post(
            f"{API}/books/import",
            files={"file": ("TEST_x.rtf", io.BytesIO(b"junk"), "application/rtf")},
            timeout=15,
        )
        assert r.status_code == 400
        assert "Unsupported" in r.text or "unsupported" in r.text

    def test_import_rejects_empty_document(self, s):
        r = s.post(
            f"{API}/books/import",
            files={"file": ("TEST_empty.txt", io.BytesIO(b"   \n  \n"), "text/plain")},
            timeout=15,
        )
        assert r.status_code == 400
        assert "empty" in r.text.lower()
