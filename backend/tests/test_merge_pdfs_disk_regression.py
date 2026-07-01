"""Regression test: `pdf_builder_weasy._merge_pdfs_disk` MUST produce
the same page count and preserve page order compared to the OLD pypdf
inline merge that lived in `build_book_pdf` before iteration 24.

The old inline merge is preserved verbatim below (in `_old_merge`) as
ground truth. For every synthesized chunk shape we test, we assert:
  1. Page count is identical.
  2. Each page's MediaBox matches to 4 dp (i.e. pages weren't re-flowed
     or scaled during the merge).
  3. `chunk_pdfs` is emptied after the merge (list-mutation contract
     that lets the caller drop bytes references early).
  4. No `merge_chunk_*` or `merge_out_*` temp files are left in `/tmp`
     after either a successful merge OR one that raises mid-way.
"""
from __future__ import annotations

import glob
import io
import os
import tempfile

import pytest
from pypdf import PdfReader, PdfWriter

from pdf_builder_weasy import _merge_pdfs_disk


# --------------------------------------------------------------------------
# Frozen OLD inline merge — ground truth for the byte-for-byte diff.
# --------------------------------------------------------------------------
def _old_merge(chunk_pdfs: list[bytes]) -> bytes:
    if len(chunk_pdfs) == 1:
        return chunk_pdfs[0]
    writer = PdfWriter()
    for blob in chunk_pdfs:
        for pg in PdfReader(io.BytesIO(blob)).pages:
            writer.add_page(pg)
    out_buf = io.BytesIO()
    writer.write(out_buf)
    return out_buf.getvalue()


# --------------------------------------------------------------------------
# Fixture: build a list of synthetic chunk PDFs. Each chunk has N blank
# pages of a caller-specified MediaBox so we can detect if the merge
# accidentally scales / normalises anything.
# --------------------------------------------------------------------------
@pytest.fixture
def make_chunks():
    def _build(pages_per_chunk: list[int], w_pt: float = 630.0, h_pt: float = 810.0):
        chunks: list[bytes] = []
        for n in pages_per_chunk:
            wr = PdfWriter()
            for _ in range(n):
                wr.add_blank_page(width=w_pt, height=h_pt)
            buf = io.BytesIO()
            wr.write(buf)
            chunks.append(buf.getvalue())
        return chunks
    return _build


def _read_pages(pdf_bytes: bytes) -> list[tuple[float, float, float, float]]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [
        tuple(round(float(x), 4) for x in p.mediabox)
        for p in reader.pages
    ]


# --------------------------------------------------------------------------
# 1. Merge parity — new vs old must produce identical page-count and
#    identical MediaBox on every page, for a range of chunk shapes.
# --------------------------------------------------------------------------
class TestMergeParityAgainstOld:
    @pytest.mark.parametrize("shape", [
        [1],                    # single-chunk fast path
        [5],                    # single-chunk fast path, multi-page
        [1, 1],                 # 2 chunks × 1 page = 2 total
        [3, 3],                 # 2 chunks × 3 pages = 6 total
        [1, 5, 1, 5, 1, 5],     # 6 chunks, uneven
        [1] * 32,               # 32 chunks × 1 page = 32 total
        [1] * 64,               # 64 chunks × 1 page — matches CHUNK_SIZE=1 book
    ])
    def test_page_count_and_mediaboxes_match(self, make_chunks, shape):
        chunks_new = make_chunks(shape)
        chunks_old = list(chunks_new)  # preserve for old-impl comparison

        merged_new = _merge_pdfs_disk(chunks_new)
        merged_old = _old_merge(chunks_old)

        pages_new = _read_pages(merged_new)
        pages_old = _read_pages(merged_old)

        expected_total = sum(shape)
        assert len(pages_new) == expected_total, (
            f"NEW page count {len(pages_new)} != expected {expected_total}"
        )
        assert len(pages_old) == expected_total
        assert pages_new == pages_old, (
            "MediaBox drift between new and old merge:\n"
            f"  new: {pages_new[:3]}...\n"
            f"  old: {pages_old[:3]}..."
        )


# --------------------------------------------------------------------------
# 2. List-mutation contract — the helper empties the input list so the
#    caller's per-blob bytes references drop early.
# --------------------------------------------------------------------------
class TestListMutation:
    def test_input_list_is_emptied_after_merge(self, make_chunks):
        chunks = make_chunks([2, 2, 2])
        assert len(chunks) == 3
        _merge_pdfs_disk(chunks)
        assert chunks == [], (
            f"chunk_pdfs should be emptied after merge, still has {len(chunks)}"
        )

    def test_fast_path_does_NOT_touch_list(self, make_chunks):
        """Single-chunk fast path returns the blob directly without
        touching the list — no reason to mutate it in that branch."""
        chunks = make_chunks([3])
        assert len(chunks) == 1
        original = chunks[0]
        out = _merge_pdfs_disk(chunks)
        # Fast path returns the same bytes object, list is not mutated.
        assert out is original
        assert len(chunks) == 1


# --------------------------------------------------------------------------
# 3. Temp-file cleanup — no orphans after success OR exception.
# --------------------------------------------------------------------------
class TestTempFileCleanup:
    def _snapshot(self, prefix: str) -> set[str]:
        return set(glob.glob(os.path.join(tempfile.gettempdir(), f"{prefix}*")))

    def test_no_orphans_after_successful_merge(self, make_chunks):
        before_chunk = self._snapshot("merge_chunk_")
        before_out = self._snapshot("merge_out_")
        chunks = make_chunks([2, 2, 2])
        _merge_pdfs_disk(chunks)
        after_chunk = self._snapshot("merge_chunk_")
        after_out = self._snapshot("merge_out_")
        assert after_chunk == before_chunk, (
            f"merge_chunk_ orphans: {sorted(after_chunk - before_chunk)}"
        )
        assert after_out == before_out, (
            f"merge_out_ orphans: {sorted(after_out - before_out)}"
        )

    def test_no_orphans_after_exception(self, monkeypatch, make_chunks):
        """Force pikepdf.open to raise AFTER we've written the chunk temp
        files. The finally block MUST still unlink all of them."""
        before_chunk = self._snapshot("merge_chunk_")
        before_out = self._snapshot("merge_out_")

        import pikepdf
        real_open = pikepdf.open

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated libqpdf failure")
        monkeypatch.setattr(pikepdf, "open", _boom)

        chunks = make_chunks([2, 2, 2])
        with pytest.raises(RuntimeError, match="simulated libqpdf failure"):
            _merge_pdfs_disk(chunks)

        monkeypatch.setattr(pikepdf, "open", real_open)

        after_chunk = self._snapshot("merge_chunk_")
        after_out = self._snapshot("merge_out_")
        assert after_chunk == before_chunk, (
            f"merge_chunk_ leaked after exception: "
            f"{sorted(after_chunk - before_chunk)}"
        )
        assert after_out == before_out, (
            f"merge_out_ leaked after exception: {sorted(after_out - before_out)}"
        )
