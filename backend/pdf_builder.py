"""Server-side PDF generation that prints the book exactly as the editor
displays it on screen.

Strategy: build a self-contained HTML document whose layout, fonts, and
positioning mirror the React editor's PageCanvas (same pixel coordinates,
same CSS, same Google-Fonts family). Load the HTML into headless Chromium
via Playwright, wait for fonts + images, and call `page.pdf()` so the PDF
is a 1:1 vector copy of what the user sees — including all blocks/boxes,
overflow clipping, and page numbers.

This replaces the previous ReportLab-based renderer, which substituted
fonts and could not reproduce the editor's exact text wrapping.
"""

from __future__ import annotations

import asyncio
import base64
import html as html_lib
import logging
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional

from playwright.async_api import async_playwright

log = logging.getLogger("pdf")

# Tracks whether we've already verified/installed Chromium so concurrent
# PDF requests don't trigger duplicate `playwright install` runs.
_chromium_ready = False
_chromium_lock = asyncio.Lock()


async def _try_launch_chromium() -> bool:
    """Lightweight liveness probe — succeeds iff Chromium is launchable."""
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            await browser.close()
        return True
    except Exception as e:
        log.info("Chromium not yet launchable: %s", str(e).splitlines()[0])
        return False


def _autodetect_chromium_path() -> Optional[str]:
    """Look in the common pre-installed locations and return the path with
    the most recent `chromium*-NNNN` directory inside. Lets us repair a
    misconfigured `PLAYWRIGHT_BROWSERS_PATH` (e.g. `/pw-browsers` on a
    deployment image where the binary actually lives in `~/.cache/`)."""
    candidates = [
        "/root/.cache/ms-playwright",
        "/pw-browsers",
        os.path.expanduser("~/.cache/ms-playwright"),
    ]
    for path in candidates:
        try:
            if not os.path.isdir(path):
                continue
            entries = os.listdir(path)
            # Any directory matching chromium* is a sign Playwright populated it.
            if any(e.startswith("chromium") for e in entries):
                return path
        except Exception:
            continue
    return None


async def _run_playwright_install() -> None:
    """Download Chromium via `python -m playwright install chromium`.
    Streams output to logs so deployment debugging is easier."""
    log.info("Installing Chromium for Playwright (one-time, ~200MB)…")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "playwright",
        "install",
        "chromium",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    text = (out or b"").decode(errors="replace")
    if proc.returncode != 0:
        log.error("playwright install failed (rc=%s): %s", proc.returncode, text[-2000:])
        raise RuntimeError(f"playwright install chromium failed: {text[-500:]}")
    log.info("Chromium installed successfully.")


async def ensure_chromium_installed() -> None:
    """Ensure a launchable Chromium exists. Safe to call from concurrent
    requests — guarded by a process-wide lock so we install at most once
    per container."""
    global _chromium_ready
    if _chromium_ready:
        return
    async with _chromium_lock:
        if _chromium_ready:
            return
        if await _try_launch_chromium():
            _chromium_ready = True
            return

        # Production gotcha: deployment images often pre-bake Chromium under
        # ~/.cache/ms-playwright but inherit a stale PLAYWRIGHT_BROWSERS_PATH
        # (e.g. /pw-browsers) where the binary doesn't exist. That makes
        # every cold start re-download Chromium and risk OOM. Detect the
        # real install location and rebind the env BEFORE attempting an
        # expensive install.
        detected = _autodetect_chromium_path()
        configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if detected and detected != configured:
            log.warning(
                "PLAYWRIGHT_BROWSERS_PATH was %r but Chromium lives at %r — repairing env.",
                configured, detected,
            )
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = detected
            if await _try_launch_chromium():
                _chromium_ready = True
                return

        # Last resort: download. Honours whatever PLAYWRIGHT_BROWSERS_PATH
        # is set to now (either repaired above or the original).
        await _run_playwright_install()
        if not await _try_launch_chromium():
            raise RuntimeError(
                "Chromium installed but still not launchable. "
                "Container may be missing system libraries (libnss3, libatk1.0, etc.)."
            )
        _chromium_ready = True


# Must mirror /app/frontend/src/lib/pageSizes.js exactly.
PAGE_SIZES_PX = {
    "a4": (794, 1123),
    "letter": (816, 1056),
    "square": (800, 800),
    "book6x9": (576, 864),
}
PAGE_MARGIN_PX = 38  # 1cm @ 96dpi — matches frontend PAGE_MARGIN_PX (37.8 rounded).

GOOGLE_FONTS_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2?"
    "family=Abril+Fatface"
    "&family=Bebas+Neue"
    "&family=Bitter:ital,wght@0,400;0,600;0,700;1,400"
    "&family=Caveat:wght@400;700"
    "&family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500"
    "&family=Crimson+Text:ital,wght@0,400;0,600;1,400"
    "&family=Dancing+Script:wght@400;700"
    "&family=DM+Sans:wght@400;500;700"
    "&family=EB+Garamond:ital,wght@0,400;0,500;0,600;0,700;1,400"
    "&family=Fira+Code:wght@400;500;700"
    "&family=Inconsolata:wght@400;700"
    "&family=Indie+Flower"
    "&family=JetBrains+Mono:wght@400;500;700"
    "&family=Kalam:wght@400;700"
    "&family=Libre+Baskerville:ital,wght@0,400;0,700;1,400"
    "&family=Lobster"
    "&family=Lora:ital,wght@0,400;0,500;0,600;0,700;1,400"
    "&family=Merriweather:ital,wght@0,400;0,700;0,900;1,400"
    "&family=Montserrat:wght@300;400;500;600;700"
    "&family=Nunito:wght@400;600;700"
    "&family=Outfit:wght@300;400;500;600;700"
    "&family=Pacifico"
    "&family=Playfair+Display:ital,wght@0,400;0,500;0,600;0,700;1,400"
    "&family=Poppins:wght@300;400;500;600;700"
    "&family=Raleway:wght@300;400;500;600;700"
    "&family=Sacramento"
    "&family=Work+Sans:wght@300;400;500;600;700"
    "&display=swap');"
)


def _is_dark_hex(color: str) -> bool:
    """Match isDarkHex() in Editor.jsx (perceived luminance, Rec. 709)."""
    try:
        c = (color or "").lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) != 6:
            return False
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 < 0.55
    except Exception:
        return False


def _css_color(c: Optional[str], default: str = "#000000") -> str:
    if not c or not isinstance(c, str):
        return default
    return c


def _attr(v: Optional[str]) -> str:
    return html_lib.escape(str(v or ""), quote=True)


# Block HTML stored by the editor is already DOMPurify-sanitised on save.
# We re-strip a small allowlist on the server as defence-in-depth so headless
# Chrome never executes user content even if a row predates sanitisation.
_SCRIPT_RE = re.compile(r"<\s*script\b[^>]*>.*?</\s*script\s*>", re.IGNORECASE | re.DOTALL)
_EVENT_ATTR_RE = re.compile(r"\son[a-z]+\s*=\s*(?:'[^']*'|\"[^\"]*\"|[^\s>]+)", re.IGNORECASE)
_JAVASCRIPT_URL_RE = re.compile(r"javascript\s*:", re.IGNORECASE)


def _safe_block_html(raw: Optional[str]) -> str:
    if not raw:
        return ""
    s = _SCRIPT_RE.sub("", raw)
    s = _EVENT_ATTR_RE.sub("", s)
    s = _JAVASCRIPT_URL_RE.sub("blocked:", s)
    return s


def _resolve_image_path(block: dict) -> Optional[str]:
    """The editor stores either `image_path` (storage key) or `image_url`
    pointing at `/api/files/<path>`. Normalise to a storage key."""
    path = (block.get("image_path") or "").strip()
    if path:
        return path
    url = (block.get("image_url") or "").strip()
    if url.startswith("/api/files/"):
        return url[len("/api/files/") :]
    return None


def _data_url(content_type: str, data: bytes) -> str:
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{content_type or 'application/octet-stream'};base64,{b64}"


def _render_block(block: dict, image_data_urls: dict) -> str:
    btype = block.get("type")
    style = (
        f"position:absolute;"
        f"left:{float(block.get('x') or 0)}px;"
        f"top:{float(block.get('y') or 0)}px;"
        f"width:{float(block.get('width') or 0)}px;"
        f"height:{float(block.get('height') or 0)}px;"
        f"z-index:{int(block.get('z_index') or 1)};"
        f"overflow:hidden;"
    )
    if btype == "text":
        inner_style = (
            f"width:100%;height:100%;"
            f"padding:4px 8px;"  # py-1 px-2 (Tailwind = 4px/8px)
            f"box-sizing:border-box;"
            f"font-family:{_attr(block.get('font_family') or 'Cormorant Garamond')}, serif;"
            f"font-size:{float(block.get('font_size') or 18)}px;"
            f"text-align:{_attr(block.get('text_align') or 'left')};"
            f"color:{_css_color(block.get('color'), '#000000')};"
            f"line-height:1.45;"
            f"overflow:hidden;"  # mirrors the editor's overflow:hidden
            f"word-wrap:break-word;"
            f"overflow-wrap:break-word;"
        )
        return (
            f'<div style="{style}">'
            f'<div style="{inner_style}">{_safe_block_html(block.get("html"))}</div>'
            f'</div>'
        )
    if btype == "image":
        path = _resolve_image_path(block)
        # Prefer the public URL (Chromium fetches in parallel, low memory).
        # Only inline the bytes when no public URL is available — the inline
        # path is heavy (HTML grows by ~33% of every image) and was the
        # source of OOM-induced 520s in production for image-heavy books.
        src = ""
        public_url = (block.get("image_url") or "").strip()
        if public_url:
            if public_url.startswith("/"):
                base = os.environ.get("PUBLIC_BACKEND_URL", "").rstrip("/")
                if base:
                    public_url = f"{base}{public_url}"
            src = public_url
        if not src:
            src = image_data_urls.get(path or "", "")
        if not src:
            log.warning(
                "PDF export: image block %s has no resolvable src (path=%r, url=%r)",
                block.get("id"), block.get("image_path"), block.get("image_url"),
            )
            return f'<div style="{style}"></div>'
        img_style = (
            "width:100%;height:100%;"
            "object-fit:contain;"
            "display:block;"
            "user-select:none;-webkit-user-drag:none;"
        )
        return (
            f'<div style="{style}">'
            f'<img src="{_attr(src)}" style="{img_style}" />'
            f'</div>'
        )
    return ""


def _render_page(
    page: dict,
    page_index: int,
    total_pages: int,
    page_number_start: int,
    page_w: int,
    page_h: int,
    image_data_urls: dict,
) -> str:
    bg = page.get("background_color") or "#FFF8DC"
    full_bleed = bool(page.get("full_bleed"))
    margin = 0 if full_bleed else PAGE_MARGIN_PX
    inner_w = page_w - margin * 2
    inner_h = page_h - margin * 2

    pn_align = (page.get("page_number_align") or "right").lower()
    pn_size = float(page.get("page_number_size") or 14)
    pn_font = page.get("page_number_font") or "Cormorant Garamond"
    pn_color = "#E8E2D4" if _is_dark_hex(bg) else "#3A3833"

    is_back_cover = total_pages > 1 and page_index == total_pages - 1
    one_based = page_index + 1
    is_before_start = one_based < page_number_start
    show_pn = bool(page.get("show_page_number")) and not is_back_cover and not is_before_start
    displayed_num = one_based - page_number_start + 1

    page_number_html = ""
    if show_pn:
        pn_style = (
            f"position:absolute;"
            f"bottom:{margin + 16}px;"
            f"font-family:{_attr(pn_font)}, serif;"
            f"font-size:{pn_size}px;"
            f"letter-spacing:0.05em;"
            f"color:{pn_color};"
        )
        if pn_align == "left":
            pn_style += f"left:{margin + 16}px;"
        elif pn_align == "center":
            pn_style += "left:0;right:0;text-align:center;"
        else:  # right
            pn_style += f"right:{margin + 16}px;"
        page_number_html = f'<div style="{pn_style}">{displayed_num}</div>'

    # Sort blocks by z_index so stacking matches the editor.
    blocks = sorted(page.get("blocks") or [], key=lambda b: int(b.get("z_index") or 0))
    blocks_html = "".join(_render_block(b, image_data_urls) for b in blocks)

    page_break = "" if page_index == total_pages - 1 else "page-break-after:always;"

    return (
        f'<div class="book-page" style="'
        f"position:relative;width:{page_w}px;height:{page_h}px;"
        f"background:#FFFFFF;overflow:hidden;{page_break}\">"
        f'<div style="position:absolute;top:{margin}px;left:{margin}px;'
        f'width:{inner_w}px;height:{inner_h}px;background:{_css_color(bg, "#FFF8DC")};"></div>'
        f"{blocks_html}"
        f"{page_number_html}"
        f"</div>"
    )


def _build_html(book: dict, image_data_urls: dict, page_range: Optional[tuple[int, int]] = None) -> tuple[str, int, int]:
    """Render a (slice of a) book to a self-contained HTML document.

    `page_range = (start, end)` renders pages[start:end] but still passes the
    GLOBAL page index to `_render_page`, so page numbering and cover/back-
    cover detection stay correct when the book is rendered in chunks."""
    page_size_key = book.get("page_size") or "a4"
    page_w, page_h = PAGE_SIZES_PX.get(page_size_key, PAGE_SIZES_PX["a4"])
    pages = book.get("pages") or []
    page_number_start = int(book.get("page_number_start") or 1)
    total_pages = len(pages)
    start, end = (0, total_pages) if page_range is None else page_range

    pages_html = "".join(
        _render_page(pages[i], i, total_pages, page_number_start, page_w, page_h, image_data_urls)
        for i in range(start, end)
    )

    css = (
        f"{GOOGLE_FONTS_IMPORT}"
        f"@page {{ size: {page_w}px {page_h}px; margin: 0; }}"
        "html, body { margin: 0; padding: 0; background: #FFFFFF; "
        "-webkit-print-color-adjust: exact; print-color-adjust: exact; }"
        "* { box-sizing: border-box; }"
        # Tailwind-style preflight reset so the editor's HTML (which stores
        # user content as <p>…</p>) doesn't gain Chrome's default 1em <p>
        # margins — those margins are what pushed the cover's "Every Color"
        # line off the bottom of its (overflow:hidden) title box.
        "p, h1, h2, h3, h4, h5, h6, ul, ol, blockquote, pre, figure { margin: 0; padding: 0; }"
        "ul, ol { list-style: none; }"
        ".book-page { box-shadow: none !important; }"
    )

    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{css}</style></head>"
        f"<body>{pages_html}</body></html>"
    )
    return html, page_w, page_h


def _collect_image_paths(book: dict) -> list[str]:
    paths: list[str] = []
    for page in book.get("pages") or []:
        for block in page.get("blocks") or []:
            if block.get("type") != "image":
                continue
            p = _resolve_image_path(block)
            if p and p not in paths:
                paths.append(p)
    return paths


# IngramSpark / general commercial print: 0.125" bleed (~3 mm) on every
# OUTSIDE edge of the cover spread. The two inner edges between back/spine
# and spine/front do not bleed.
COVER_BLEED_IN = 0.125
PX_PER_INCH = 96
COVER_BLEED_PX = round(COVER_BLEED_IN * PX_PER_INCH)  # = 12

# IngramSpark white-paper interior caliper. Spine width (in) =
# interior_page_count * 0.002252. We round generously for kids' books
# (lots of art + heavy paper) but expose it to the caller so they can
# match a specific printer.
DEFAULT_PAPER_CALIPER_IN = 0.002252


def _build_cover_spread_html(
    book: dict,
    image_data_urls: dict,
    spine_width_in: Optional[float] = None,
    paper_caliper_in: float = DEFAULT_PAPER_CALIPER_IN,
) -> tuple[str, int, int]:
    """Build a single-page wide HTML for the cover spread:

        ┌──────────┬─────┬──────────┐
        │   BACK   │SPINE│  FRONT   │   (+ 0.125" bleed all around)
        └──────────┴─────┴──────────┘

    Layout is IngramSpark perfect-bound: when you look at the printed cover
    laid flat from the front, the back cover is on the left, the spine in
    the middle, and the front cover on the right. The "front cover" is
    `pages[0]` (the cover the editor designs); the "back cover" is the
    final page (`pages[-1]`).
    """
    page_size_key = book.get("page_size") or "a4"
    page_w, page_h = PAGE_SIZES_PX.get(page_size_key, PAGE_SIZES_PX["a4"])
    pages = book.get("pages") or []
    total = len(pages)
    if total < 1:
        raise ValueError("Cover spread requires at least one page in the book")

    # Spine width: explicit user override beats the page-count formula.
    # When the book has fewer than 24 interior pages, the spine is too
    # thin for printing — clamp to a 1 mm minimum so we still render.
    if spine_width_in is None:
        # interior pages = total - 2 (subtract front + back covers).
        interior_pages = max(0, total - 2)
        spine_width_in = interior_pages * paper_caliper_in
    spine_px = max(4, round(float(spine_width_in) * PX_PER_INCH))

    # Trim dimensions = back + spine + front (no bleed yet).
    trim_w = page_w * 2 + spine_px
    trim_h = page_h
    # Final canvas = trim + bleed all around.
    total_w = trim_w + COVER_BLEED_PX * 2
    total_h = trim_h + COVER_BLEED_PX * 2

    back_idx = total - 1
    front_idx = 0
    # Reuse the same page renderer the interior uses — same fonts, same
    # blocks, same artwork. We treat the spread as ONE page so we skip the
    # page-break-after class.
    back_html = _render_page(
        pages[back_idx], back_idx, total, 1, page_w, page_h, image_data_urls
    )
    front_html = _render_page(
        pages[front_idx], front_idx, total, 1, page_w, page_h, image_data_urls
    )

    # Spine background — try to harmonise with the front cover so the
    # printed object reads as one design. Falls back to dark editorial
    # background if the cover doesn't declare a colour.
    spine_bg = _css_color(pages[front_idx].get("background_color"), "#1C1B19")

    css = (
        f"{GOOGLE_FONTS_IMPORT}"
        f"@page {{ size: {total_w}px {total_h}px; margin: 0; }}"
        "html, body { margin: 0; padding: 0; background: #FFFFFF; "
        "-webkit-print-color-adjust: exact; print-color-adjust: exact; }"
        "* { box-sizing: border-box; }"
        "p, h1, h2, h3, h4, h5, h6, ul, ol, blockquote, pre, figure { margin: 0; padding: 0; }"
        "ul, ol { list-style: none; }"
        f".spread {{ position: relative; width: {total_w}px; height: {total_h}px; "
        f"background: {spine_bg}; }}"
        # Wrap each half so the existing absolute-positioned blocks inside
        # `book-page` stay anchored to their own page rectangle.
        f".cover-slot {{ position: absolute; width: {page_w}px; height: {page_h}px;"
        f" top: {COVER_BLEED_PX}px; overflow: hidden; }}"
        f".slot-back  {{ left: {COVER_BLEED_PX}px; }}"
        f".slot-front {{ left: {COVER_BLEED_PX + page_w + spine_px}px; }}"
        # Bleed-zone tint (printer side) — pure spine colour so the bled
        # area visually continues the spine instead of producing a white
        # halo on trim.
        f".bleed-band {{ position: absolute; background: {spine_bg}; }}"
    )

    bleed_bands = (
        f'<div class="bleed-band" style="top:0;left:0;width:100%;height:{COVER_BLEED_PX}px"></div>'
        f'<div class="bleed-band" style="bottom:0;left:0;width:100%;height:{COVER_BLEED_PX}px"></div>'
        f'<div class="bleed-band" style="top:0;left:0;width:{COVER_BLEED_PX}px;height:100%"></div>'
        f'<div class="bleed-band" style="top:0;right:0;width:{COVER_BLEED_PX}px;height:100%"></div>'
    )

    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{css}</style></head>"
        f"<body><div class='spread'>"
        f"{bleed_bands}"
        f"<div class='cover-slot slot-back'>{back_html}</div>"
        f"<div class='cover-slot slot-front'>{front_html}</div>"
        f"</div></body></html>"
    )
    return html, total_w, total_h


async def build_cover_spread_pdf(
    book: dict,
    get_image: Callable[[str], tuple[bytes, str]],
    public_base_url: Optional[str] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    spine_width_in: Optional[float] = None,
    paper_caliper_in: float = DEFAULT_PAPER_CALIPER_IN,
) -> bytes:
    """Render the front-cover + spine + back-cover as a single wide PDF
    page with 0.125" bleed on every outside edge. The result is the
    print-ready cover file IngramSpark (and most other perfect-bound POD
    printers) expect: one PDF, one page, BACK | SPINE | FRONT layout."""

    def _emit(stage: str) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(stage)
        except Exception:
            pass

    if public_base_url:
        os.environ["PUBLIC_BACKEND_URL"] = public_base_url

    _emit("preparing chromium")
    await ensure_chromium_installed()

    image_cache: dict[str, tuple[bytes, str]] = {}

    async def _handle_route(route):
        req_url = route.request.url
        marker = "/api/files/"
        if marker in req_url:
            key = req_url.split(marker, 1)[1].split("?", 1)[0]
            cached = image_cache.get(key)
            if cached is None:
                try:
                    raw, ctype = await asyncio.to_thread(get_image, key)
                    if not raw:
                        await route.fulfill(status=404, body=b"")
                        return
                    cached = (raw, ctype or "image/png")
                    image_cache[key] = cached
                except Exception:
                    await route.fulfill(status=502, body=b"")
                    return
            data, ctype = cached
            await route.fulfill(status=200, body=data, content_type=ctype)
            return
        await route.continue_()

    html, total_w, total_h = _build_cover_spread_html(
        book, {}, spine_width_in=spine_width_in, paper_caliper_in=paper_caliper_in
    )
    log.info(
        "Cover spread: %dx%d px (trim %dx%d, bleed %dpx each side, spine %d px)",
        total_w, total_h,
        total_w - 2 * COVER_BLEED_PX, total_h - 2 * COVER_BLEED_PX,
        COVER_BLEED_PX,
        total_w - 2 * COVER_BLEED_PX - 2 * PAGE_SIZES_PX.get(book.get("page_size") or "a4", PAGE_SIZES_PX["a4"])[0],
    )

    async with async_playwright() as pw:
        _emit("launching chromium")
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--disable-background-networking", "--no-zygote",
            ],
        )
        try:
            _emit("rendering cover spread")
            context = await browser.new_context(viewport={"width": total_w, "height": total_h})
            try:
                page = await context.new_page()
                await page.route("**/api/files/**", _handle_route)
                await page.set_content(html, wait_until="domcontentloaded", timeout=30_000)
                try:
                    await page.evaluate(
                        "Promise.race(["
                        "  (document.fonts ? document.fonts.ready : Promise.resolve()),"
                        "  new Promise(r => setTimeout(r, 3000))"
                        "])"
                    )
                except Exception:
                    pass
                try:
                    await page.evaluate(
                        """
                        Promise.race([
                          Promise.all(Array.from(document.images).map(img =>
                            img.complete
                              ? Promise.resolve()
                              : new Promise(r => {
                                  img.addEventListener('load', r, { once: true });
                                  img.addEventListener('error', r, { once: true });
                                })
                          )),
                          new Promise(r => setTimeout(r, 20000))
                        ])
                        """
                    )
                except Exception:
                    pass
                pdf_bytes = await page.pdf(
                    width=f"{total_w}px",
                    height=f"{total_h}px",
                    print_background=True,
                    prefer_css_page_size=True,
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                )
            finally:
                await context.close()
        finally:
            await browser.close()

    _emit("done")
    return pdf_bytes


async def build_book_pdf(
    book: dict,
    get_image: Callable[[str], tuple[bytes, str]],
    public_base_url: Optional[str] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    start_page: Optional[int] = None,
    end_page: Optional[int] = None,
) -> bytes:
    """Render the book to a PDF that exactly mirrors the editor view.

    Parameters
    ----------
    book : dict
        The Book document straight from MongoDB (full, unsliced).
    get_image : callable(path) -> (bytes, content_type)
        Synchronous fetcher returning the raw bytes + Content-Type for an
        asset stored in object storage. We inline these as data: URLs so
        Chromium needs zero outbound requests to load the book artwork.
    public_base_url : str, optional
        Origin (e.g. `https://app.example.com`) used to absolutise
        `/api/files/...` URLs when the internal fetch fails — letting
        Chromium pull the image the same way the editor does. When unset
        the env var `PUBLIC_BACKEND_URL` is consulted.
    progress_cb : callable(stage_str), optional
        Invoked at each major stage so the surrounding job worker can
        surface live progress to the client (e.g. "rendering chunk 3/8").
        Errors inside the callback are swallowed.
    start_page, end_page : int, optional (1-indexed, inclusive)
        Render only this range of pages. Crucially the GLOBAL page index
        is preserved when passing to `_render_page`, so the displayed page
        numbers in a range export match the original book numbering
        (exporting pages 51–112 shows "51, 52, …, 112" — not 1–62) and
        back-cover detection still keys off the original book length.
    """
    def _emit(stage: str) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(stage)
        except Exception:
            pass
    # Make the public base URL available to _render_block via env so we
    # don't have to plumb it through every helper.
    if public_base_url:
        os.environ["PUBLIC_BACKEND_URL"] = public_base_url

    # Strategy: keep the HTML tiny (no base64 images), and serve each image
    # Chromium asks for from this process via `page.route()`. Memory profile:
    #   • upfront pre-fetch of all images at once ⇒ OOM risk on big books
    #   • on-demand fetch when Chromium requests it ⇒ ~1 image in RAM at a time
    # We also downscale very large images (max 2000 px on the long edge,
    # JPEG-recompressed) before handing them off — a 4000×4000 photo decodes
    # to ~64 MB inside Chromium even when rendered at 600 px, which is what
    # was pushing the container over its memory limit.
    image_data_urls: dict[str, str] = {}  # placeholder, no inline base64 anymore
    paths = _collect_image_paths(book)
    log.info("PDF export: book has %d unique image path(s)", len(paths))

    # Small process-local cache so the SAME image isn't re-fetched if it
    # appears on multiple pages. Cap memory at a few images worth.
    image_cache: dict[str, tuple[bytes, str]] = {}
    # 300 DPI cap (standard offset-print resolution). For an 8-inch-wide
    # content block this yields 2400 px; an 11-inch letter full-bleed image
    # caps at the 3300 px long-edge ceiling below. Indistinguishable from
    # 600 DPI to the eye and halves Chromium's per-image decoded memory.
    MAX_DIM = 3300
    MAX_BYTES_BEFORE_RESIZE = 1_500_000  # ~1.5 MB

    def _maybe_downscale(data: bytes, ctype: str) -> tuple[bytes, str]:
        """Return resized bytes (+ adjusted content-type) when the image is
        bigger than the print target needs. Falls back to the original on any
        decoding error so we never lose an image just because of resize."""
        if not data or len(data) < MAX_BYTES_BEFORE_RESIZE:
            return data, ctype
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(data))
            w, h = img.size
            long_edge = max(w, h)
            if long_edge <= MAX_DIM:
                return data, ctype
            scale = MAX_DIM / long_edge
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
            resized = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            # Flatten any alpha → JPEG (smaller, faster decode) unless the
            # original truly needs transparency.
            has_alpha = resized.mode in ("RGBA", "LA") or (
                resized.mode == "P" and "transparency" in resized.info
            )
            if has_alpha:
                resized.save(buf, format="PNG", optimize=True)
                out_type = "image/png"
            else:
                resized.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True)
                out_type = "image/jpeg"
            new_bytes = buf.getvalue()
            log.info(
                "PDF export: downscaled image %dx%d → %dx%d, %d → %d bytes",
                w, h, *new_size, len(data), len(new_bytes),
            )
            return new_bytes, out_type
        except Exception as e:
            log.warning("PDF export: downscale failed (%s); using original", e)
            return data, ctype

    # Safety net: ensure Chromium is available. Startup tries this too, but
    # in production the binary might not be there on first boot.
    _emit("preparing chromium")
    await ensure_chromium_installed()

    # Chunked rendering: large books cannot be rendered in one Chromium
    # session without OOMing the container (each image decodes to tens of
    # MB; 70 images at once is multi-GB). We render N pages at a time
    # using fresh browser CONTEXTS (cheap) but a SINGLE long-lived browser
    # (saves ~3-5s per chunk on cold launch), then merge the resulting
    # PDFs with pypdf into the final document.
    total_pages = len(book.get("pages") or [])
    page_size_key = book.get("page_size") or "a4"
    page_w, page_h = PAGE_SIZES_PX.get(page_size_key, PAGE_SIZES_PX["a4"])
    # 5-page chunks halve peak memory vs the previous 10, at the cost of
    # one extra context teardown per chunk (negligible).
    CHUNK_SIZE = 5
    # Resolve the (optional) 1-indexed inclusive range into a [slice_start,
    # slice_end) half-open interval against the FULL `pages` array. We do
    # NOT touch `book["pages"]` itself — the global indices must survive
    # all the way through to `_render_page` so page numbering and back-
    # cover detection use the original book's positions.
    slice_start = 0 if start_page is None else max(0, start_page - 1)
    slice_end = total_pages if end_page is None else min(total_pages, end_page)
    if slice_start >= slice_end:
        # Empty / invalid range — emit a minimal 1-page placeholder rather
        # than crashing inside Chromium.
        slice_start, slice_end = 0, min(total_pages, 1)
    chunks = [
        (i, min(i + CHUNK_SIZE, slice_end))
        for i in range(slice_start, slice_end, CHUNK_SIZE)
    ] or [(slice_start, slice_start)]
    log.info(
        "PDF export: rendering pages %d-%d (of %d total) in %d chunk(s) of up to %d",
        slice_start + 1, slice_end, total_pages, len(chunks), CHUNK_SIZE,
    )

    # Intercept /api/files/... requests and answer from in-process cache.
    # Falls through for everything else (Google Fonts, etc).
    async def _handle_route(route):
        req_url = route.request.url
        marker = "/api/files/"
        if marker in req_url:
            key = req_url.split(marker, 1)[1].split("?", 1)[0]
            cached = image_cache.get(key)
            if cached is None:
                try:
                    raw, ctype = await asyncio.to_thread(get_image, key)
                    if not raw:
                        log.warning("PDF export: empty bytes for %s", key)
                        await route.fulfill(status=404, body=b"")
                        return
                    data, out_type = _maybe_downscale(raw, ctype or "image/png")
                    cached = (data, out_type)
                    image_cache[key] = cached
                except Exception as e:
                    log.warning("PDF export: fetch failed for %s: %s", key, e)
                    await route.fulfill(status=502, body=b"")
                    return
            data, ctype = cached
            await route.fulfill(status=200, body=data, content_type=ctype)
            return
        await route.continue_()

    async def _render_chunk(browser, idx: int, total: int, start: int, end: int) -> bytes:
        html, _, _ = _build_html(book, image_data_urls, page_range=(start, end))
        context = await browser.new_context(viewport={"width": page_w, "height": page_h})
        try:
            page = await context.new_page()
            await page.route("**/api/files/**", _handle_route)
            # Use `domcontentloaded` not `load`. `load` blocks until every
            # external resource (Google Fonts CSS + WOFF2 files) finishes,
            # and a single slow font request in production was hanging the
            # render for ~60s per chunk → 5+ minute total exports.
            await page.set_content(html, wait_until="domcontentloaded", timeout=30_000)
            # Give fonts a short, BOUNDED window to load. Falls back to the
            # browser's default serif if the network is too slow — visually
            # acceptable and infinitely better than timing out the export.
            try:
                await page.evaluate(
                    "Promise.race(["
                    "  (document.fonts ? document.fonts.ready : Promise.resolve()),"
                    "  new Promise(r => setTimeout(r, 3000))"
                    "])"
                )
            except Exception:
                pass
            # CRITICAL: `domcontentloaded` does not wait for images. If we
            # called `page.pdf()` now, Chromium would render before any
            # <img> finished fetching, producing a PDF with zero artwork.
            # Wait for every image to settle (load OR error — we don't care
            # which, broken images just become blanks), capped so a single
            # stuck request can't hang the chunk.
            try:
                await page.evaluate(
                    """
                    Promise.race([
                      Promise.all(Array.from(document.images).map(img =>
                        img.complete
                          ? Promise.resolve()
                          : new Promise(r => {
                              img.addEventListener('load', r, { once: true });
                              img.addEventListener('error', r, { once: true });
                            })
                      )),
                      new Promise(r => setTimeout(r, 20000))
                    ])
                    """
                )
            except Exception:
                pass

            return await page.pdf(
                width=f"{page_w}px",
                height=f"{page_h}px",
                print_background=True,
                prefer_css_page_size=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
        finally:
            await context.close()

    chunk_pdfs: list[bytes] = []
    async with async_playwright() as pw:
        _emit("launching chromium")
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                # Memory hygiene — production containers are tight.
                "--disable-gpu",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-extensions",
                "--disable-sync",
                "--disable-translate",
                "--metrics-recording-only",
                "--no-first-run",
                "--no-zygote",
            ],
        )
        try:
            for idx, (start, end) in enumerate(chunks):
                _emit(f"rendering chunk {idx + 1}/{len(chunks)}")
                t0 = asyncio.get_event_loop().time()
                chunk_bytes = await _render_chunk(browser, idx, len(chunks), start, end)
                t1 = asyncio.get_event_loop().time()
                log.info("PDF export: chunk %d/%d (pages %d-%d) rendered in %.1fs, %d bytes",
                         idx + 1, len(chunks), start + 1, end, t1 - t0, len(chunk_bytes))
                chunk_pdfs.append(chunk_bytes)
        finally:
            await browser.close()

    # Single chunk? Skip the merge — saves time and avoids pypdf re-encoding.
    if len(chunk_pdfs) == 1:
        _emit("done")
        return chunk_pdfs[0]

    # Merge with pypdf. Use a writer rather than the deprecated PdfMerger.
    _emit("merging chunks")
    import io
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for blob in chunk_pdfs:
        reader = PdfReader(io.BytesIO(blob))
        for page in reader.pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    _emit("done")
    return out.getvalue()
