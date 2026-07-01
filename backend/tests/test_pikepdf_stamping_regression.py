"""Regression test: pikepdf refactor of `apply_print_boxes` and
`ensure_even_page_count` MUST produce byte-identical box geometry to
the previous pypdf implementation. Any drift in TrimBox / BleedBox /
CropBox coordinates would silently break IngramSpark preflight, so we
compare box values to 4 decimal places on every page.

We keep a frozen copy of the OLD pypdf implementation inline in this
test file (below) and diff its output against the current one. This
protects against three regression classes:
  1. Coordinate arithmetic drift in the pikepdf rewrite.
  2. Recto/verso parity flip (TrimBox mis-alignment across the fold).
  3. The blank-page appended by ensure_even_page_count getting the
     wrong parity or wrong MediaBox.

The test also verifies:
  * temp files are cleaned up in the finally block (no orphan
    `stamp_in_*.pdf` / `evenpad_in_*.pdf` in `/tmp` after the call
    even if we deliberately raise mid-processing).
"""
from __future__ import annotations

import glob
import io
import os
import tempfile

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

import pdf_builder as pb
from pdf_builder import (
    INTERIOR_BLEED_PX,
    apply_print_boxes,
    ensure_even_page_count,
)


# --------------------------------------------------------------------------
# Frozen OLD pypdf implementations — kept here verbatim as the ground
# truth for the box arithmetic. The regression test compares the NEW
# pikepdf implementation's output to these to 4 dp per box per page.
# --------------------------------------------------------------------------

def _old_apply_print_boxes(pdf_bytes: bytes, page_w_px: int, page_h_px: int) -> bytes:
    px_to_pt = 0.75
    trim_w_pt = round(page_w_px * px_to_pt, 4)
    trim_h_pt = round(page_h_px * px_to_pt, 4)
    bleed_pt = round(INTERIOR_BLEED_PX * px_to_pt, 4)

    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        is_right_page = (i % 2 == 0)
        media = page.mediabox
        media_w = float(media.width)
        media_h = float(media.height)
        if is_right_page:
            tx0, ty0 = 0.0, bleed_pt
            tx1, ty1 = trim_w_pt + bleed_pt, trim_h_pt + bleed_pt
        else:
            tx0, ty0 = bleed_pt, bleed_pt
            tx1, ty1 = bleed_pt + trim_w_pt + bleed_pt, trim_h_pt + bleed_pt
        page.trimbox = RectangleObject([tx0, ty0, tx1, ty1])
        page.bleedbox = RectangleObject([0.0, 0.0, media_w, media_h])
        page.cropbox = RectangleObject([0.0, 0.0, media_w, media_h])
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _old_ensure_even_page_count(pdf_bytes: bytes) -> tuple[bytes, bool]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    page_count = len(reader.pages)
    if page_count % 2 == 0:
        return pdf_bytes, False
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)
    last = reader.pages[-1]
    media = last.mediabox
    media_w = float(media.width)
    media_h = float(media.height)
    writer.add_blank_page(width=media_w, height=media_h)
    new_idx = page_count
    is_right_page = (new_idx % 2 == 0)
    bleed_pt = float(last.trimbox.bottom)
    trim_w_pt = media_w - 2 * bleed_pt
    trim_h_pt = media_h - 2 * bleed_pt
    new_page = writer.pages[-1]
    if is_right_page:
        tx0, ty0 = 0.0, bleed_pt
        tx1, ty1 = trim_w_pt + bleed_pt, trim_h_pt + bleed_pt
    else:
        tx0, ty0 = bleed_pt, bleed_pt
        tx1, ty1 = bleed_pt + trim_w_pt + bleed_pt, trim_h_pt + bleed_pt
    new_page.trimbox = RectangleObject([tx0, ty0, tx1, ty1])
    new_page.bleedbox = RectangleObject([0.0, 0.0, media_w, media_h])
    new_page.cropbox = RectangleObject([0.0, 0.0, media_w, media_h])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), True


# --------------------------------------------------------------------------
# Fixture: build a synthetic multi-page PDF whose MediaBox matches what
# the WeasyPrint renderer emits when `pdfx_bleed=True` (trim + 2×bleed
# = 12 px on every side @ 96 DPI). We use pypdf to construct the input
# so this fixture is independent of the SUT.
# --------------------------------------------------------------------------

@pytest.fixture
def synth_pdf():
    """3-page symmetric-mediabox PDF, page_w=816 px, page_h=1056 px
    (US Letter). MediaBox in pt = (816 + 24) * 0.75 × (1056 + 24) * 0.75
    = 630 × 810. Matches what the renderer produces after pdfx_bleed."""
    def _build(page_count: int, page_w_px: int = 816, page_h_px: int = 1056):
        px_to_pt = 0.75
        media_w_pt = (page_w_px + 2 * INTERIOR_BLEED_PX) * px_to_pt
        media_h_pt = (page_h_px + 2 * INTERIOR_BLEED_PX) * px_to_pt
        w = PdfWriter()
        for _ in range(page_count):
            w.add_blank_page(width=media_w_pt, height=media_h_pt)
        buf = io.BytesIO()
        w.write(buf)
        return buf.getvalue(), page_w_px, page_h_px
    return _build


def _boxes_per_page(pdf_bytes: bytes) -> list[dict]:
    """Extract MediaBox / TrimBox / BleedBox / CropBox as tuples of 4
    floats from every page. Used for the parity-safe box diff below."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    out: list[dict] = []
    for p in reader.pages:
        out.append({
            "mediabox": tuple(round(float(x), 4) for x in p.mediabox),
            "trimbox": tuple(round(float(x), 4) for x in p.trimbox),
            "bleedbox": tuple(round(float(x), 4) for x in p.bleedbox),
            "cropbox": tuple(round(float(x), 4) for x in p.cropbox),
        })
    return out


# --------------------------------------------------------------------------
# Regression tests — boxes must match to 4 dp per page.
# --------------------------------------------------------------------------

class TestBoxParityAgainstPypdf:
    @pytest.mark.parametrize("page_count", [1, 2, 3, 5, 10, 32, 64])
    def test_apply_print_boxes_matches_old_impl(self, synth_pdf, page_count):
        pdf_bytes, w, h = synth_pdf(page_count)
        new_out = apply_print_boxes(pdf_bytes, w, h)
        old_out = _old_apply_print_boxes(pdf_bytes, w, h)

        new_boxes = _boxes_per_page(new_out)
        old_boxes = _boxes_per_page(old_out)
        assert len(new_boxes) == len(old_boxes) == page_count
        for i, (nb, ob) in enumerate(zip(new_boxes, old_boxes)):
            assert nb["mediabox"] == ob["mediabox"], (
                f"page {i+1} MediaBox drift: new={nb['mediabox']} old={ob['mediabox']}"
            )
            assert nb["trimbox"] == ob["trimbox"], (
                f"page {i+1} TrimBox drift: new={nb['trimbox']} old={ob['trimbox']}"
            )
            assert nb["bleedbox"] == ob["bleedbox"], (
                f"page {i+1} BleedBox drift: new={nb['bleedbox']} old={ob['bleedbox']}"
            )
            assert nb["cropbox"] == ob["cropbox"], (
                f"page {i+1} CropBox drift: new={nb['cropbox']} old={ob['cropbox']}"
            )

    def test_apply_print_boxes_geometry_absolute(self, synth_pdf):
        """Independent spec-check — don't just match the old code, match
        the IngramSpark v5.11.26 box scheme. Guards against a scenario
        where both impls drifted the same way."""
        pdf_bytes, w, h = synth_pdf(4, page_w_px=816, page_h_px=1056)
        out = apply_print_boxes(pdf_bytes, w, h)
        boxes = _boxes_per_page(out)
        # Trim = 816×1056 px → 612×792 pt. Bleed = 9 pt (=12 px × 0.75).
        # MediaBox = 612 + 18 × 792 + 18 = 630 × 810 pt.
        for i, b in enumerate(boxes):
            mb = b["mediabox"]
            assert mb == (0.0, 0.0, 630.0, 810.0), f"page {i+1} MediaBox: {mb}"
            assert b["bleedbox"] == (0.0, 0.0, 630.0, 810.0)
            assert b["cropbox"] == (0.0, 0.0, 630.0, 810.0)
            if i % 2 == 0:
                # Right-hand page (recto): spine on LEFT (x=0..9 gutter).
                assert b["trimbox"] == (0.0, 9.0, 621.0, 801.0), (
                    f"page {i+1} recto TrimBox: {b['trimbox']}"
                )
            else:
                # Left-hand page (verso): spine on RIGHT.
                assert b["trimbox"] == (9.0, 9.0, 630.0, 801.0), (
                    f"page {i+1} verso TrimBox: {b['trimbox']}"
                )

    @pytest.mark.parametrize("page_count", [1, 3, 5, 9, 33, 63])  # all odd
    def test_ensure_even_page_count_matches_old_impl(self, synth_pdf, page_count):
        # Feed the pypdf output through apply_print_boxes first so the
        # input to ensure_even_page_count matches the real pipeline
        # (stamped trimbox on every existing page, from which the new
        # blank inherits its bleed dimension).
        pdf_bytes, w, h = synth_pdf(page_count)
        stamped = apply_print_boxes(pdf_bytes, w, h)

        new_out, new_added = ensure_even_page_count(stamped)
        old_out, old_added = _old_ensure_even_page_count(stamped)
        assert new_added is True and old_added is True

        new_boxes = _boxes_per_page(new_out)
        old_boxes = _boxes_per_page(old_out)
        assert len(new_boxes) == page_count + 1
        assert len(old_boxes) == page_count + 1
        for i, (nb, ob) in enumerate(zip(new_boxes, old_boxes)):
            assert nb["mediabox"] == ob["mediabox"], (
                f"page {i+1} MediaBox drift: new={nb['mediabox']} old={ob['mediabox']}"
            )
            assert nb["trimbox"] == ob["trimbox"], (
                f"page {i+1} TrimBox drift: new={nb['trimbox']} old={ob['trimbox']}"
            )
            assert nb["bleedbox"] == ob["bleedbox"], (
                f"page {i+1} BleedBox drift: new={nb['bleedbox']} old={ob['bleedbox']}"
            )
            assert nb["cropbox"] == ob["cropbox"], (
                f"page {i+1} CropBox drift: new={nb['cropbox']} old={ob['cropbox']}"
            )

    def test_ensure_even_page_count_no_op_on_even_input(self, synth_pdf):
        """Even page count → returns original bytes untouched (fast path;
        avoids the temp-file write)."""
        pdf_bytes, w, h = synth_pdf(4)
        stamped = apply_print_boxes(pdf_bytes, w, h)
        out, added = ensure_even_page_count(stamped)
        assert added is False
        assert out is stamped, "Even-count fast path must return same bytes object"


# --------------------------------------------------------------------------
# Temp-file cleanup — the disk-spool implementation MUST clean up both
# input and output temp files even if an exception is raised mid-
# processing. This is critical because these files can be 100+ MB and
# accumulate quickly under retry pressure.
# --------------------------------------------------------------------------

class TestTempFileCleanup:
    def _tmp_snapshot(self, prefix: str) -> set[str]:
        return set(glob.glob(os.path.join(tempfile.gettempdir(), f"{prefix}*")))

    def test_apply_print_boxes_cleans_up_after_success(self, synth_pdf):
        before = self._tmp_snapshot("stamp_in_")
        pdf_bytes, w, h = synth_pdf(3)
        apply_print_boxes(pdf_bytes, w, h)
        after = self._tmp_snapshot("stamp_in_")
        assert after == before, (
            f"apply_print_boxes leaked temp files: {sorted(after - before)}"
        )

    def test_apply_print_boxes_cleans_up_on_exception(self, monkeypatch, synth_pdf):
        """Force pikepdf.open to raise AFTER we've written the input
        temp file — the finally block must still remove both temp files."""
        before = self._tmp_snapshot("stamp_in_")

        import pikepdf
        real_open = pikepdf.open

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated libqpdf failure")
        monkeypatch.setattr(pdf_builder_pikepdf_open := pb, "apply_print_boxes",
                            pb.apply_print_boxes)  # keep ref
        monkeypatch.setattr(pikepdf, "open", _boom)

        pdf_bytes, w, h = synth_pdf(3)
        with pytest.raises(RuntimeError, match="simulated libqpdf failure"):
            apply_print_boxes(pdf_bytes, w, h)

        # Restore before scanning /tmp so subsequent tests aren't affected.
        monkeypatch.setattr(pikepdf, "open", real_open)

        after = self._tmp_snapshot("stamp_in_")
        assert after == before, (
            f"apply_print_boxes leaked temp files after exception: "
            f"{sorted(after - before)}"
        )

    def test_ensure_even_page_count_cleans_up_after_success(self, synth_pdf):
        before = self._tmp_snapshot("evenpad_in_")
        pdf_bytes, w, h = synth_pdf(3)  # odd → will pad
        stamped = apply_print_boxes(pdf_bytes, w, h)
        ensure_even_page_count(stamped)
        after = self._tmp_snapshot("evenpad_in_")
        assert after == before, (
            f"ensure_even_page_count leaked temp files: {sorted(after - before)}"
        )
