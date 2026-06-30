"""Offline unit tests for Phase 3.3 PDF metadata stamping.

These tests synthesize tiny valid PDFs in-memory, call stamp_pdf_metadata,
and read back the resulting /Info dictionary with pypdf. No HTTP. No
hosted-env dependency. Run in <1 second.

Coverage:
  - Title / Author / Producer stamping
  - ISBN placeholder when book.isbn is blank, missing, malformed
  - 13-digit ISBN passes through as "ISBN <digits>"
  - 10-digit ISBN passes through as "ISBN <digits>"
  - Hyphenated ISBN is normalized
  - Publisher defaults to "Self-published" when blank
  - Idempotent — stamping twice yields the same metadata
  - Failure mode — corrupt input bytes return unchanged (no crash)
"""
from __future__ import annotations

import io
import sys

sys.path.insert(0, "/app/backend")

import pytest
from pypdf import PdfReader, PdfWriter

from pdf_builder import stamp_pdf_metadata, ISBN_PLACEHOLDER


def _make_blank_pdf(pages: int = 1, width: float = 612.0, height: float = 792.0) -> bytes:
    """Synthesize a minimal valid PDF with N blank pages."""
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=width, height=height)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def _read_info(pdf_bytes: bytes) -> dict:
    """Return the /Info dictionary as a plain str→str dict."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    meta = reader.metadata or {}
    # pypdf returns ByteStringObject / TextStringObject — coerce to str.
    return {str(k): str(v) for k, v in meta.items()}


class TestPdfMetadataStamping:
    def test_title_and_author_are_stamped(self):
        out = stamp_pdf_metadata(
            _make_blank_pdf(),
            {"title": "Belinda's Adventure", "author": "Jane Doe"},
        )
        info = _read_info(out)
        assert info.get("/Title") == "Belinda's Adventure"
        assert info.get("/Author") == "Jane Doe"

    def test_missing_title_defaults_to_untitled(self):
        out = stamp_pdf_metadata(_make_blank_pdf(), {})
        info = _read_info(out)
        assert info.get("/Title") == "Untitled"

    def test_isbn_placeholder_when_blank(self):
        """User explicit policy: ISBN field stays empty until publication.
        The PDF must carry a descriptive placeholder so catalog tools
        don't index the file as having an empty Subject."""
        out = stamp_pdf_metadata(
            _make_blank_pdf(),
            {"title": "x", "author": "y", "isbn": ""},
        )
        info = _read_info(out)
        assert info.get("/Subject") == ISBN_PLACEHOLDER

    def test_isbn_placeholder_when_field_absent(self):
        out = stamp_pdf_metadata(_make_blank_pdf(), {"title": "x"})
        info = _read_info(out)
        assert info.get("/Subject") == ISBN_PLACEHOLDER

    def test_isbn_placeholder_when_malformed(self):
        """Whitespace, half-typed ISBNs etc. all fall back to placeholder
        — better to flag the slot as pending than ship "ISBN 123" which
        would mislead a catalog ingestor."""
        for bad in ("   ", "123", "978-0-XX-XXXXXX-X", "abc-def-ghi", "97800000000"):
            out = stamp_pdf_metadata(
                _make_blank_pdf(), {"title": "x", "isbn": bad},
            )
            info = _read_info(out)
            assert info.get("/Subject") == ISBN_PLACEHOLDER, (
                f"malformed ISBN {bad!r} should fall back to placeholder"
            )

    def test_isbn_13_digit_normalized_and_stamped(self):
        """Hyphens stripped; digits kept; 13-digit form recognised."""
        out = stamp_pdf_metadata(
            _make_blank_pdf(),
            {"title": "x", "isbn": "978-0-00-000000-0"},
        )
        info = _read_info(out)
        assert info.get("/Subject") == "ISBN 9780000000000"

    def test_isbn_10_digit_recognised(self):
        out = stamp_pdf_metadata(
            _make_blank_pdf(),
            {"title": "x", "isbn": "0-123-45678-9"},
        )
        info = _read_info(out)
        assert info.get("/Subject") == "ISBN 0123456789"

    def test_isbn_10_with_check_X_recognised(self):
        """ISBN-10 check digits can be 'X' (representing 10)."""
        out = stamp_pdf_metadata(
            _make_blank_pdf(),
            {"title": "x", "isbn": "0123-45678-X"},
        )
        info = _read_info(out)
        assert info.get("/Subject") == "ISBN 012345678X"

    def test_publisher_defaults_to_self_published(self):
        out = stamp_pdf_metadata(_make_blank_pdf(), {"title": "x"})
        info = _read_info(out)
        assert info.get("/Creator") == "Self-published"

    def test_publisher_stamped_when_set(self):
        out = stamp_pdf_metadata(
            _make_blank_pdf(),
            {"title": "x", "publisher": "Penguin Random House"},
        )
        info = _read_info(out)
        assert info.get("/Creator") == "Penguin Random House"

    def test_producer_identifies_pipeline(self):
        out = stamp_pdf_metadata(_make_blank_pdf(), {"title": "x"})
        info = _read_info(out)
        # Producer is the software identifier — must be stable so the
        # next agent can grep for it. Don't change the literal without
        # also updating the integration tests that grep this.
        assert info.get("/Producer") == "Bindery WeasyPrint pipeline"

    def test_creation_and_mod_dates_set(self):
        out = stamp_pdf_metadata(_make_blank_pdf(), {"title": "x"})
        info = _read_info(out)
        # PDF date format: D:YYYYMMDDHHmmSSZ
        assert info.get("/CreationDate", "").startswith("D:")
        assert info.get("/ModDate", "").startswith("D:")

    def test_idempotent(self):
        """Stamping a PDF that's already been stamped must produce the
        same metadata (modulo the timestamp). Catches a bug where the
        second pass would somehow append/duplicate keys."""
        book = {"title": "Same Book", "author": "Same Author",
                "isbn": "9780000000000", "publisher": "ACME"}
        first = stamp_pdf_metadata(_make_blank_pdf(), book)
        second = stamp_pdf_metadata(first, book)
        info1 = _read_info(first)
        info2 = _read_info(second)
        # Everything except timestamps must match exactly.
        for k in ("/Title", "/Author", "/Subject", "/Creator", "/Producer"):
            assert info1.get(k) == info2.get(k), (k, info1.get(k), info2.get(k))

    def test_corrupt_input_returns_unchanged(self):
        """Failure tolerance — if pypdf can't read the input (e.g.
        encrypted, truncated), we must NOT raise; we return the bytes
        as-is and log a warning. Failing the whole export over missing
        metadata would be a regression vs the pre-Phase-3.3 behavior."""
        garbage = b"this is not a pdf"
        out = stamp_pdf_metadata(garbage, {"title": "x"})
        assert out == garbage, "corrupt input must round-trip unchanged"

    def test_multi_page_pdf_keeps_page_count(self):
        """Metadata stamping must not drop or duplicate pages."""
        original = _make_blank_pdf(pages=5)
        out = stamp_pdf_metadata(original, {"title": "x"})
        assert len(PdfReader(io.BytesIO(out)).pages) == 5

    def test_unicode_title_round_trips(self):
        """PDF Info dict is officially UTF-16BE. pypdf handles encoding,
        but verify a non-ASCII title round-trips so we don't ship
        mojibake for international titles."""
        out = stamp_pdf_metadata(
            _make_blank_pdf(),
            {"title": "Belinda — A Children's Tale", "author": "Émile Zola"},
        )
        info = _read_info(out)
        assert info.get("/Title") == "Belinda — A Children's Tale"
        assert info.get("/Author") == "Émile Zola"
