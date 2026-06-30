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
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeout
from typing import Callable, Optional

from weasyprint import HTML, CSS  # noqa: F401  CSS reserved for future per-export overrides

import pdf_builder as _pb

# Dedicated executor for image fetches inside WeasyPrint's `url_fetcher`.
# We call it from a SYNC context (WeasyPrint runs in `asyncio.to_thread`),
# so we cannot use asyncio.wait_for here — we need a thread-pool with a
# `future.result(timeout=...)` we can bail on. 4 workers lets multiple
# images in a single page fetch in parallel without overwhelming the
# pod's network stack.
_IMG_FETCH_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="wp-img-fetch")
# Per-image hard ceiling — beyond this, the storage backend is presumed
# stuck and we substitute a blank PNG. WeasyPrint then continues with
# the next image so the page still renders.
_IMG_FETCH_TIMEOUT_S = 25.0

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
    cache: Optional[dict] = None,
):
    """Build a `url_fetcher` callable WeasyPrint will use to resolve every
    external URL referenced by the HTML (image src=, CSS font @import,
    etc.). We short-circuit the /api/files/{path} URLs to the same
    in-pod `get_image` helper Chromium uses, applying the same DPI
    downscaling so the embedded image dimensions match.

    `cache` (optional) is a shared dict that memoises (key → (bytes,
    mime)) across multiple url_fetcher calls. Critical for chunked
    rendering: an image used on pages 3 and 7 gets fetched and
    downscaled exactly once. Pass an empty dict from the caller; it
    gets populated in place.

    Anything else (e.g. Google Fonts) falls back to WeasyPrint's default
    HTTP fetcher."""
    from weasyprint import default_url_fetcher
    marker = "/api/files/"
    long_edge_cap = _pb.DPI_LONG_EDGE_CAPS.get(_pb._resolve_dpi(dpi), 3300)
    jpeg_quality = 92 if dpi >= 450 else 85
    img_cache: dict = cache if cache is not None else {}

    def fetcher(url: str):
        try:
            if marker in url:
                key = url.split(marker, 1)[1].split("?", 1)[0]
                # Memoised — same image used on multiple pages is
                # fetched + downscaled exactly once per job.
                cached = img_cache.get(key)
                if cached is not None:
                    return {"string": cached[0], "mime_type": cached[1]}
                # Bounded fetch — a slow/hung object-storage backend
                # can't wedge the WeasyPrint render thread. Beyond the
                # timeout we substitute a blank PNG so the page still
                # renders (just without that image).
                fetch_started = time.monotonic()
                try:
                    future = _IMG_FETCH_EXECUTOR.submit(get_image, key)
                    raw, ctype = future.result(timeout=_IMG_FETCH_TIMEOUT_S)
                except _FuturesTimeout:
                    log.warning(
                        "WeasyPrint: image fetch timed out after %.0fs for %s — "
                        "substituting blank PNG",
                        _IMG_FETCH_TIMEOUT_S, key,
                    )
                    img_cache[key] = (_BLANK_PNG, "image/png")
                    return {"string": _BLANK_PNG, "mime_type": "image/png"}
                except Exception as fe:
                    log.warning(
                        "WeasyPrint: image fetch errored for %s: %s — "
                        "substituting blank PNG", key, fe,
                    )
                    img_cache[key] = (_BLANK_PNG, "image/png")
                    return {"string": _BLANK_PNG, "mime_type": "image/png"}
                fetch_ms = (time.monotonic() - fetch_started) * 1000
                if fetch_ms > 3000:
                    log.warning(
                        "WeasyPrint: SLOW image fetch %.0fms for %s (size=%d)",
                        fetch_ms, key, len(raw or b""),
                    )
                if not raw:
                    log.warning("WeasyPrint: empty bytes for %s", key)
                    # Returning None would crash WeasyPrint; instead
                    # return a 1×1 transparent PNG so the layout still
                    # works (image just appears blank).
                    img_cache[key] = (_BLANK_PNG, "image/png")
                    return {"string": _BLANK_PNG, "mime_type": "image/png"}
                # Downscale aggressively — WeasyPrint embeds image bytes
                # directly into the PDF, so a 12000×12000 PNG would
                # produce a 200 MB file. Cap at the chosen DPI long-edge
                # and re-encode to JPEG (or keep PNG for transparency).
                try:
                    data, mime = _maybe_downscale(raw, ctype or "image/png",
                                                  long_edge_cap, jpeg_quality)
                except Exception as de:
                    log.warning(
                        "WeasyPrint: downscale failed for %s (%s) — using raw bytes",
                        key, de,
                    )
                    data, mime = raw, ctype or "image/png"
                img_cache[key] = (data, mime)
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
    path_existence_check: Optional[Callable[[list], "asyncio.Future"]] = None,
) -> bytes:
    """Render the whole book (or a page slice) to PDF via WeasyPrint.

    Same signature as `pdf_builder.build_book_pdf` — the worker in
    server.py can swap between engines without other changes.

    `path_existence_check` — optional async callback `(paths) -> set[str]`
    that returns the subset of `paths` which have NO corresponding row
    in db.files. The caller (server.py) wires this; pdf_builder_weasy
    can't import `db` itself without a circular dependency, and the
    builder shouldn't know about Mongo at all. When provided, the
    callback runs once before the pre-fetch loop and surfaces
    orphaned / deleted image references via a WARNING log per missing
    path. It does NOT block the export — the existing blank-PNG
    fallback in the fetch path handles 404s downstream — it only makes
    the failure mode visible at the start instead of mid-fetch."""
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

    base = (public_base_url or "").rstrip("/") + "/"

    # Chunked rendering — render `CHUNK_SIZE` pages per WeasyPrint call,
    # then merge with pypdf. Big art-heavy books take 5-15s per page in
    # WeasyPrint (Cairo's image compositing is CPU-bound); rendering
    # them all in one `write_pdf()` blew through the 10-minute frontend
    # polling deadline. Chunking gives us:
    #   - Visible progress between chunks (stage_at updates per chunk)
    #   - Bounded memory (no big intermediate page tree)
    #   - Failures isolated to a small page range, not the whole book
    # CHUNK_SIZE=1 — fully isolates every page. Production debugging on
    # art-heavy books (5+ full-bleed 300-DPI illustrations) showed that
    # even two-page chunks could exceed the 240s budget when both pages
    # were heavy. With single-page chunks, a problem page can't drag
    # neighbors down, the per-page timing log surfaces exactly which
    # page is slow, and the emergency blank-page fallback below can
    # substitute one bad page rather than killing the whole job.
    CHUNK_SIZE = 1
    page_count = e - s
    chunks = [(s + i, min(s + i + CHUNK_SIZE, e)) for i in range(0, page_count, CHUNK_SIZE)]
    log.info(
        "WeasyPrint: rendering %d pages in %d chunks of up to %d",
        page_count, len(chunks), CHUNK_SIZE,
    )

    # Per-image cache for the whole job — every image is fetched +
    # downscaled exactly once even if it appears on multiple pages.
    # Massively reduces wall-clock on books that re-use a logo /
    # template image.
    job_image_cache: dict[str, tuple[bytes, str]] = {}

    # ── PRE-FETCH PHASE ──────────────────────────────────────────────
    # Production debugging revealed that WeasyPrint can wedge inside
    # `write_pdf()` when its internal url_fetcher blocks on slow Object
    # Storage reads — heartbeats stop ticking because the render thread
    # holds the GIL while it waits on network I/O it issued itself.
    # Fix: fetch every image referenced by this page-range BEFORE we
    # call WeasyPrint. Then the in-render url_fetcher is a pure cache
    # lookup (microseconds), never blocks, and the heartbeat keeps
    # flowing. We also get visible per-image stage updates the user
    # sees in the toast ("prefetching image 3/12").
    long_edge_cap = _pb.DPI_LONG_EDGE_CAPS.get(_pb._resolve_dpi(dpi), 3300)
    jpeg_quality = 92 if dpi >= 450 else 85
    image_paths_in_range: list[str] = []
    seen_paths: set[str] = set()
    for page_idx in range(s, e):
        if page_idx < 0 or page_idx >= len(pages):
            continue
        for block in (pages[page_idx].get("blocks") or []):
            if block.get("type") != "image":
                continue
            p = _pb._resolve_image_path(block)
            if not p or p in seen_paths:
                continue
            seen_paths.add(p)
            image_paths_in_range.append(p)

    if image_paths_in_range:
        # Orphan / deleted-asset surfacing — runs once before any fetch
        # begins. Catches paths the editor still references but that no
        # longer have a row in db.files (deleted by the user, GC'd by
        # a future cleanup job, or the result of importing a book from
        # another environment). Logs one WARNING per missing path so an
        # operator can grep the supervisor output and see exactly which
        # blocks have orphaned references. Does NOT alter the export —
        # the existing blank-PNG fallback in `_fetch_one` below handles
        # the actual fetch failure; this just makes the cause visible
        # at the start instead of mid-fetch.
        if path_existence_check:
            try:
                missing_paths = await path_existence_check(list(image_paths_in_range))
                if missing_paths:
                    log.warning(
                        "WeasyPrint: %d image path(s) referenced by the book have "
                        "no db.files record (deleted or orphaned): %s",
                        len(missing_paths), sorted(missing_paths),
                    )
            except Exception as ve:
                # Validation failure must NOT block the export — it's
                # diagnostic-only. If db.files is unreachable we still
                # want the export to complete from object storage.
                log.warning("WeasyPrint: path_existence_check failed: %s", ve)

        log.info(
            "WeasyPrint: pre-fetching %d unique images for pages %d-%d "
            "(concurrency=%d)",
            len(image_paths_in_range), s + 1, e,
            _IMG_FETCH_EXECUTOR._max_workers,
        )
        # Concurrent pre-fetch — bounded by a semaphore at the SAME size
        # as the executor (4), so we never have more than 4 images decoded
        # in memory simultaneously (each can be ~25-30 MB at 2625×2625 RGB
        # mid-resize). The semaphore is acquired/released per-image, not
        # per-batch, so a single slow image (e.g. one hitting the 25 s
        # timeout) never blocks the next image from starting — it just
        # holds its own slot while the others churn through.  This is
        # what naive batching of 4-at-a-time would NOT give us (head of
        # line blocking on the slow image in each batch).
        prefetch_t0 = time.monotonic()
        prefetch_total = len(image_paths_in_range)
        # Counter shared across coroutines, mutated only in the asyncio
        # event-loop thread (no lock needed). Used by `prefetching image
        # N/M` toast updates that count COMPLETIONS, not array index, so
        # users see real progress as fetches finish in non-deterministic
        # order.
        prefetch_done = {"n": 0}
        prefetch_blank_fallbacks = {"n": 0}
        sem = asyncio.Semaphore(_IMG_FETCH_EXECUTOR._max_workers)

        async def _fetch_one(idx: int, key: str) -> None:
            async with sem:
                try:
                    fut = _IMG_FETCH_EXECUTOR.submit(get_image, key)
                    raw, ctype = await asyncio.wait_for(
                        asyncio.wrap_future(fut), timeout=_IMG_FETCH_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    log.warning(
                        "WeasyPrint: pre-fetch timed out (%ss) for %s — using blank PNG",
                        _IMG_FETCH_TIMEOUT_S, key,
                    )
                    job_image_cache[key] = (_BLANK_PNG, "image/png")
                    prefetch_blank_fallbacks["n"] += 1
                    _bump_prefetch_progress()
                    return
                except Exception as fe:
                    log.warning("WeasyPrint: pre-fetch errored for %s: %s", key, fe)
                    job_image_cache[key] = (_BLANK_PNG, "image/png")
                    prefetch_blank_fallbacks["n"] += 1
                    _bump_prefetch_progress()
                    return
                if not raw:
                    log.warning(
                        "WeasyPrint: pre-fetch returned empty bytes for %s — "
                        "using blank PNG (asset may be deleted from storage)",
                        key,
                    )
                    job_image_cache[key] = (_BLANK_PNG, "image/png")
                    prefetch_blank_fallbacks["n"] += 1
                    _bump_prefetch_progress()
                    return
                # Downscale in a thread so the event loop keeps spinning
                # and the heartbeat ticks even for huge images.
                try:
                    data, mime = await asyncio.wait_for(
                        asyncio.to_thread(
                            _maybe_downscale, raw, ctype or "image/png",
                            long_edge_cap, jpeg_quality,
                        ),
                        timeout=60.0,
                    )
                except Exception as de:
                    log.warning(
                        "WeasyPrint: pre-fetch downscale failed for %s (%s) — using raw bytes",
                        key, de,
                    )
                    data, mime = raw, ctype or "image/png"
                job_image_cache[key] = (data, mime)
                log.info(
                    "WeasyPrint: pre-fetched %s (%d bytes → %d bytes, %s)",
                    key, len(raw), len(data), mime,
                )
                _bump_prefetch_progress()

        def _bump_prefetch_progress() -> None:
            # Called on completion (success OR fallback) of every fetch.
            # Counts COMPLETIONS so the toast reads "prefetching image
            # 7/12" reflecting actual progress, not a fixed array index.
            prefetch_done["n"] += 1
            _emit(f"prefetching image {prefetch_done['n']}/{prefetch_total}")

        # Kick off all fetches; the semaphore caps live concurrency at 4.
        # Any unexpected exception still completes the gather because we
        # handle EVERY failure mode inside `_fetch_one` by writing a
        # blank-PNG fallback to the cache, so `return_exceptions=True`
        # here is purely defensive against asyncio bugs.
        await asyncio.gather(
            *[_fetch_one(idx, key) for idx, key in enumerate(image_paths_in_range)],
            return_exceptions=True,
        )
        prefetch_elapsed_s = time.monotonic() - prefetch_t0
        prefetch_per_image_ms = (
            (prefetch_elapsed_s * 1000) / prefetch_total
            if prefetch_total else 0.0
        )
        log.info(
            "WeasyPrint: pre-fetch phase finished in %.2fs (%d images, "
            "avg %.0f ms/image, %d blank-PNG fallbacks)",
            prefetch_elapsed_s, prefetch_total,
            prefetch_per_image_ms, prefetch_blank_fallbacks["n"],
        )
    else:
        prefetch_elapsed_s = 0.0
        prefetch_total = 0
        prefetch_blank_fallbacks = {"n": 0}
    # ── END PRE-FETCH PHASE ──────────────────────────────────────────

    job_fetcher = _build_url_fetcher(get_image, public_base_url, dpi=dpi, cache=job_image_cache)

    # ── FONT SUBSETTING ────────────────────────────────────────────────
    # Scan every block / preset / inline-style for font references and
    # build the set ONCE per export job. Threaded into every per-page
    # `_build_html` call so the embedded @font-face block contains only
    # the fonts actually used by THIS book — a parser-tax cut from 9 s/
    # page (369 unused rules) to ~16 ms/page (the actual Cairo render
    # cost). See pdf_builder._extract_used_font_families for the surface
    # list and pdf_builder._google_fonts_css for the subset logic.
    used_font_families, missing_font_families = _pb._extract_used_font_families(book)
    log.info(
        "WeasyPrint: font subset = %d families (%s); %d referenced "
        "families missing from Google Fonts cache: %s",
        len(used_font_families), sorted(used_font_families),
        len(missing_font_families),
        sorted(missing_font_families) if missing_font_families else "none",
    )

    def _render_chunk_sync(chunk_start: int, chunk_end: int) -> bytes:
        chunk_html, _w, _h = _pb._build_html(
            book, {}, page_range=(chunk_start, chunk_end), pdfx_bleed=pdfx_bleed,
            used_font_families=used_font_families,
        )
        doc = HTML(string=chunk_html, base_url=base, url_fetcher=job_fetcher)
        out = io.BytesIO()
        doc.write_pdf(target=out, presentational_hints=False)
        return out.getvalue()

    def _render_blank_page_sync(chunk_start: int, chunk_end: int) -> bytes:
        """Last-resort fallback — render the page-range with all images
        and content stripped, just the page geometry. Used only when a
        real render times out so the user still gets a contiguous PDF
        with placeholders for the bad pages, rather than nothing."""
        size_key = book.get("page_size", "a4")
        if size_key not in _pb.PAGE_SIZES_PX:
            size_key = "a4"
        w_px, h_px = _pb.PAGE_SIZES_PX[size_key]
        # Inline minimal HTML: one empty page per slot, exact trim size.
        page_count_local = max(1, chunk_end - chunk_start)
        page_div = (
            f'<div style="width:{w_px}px;height:{h_px}px;page-break-after:always;'
            f'background:white;display:flex;align-items:center;justify-content:center;'
            f'color:#aaa;font-family:sans-serif;font-size:14px;">'
            f'[page could not be rendered]</div>'
        )
        html_text = (
            f'<!doctype html><html><head><style>@page{{size:{w_px}px {h_px}px;margin:0}}'
            f'body{{margin:0}}</style></head><body>'
            + (page_div * page_count_local)
            + "</body></html>"
        )
        doc = HTML(string=html_text, base_url=base, url_fetcher=job_fetcher)
        out = io.BytesIO()
        doc.write_pdf(target=out, presentational_hints=False)
        return out.getvalue()

    chunk_pdfs: list[bytes] = []
    # Phase #4 instrumentation: track render-phase wall-clock + count of
    # placeholder fallbacks so the end-of-job summary surfaces silent
    # quality regressions (a "fast" book that secretly substituted six
    # blank pages is a worse outcome than a slow book that rendered
    # everything — the summary line must make that visible at a glance).
    render_t0 = time.monotonic()
    render_placeholder_count = 0
    for idx, (cs, ce) in enumerate(chunks):
        # Emit a heartbeat task during this chunk so the frontend's
        # stage-idle deadline (4 min on the latest UI; 10 min wall
        # clock on older UIs) never fires on a legitimately rendering
        # chunk. Heartbeat ticks every 10s with cumulative elapsed
        # time, which counts as a "stage change" to the polling UI.
        chunk_label = f"rendering page {cs + 1}/{e}"
        _emit(chunk_label)
        page_t0 = time.monotonic()
        stop_hb = asyncio.Event()

        async def _heartbeat(label=chunk_label):
            elapsed = 0
            while not stop_hb.is_set():
                try:
                    await asyncio.wait_for(stop_hb.wait(), timeout=10.0)
                    return
                except asyncio.TimeoutError:
                    pass
                elapsed += 10
                try:
                    _emit(f"{label} · {elapsed}s")
                except Exception:
                    pass

        hb_task = asyncio.create_task(_heartbeat())
        # Per-page budget — 240s is enormous (typical page renders in
        # 5-15s) but it gives even a worst-case pod (cold CPU, full-bleed
        # 600-DPI illustration) plenty of headroom before we declare the
        # page pathological. With CHUNK_SIZE=1 we lose only that one page
        # to a blank placeholder; the rest of the book renders normally.
        try:
            chunk_bytes = await asyncio.wait_for(
                asyncio.to_thread(_render_chunk_sync, cs, ce),
                timeout=240.0,
            )
            chunk_pdfs.append(chunk_bytes)
            page_ms = (time.monotonic() - page_t0) * 1000
            log.info(
                "WeasyPrint: page %d/%d rendered in %.0fms",
                cs + 1, e, page_ms,
            )
            if page_ms > 60_000:
                log.warning(
                    "WeasyPrint: page %d/%d was SLOW (%.1fs) — check for "
                    "oversized images or complex CSS on this page",
                    cs + 1, e, page_ms / 1000,
                )
        except asyncio.TimeoutError:
            # The page exceeded 240s. Substitute a blank placeholder so
            # the rest of the book still exports — far better than the
            # whole job failing because of one bad page. The user gets
            # a PDF with a clearly-marked blank slot and can investigate
            # that specific page in the editor.
            log.error(
                "WeasyPrint: page %d/%d exceeded 240s render budget — "
                "substituting blank placeholder so the export can complete",
                cs + 1, e,
            )
            _emit(f"page {cs + 1} too slow — using placeholder")
            render_placeholder_count += 1
            try:
                blank_bytes = await asyncio.wait_for(
                    asyncio.to_thread(_render_blank_page_sync, cs, ce),
                    timeout=30.0,
                )
                chunk_pdfs.append(blank_bytes)
            except Exception as blank_err:
                # Even the blank fallback failed — at this point the
                # WeasyPrint install is so broken that we should fail
                # fast rather than build a partially-broken PDF.
                log.exception(
                    "WeasyPrint: blank-placeholder fallback also failed: %s",
                    blank_err,
                )
                raise RuntimeError(
                    f"page {cs + 1} exceeded 240s render budget AND the "
                    f"blank-placeholder fallback also failed ({blank_err}). "
                    f"Try lowering DPI (300 instead of 600) or reducing the "
                    f"illustration resolution on page {cs + 1}."
                ) from blank_err
        except Exception as e_render:
            # Unexpected error (not a timeout). Same placeholder strategy
            # so one bad page doesn't kill the whole export.
            log.exception(
                "WeasyPrint: page %d/%d raised %s — substituting blank placeholder",
                cs + 1, e, e_render,
            )
            _emit(f"page {cs + 1} errored — using placeholder")
            render_placeholder_count += 1
            try:
                blank_bytes = await asyncio.wait_for(
                    asyncio.to_thread(_render_blank_page_sync, cs, ce),
                    timeout=30.0,
                )
                chunk_pdfs.append(blank_bytes)
            except Exception:
                # Re-raise the ORIGINAL render error so the operator can
                # see the actual failure cause in the job's error field.
                raise e_render
        finally:
            stop_hb.set()
            try:
                await asyncio.wait_for(hb_task, timeout=2.0)
            except Exception:
                hb_task.cancel()

    # Merge chunks. WeasyPrint produces one PDF per chunk; pypdf
    # concatenates without re-rasterising — fast and lossless.
    _emit("merging chunks")
    if len(chunk_pdfs) == 1:
        pdf_bytes = chunk_pdfs[0]
    else:
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        for blob in chunk_pdfs:
            for pg in PdfReader(io.BytesIO(blob)).pages:
                writer.add_page(pg)
        out_buf = io.BytesIO()
        writer.write(out_buf)
        pdf_bytes = out_buf.getvalue()

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

    # Phase #4 — end-of-job summary. This single line is what turns
    # "PDF export is slow again" into a 30-second diagnosis. It captures:
    #   - pre-fetch and render wall-clock time SEPARATELY (so the next
    #     regression is attributed to fetch vs render, not guessed)
    #   - average ms per image and ms per page for each phase
    #   - count of blank-PNG / placeholder fallbacks (so a "fast" book
    #     that secretly skipped 6 broken pages is loud, not silent)
    # Total wall clock is the sum of both phases (they run sequentially).
    render_elapsed_s = time.monotonic() - render_t0
    render_per_page_ms = (
        (render_elapsed_s * 1000) / page_count if page_count else 0.0
    )
    total_elapsed_s = prefetch_elapsed_s + render_elapsed_s
    log.info(
        "WeasyPrint JOB SUMMARY: total=%.2fs | prefetch=%.2fs (%d imgs, "
        "avg %.0fms/img, %d blank-fallbacks) | render=%.2fs (%d pages, "
        "avg %.0fms/page, %d placeholders) | fonts: %d used, %d missing%s",
        total_elapsed_s,
        prefetch_elapsed_s, prefetch_total,
        (prefetch_elapsed_s * 1000) / prefetch_total if prefetch_total else 0.0,
        prefetch_blank_fallbacks["n"],
        render_elapsed_s, page_count, render_per_page_ms,
        render_placeholder_count,
        len(used_font_families), len(missing_font_families),
        # Spell out missing families when there ARE any — silent fallback
        # to serif is the most common quality regression an operator will
        # miss otherwise. When zero, omit so the line stays scannable.
        f" {sorted(missing_font_families)}" if missing_font_families else "",
    )
    # Phase 3.3 — stamp Info dict (Title/Author/ISBN/Publisher) AFTER
    # all upstream PDF rewrites have settled. Doing this last guarantees
    # no downstream step can clobber the metadata.  Idempotent and
    # failure-tolerant (returns original bytes if pypdf can't rewrite).
    try:
        pdf_bytes = _pb.stamp_pdf_metadata(pdf_bytes, book)
    except Exception as e:
        log.warning("WeasyPrint: stamp_pdf_metadata failed: %s", e)
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

    # Heartbeat (see comment in build_book_pdf above).
    stop_hb = asyncio.Event()

    async def _heartbeat():
        elapsed = 0
        while not stop_hb.is_set():
            try:
                await asyncio.wait_for(stop_hb.wait(), timeout=10.0)
                return
            except asyncio.TimeoutError:
                pass
            elapsed += 10
            try:
                _emit(f"rendering cover with weasyprint ({elapsed}s)")
            except Exception:
                pass

    hb_task = asyncio.create_task(_heartbeat())
    try:
        pdf_bytes = await asyncio.wait_for(
            asyncio.to_thread(_render_sync),
            timeout=WEASY_TIMEOUT_S,
        )
    finally:
        stop_hb.set()
        try:
            await asyncio.wait_for(hb_task, timeout=2.0)
        except Exception:
            hb_task.cancel()
    # Phase 3.3 — same Info-dict stamping as the interior. Catalog
    # ingestion tools index both files; consistent metadata across
    # cover + interior avoids one being flagged as orphaned.
    try:
        pdf_bytes = _pb.stamp_pdf_metadata(pdf_bytes, book)
    except Exception as e:
        log.warning("WeasyPrint: cover stamp_pdf_metadata failed: %s", e)
    _emit("done")
    return pdf_bytes
