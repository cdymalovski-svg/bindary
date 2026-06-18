"""WeasyPrint-based PDF renderer for Bindery.

Drop-in replacement for the Chromium-based `pdf_builder` whenever the
Playwright subprocess is unreliable on the host pod (cannot be cancelled
by `asyncio.wait_for` — Playwright's launch() internally spawns a Node
process that doesn't respect Python's cancellation). WeasyPrint renders
the same HTML to PDF entirely in-process using Cairo + Pango — no
subprocess, no async-cancellation gotchas, no infinite hangs.

Trade-offs vs Chromium:
  - No JavaScript (we don't use any in the editor's static block layout).
  - Slightly different flex/grid edge cases (we use absolute positioning
    for blocks, which WeasyPrint handles identically to Chrome).
  - Pango font kerning may differ subtly from Chrome's Skia text engine.
  - Worth it: never hangs, never needs retries, never needs subprocess
    health checks.

Architecture: re-uses `pdf_builder._build_html` (interior pages) and
`pdf_builder._build_cover_spread_html` (cover spread) so visual output
matches what Chromium would produce as closely as possible. Image
fetching is wired through a custom `url_fetcher` so the same Object
Storage path the Chromium pipeline uses is also honoured here."""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Callable, Optional

from weasyprint import HTML, CSS  # noqa: F401  CSS reserved for future per-export overrides

import pdf_builder as _pb

log = logging.getLogger("bindery.pdf_weasy")

# Hard wall-clock cap on the render. WeasyPrint shouldn't hang, but we
# still bound it so a pathological book (e.g. corrupt image) can never
# stall the worker. 5 min is generous: a 100-page book with full-bleed
# illustrations renders in ~30s on modest hardware.
WEASY_TIMEOUT_S = 300.0


def _build_url_fetcher(
    get_image: Callable[[str], tuple[bytes, str]],
    public_base_url: Optional[str],
    dpi: int = 300,
):
    """Build a `url_fetcher` callable WeasyPrint will use to resolve every
    external URL referenced by the HTML (image src=, CSS font @import,
    etc.). We short-circuit the /api/files/{path} URLs to the same
    in-pod `get_image` helper Chromium uses, applying the same DPI
    downscaling so the embedded image dimensions match.

    Anything else (e.g. Google Fonts) falls back to WeasyPrint's default
    HTTP fetcher."""
    from weasyprint import default_url_fetcher
    marker = "/api/files/"
    long_edge_cap = _pb.DPI_LONG_EDGE_CAPS.get(_pb._resolve_dpi(dpi), 3300)
    jpeg_quality = 92 if dpi >= 450 else 85

    def fetcher(url: str):
        try:
            if marker in url:
                key = url.split(marker, 1)[1].split("?", 1)[0]
                raw, ctype = get_image(key)
                if not raw:
                    log.warning("WeasyPrint: empty bytes for %s", key)
                    # Returning None would crash WeasyPrint; instead
                    # return a 1×1 transparent PNG so the layout still
                    # works (image just appears blank).
                    return {"string": _BLANK_PNG, "mime_type": "image/png"}
                # Downscale aggressively — WeasyPrint embeds image bytes
                # directly into the PDF, so a 12000×12000 PNG would
                # produce a 200 MB file. Cap at the chosen DPI long-edge
                # and re-encode to JPEG (or keep PNG for transparency).
                data, mime = _maybe_downscale(raw, ctype or "image/png",
                                              long_edge_cap, jpeg_quality)
                return {"string": data, "mime_type": mime}
            # Google Fonts WOFF2 — serve from local cache to avoid
            # the synchronous network fetch that Support identified
            # as the full-book hang cause. The inlined CSS already
            # base64-embeds every font face, so this branch only
            # fires if WeasyPrint encounters a stray URL we missed.
            if "fonts.gstatic.com" in url:
                from fonts_cache import font_url_cache_get
                cached = font_url_cache_get(url)
                if cached is not None:
                    return {"string": cached, "mime_type": "font/woff2"}
                # Not cached — return empty so the font falls back to
                # system rather than blocking on the slow Google fetch.
                log.warning("WeasyPrint: blocking %s (not in cache)", url)
                return {"string": b"", "mime_type": "font/woff2"}
            # Google Fonts CSS — same defence. Always blocked; the
            # inlined CSS in HTML supersedes it.
            if "fonts.googleapis.com" in url:
                log.warning("WeasyPrint: blocking Google Fonts CSS fetch (%s)", url)
                return {"string": b"", "mime_type": "text/css"}
        except Exception as e:
            log.warning("WeasyPrint url_fetcher failed for %s: %s", url, e)
            return {"string": _BLANK_PNG, "mime_type": "image/png"}
        return default_url_fetcher(url)

    return fetcher


# 1×1 transparent PNG (87 bytes) — emergency fallback when an image
# can't be fetched. Better than crashing the whole export.
_BLANK_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63600100000005000148afa4710000000049454e44ae42"
    "6082"
)


def _maybe_downscale(raw: bytes, ctype: str, max_dim: int, quality: int) -> tuple[bytes, str]:
    """Mirror of `pdf_builder._maybe_downscale` but with explicit
    parameters (so WeasyPrint can request a different DPI ceiling than
    the Chromium pipeline if needed). Reused from pdf_builder when
    possible to keep behaviour identical."""
    try:
        from PIL import Image
    except Exception:
        return raw, ctype
    if len(raw) < 200_000 and max_dim >= 6600:
        # Tiny images don't need re-encoding even at maximum DPI — no
        # downscale would happen anyway.
        return raw, ctype
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        return raw, ctype
    w, h = im.size
    long_edge = max(w, h)
    if long_edge <= max_dim:
        return raw, ctype
    scale = max_dim / long_edge
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    resized = im.resize(new_size, Image.LANCZOS)
    buf = io.BytesIO()
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    if has_alpha:
        # Preserve transparency — keep PNG.
        resized.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png"
    resized.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), "image/jpeg"


async def build_book_pdf(
    book: dict,
    get_image: Callable[[str], tuple[bytes, str]],
    public_base_url: Optional[str] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    start_page: Optional[int] = None,
    end_page: Optional[int] = None,
    pdfx_bleed: bool = True,
    dpi: int = 300,
) -> bytes:
    """Render the whole book (or a page slice) to PDF via WeasyPrint.

    Same signature as `pdf_builder.build_book_pdf` — the worker in
    server.py can swap between engines without other changes."""
    def _emit(stage: str):
        if progress_cb:
            try:
                progress_cb(stage)
            except Exception:
                pass

    _emit("preparing html")
    # Convert 1-indexed inclusive (start_page, end_page) → 0-indexed
    # half-open [s, e) for `_build_html`. Matches the Chromium pipeline
    # in pdf_builder.build_book_pdf (line 1193).
    pages = book.get("pages") or []
    total = len(pages)
    s = 0 if start_page is None else max(0, int(start_page) - 1)
    e = total if end_page is None else min(total, int(end_page))
    if s >= e:
        s, e = 0, min(total, 1)
    html_text, outer_w, outer_h = _pb._build_html(
        book, {}, page_range=(s, e), pdfx_bleed=pdfx_bleed,
    )

    base = (public_base_url or "").rstrip("/") + "/"
    fetcher = _build_url_fetcher(get_image, public_base_url, dpi=dpi)

    def _render_sync() -> bytes:
        _emit("rendering with weasyprint")
        # WeasyPrint's `HTML().write_pdf()` is fully synchronous and
        # cannot be cancelled mid-render. We run it in a worker thread
        # so the asyncio loop stays responsive (heartbeats, polling).
        doc = HTML(string=html_text, base_url=base, url_fetcher=fetcher)
        # `presentational_hints=False` — our HTML uses inline styles so
        # we don't want WeasyPrint inferring extra defaults that could
        # differ from Chromium.
        out = io.BytesIO()
        doc.write_pdf(target=out, presentational_hints=False)
        return out.getvalue()

    # Bound the render with `asyncio.wait_for` so a pathological book
    # can't stall the worker forever. WeasyPrint itself never hangs —
    # this is purely defensive against e.g. an infinite-loop CSS bug.
    pdf_bytes = await asyncio.wait_for(
        asyncio.to_thread(_render_sync),
        timeout=WEASY_TIMEOUT_S,
    )

    # Stamp IngramSpark print boxes (MediaBox / TrimBox / BleedBox) the
    # same way the Chromium pipeline does. Then auto-pad to even page
    # count for full-book exports so the printer gets a left/right
    # parity-correct book.
    if pdfx_bleed:
        _emit("stamping print boxes")
        try:
            size_key = book.get("page_size", "a4")
            if size_key not in _pb.PAGE_SIZES_PX:
                size_key = "a4"
            page_w_trim, page_h_trim = _pb.PAGE_SIZES_PX[size_key]
            pdf_bytes = _pb.apply_print_boxes(pdf_bytes, page_w_trim, page_h_trim)
        except Exception as e:
            log.warning("WeasyPrint: apply_print_boxes failed (%s) — returning raw bytes", e)
        # Even-page padding ONLY for full-book exports — page-range
        # exports keep their exact page count so the user can verify a
        # chapter without auto-padding.
        is_full_book = (start_page is None and end_page is None)
        if is_full_book:
            try:
                pdf_bytes, appended = _pb.ensure_even_page_count(pdf_bytes)
                if appended:
                    _emit("padded to even page count")
            except Exception as e:
                log.warning("WeasyPrint: ensure_even_page_count failed: %s", e)

    _emit("done")
    return pdf_bytes


async def build_cover_spread_pdf(
    book: dict,
    get_image: Callable[[str], tuple[bytes, str]],
    public_base_url: Optional[str] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    spine_width_in: Optional[float] = None,
    paper_caliper_in: float = _pb.DEFAULT_PAPER_CALIPER_IN,
    binding: str = "perfect",
    dpi: int = 300,
) -> bytes:
    """WeasyPrint version of the cover-spread renderer. Same geometry,
    same bleed/wrap math — just rendered via Cairo instead of Chromium."""
    def _emit(stage: str):
        if progress_cb:
            try:
                progress_cb(stage)
            except Exception:
                pass

    _emit("preparing cover html")
    html_text, total_w, total_h = _pb._build_cover_spread_html(
        book, {}, spine_width_in=spine_width_in,
        paper_caliper_in=paper_caliper_in, binding=binding,
    )

    base = (public_base_url or "").rstrip("/") + "/"
    fetcher = _build_url_fetcher(get_image, public_base_url, dpi=dpi)

    def _render_sync() -> bytes:
        _emit("rendering cover with weasyprint")
        doc = HTML(string=html_text, base_url=base, url_fetcher=fetcher)
        out = io.BytesIO()
        doc.write_pdf(target=out, presentational_hints=False)
        return out.getvalue()

    pdf_bytes = await asyncio.wait_for(
        asyncio.to_thread(_render_sync),
        timeout=WEASY_TIMEOUT_S,
    )
    _emit("done")
    return pdf_bytes
