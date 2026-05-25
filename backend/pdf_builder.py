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
        # Pre-flight install. Use whatever path Playwright defaults to
        # (respects PLAYWRIGHT_BROWSERS_PATH if set, else ~/.cache/ms-playwright).
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


def _build_html(book: dict, image_data_urls: dict) -> tuple[str, int, int]:
    page_size_key = book.get("page_size") or "a4"
    page_w, page_h = PAGE_SIZES_PX.get(page_size_key, PAGE_SIZES_PX["a4"])
    pages = book.get("pages") or []
    page_number_start = int(book.get("page_number_start") or 1)
    total_pages = len(pages)

    pages_html = "".join(
        _render_page(p, i, total_pages, page_number_start, page_w, page_h, image_data_urls)
        for i, p in enumerate(pages)
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


async def build_book_pdf(
    book: dict,
    get_image: Callable[[str], tuple[bytes, str]],
    public_base_url: Optional[str] = None,
) -> bytes:
    """Render the book to a PDF that exactly mirrors the editor view.

    Parameters
    ----------
    book : dict
        The Book document straight from MongoDB.
    get_image : callable(path) -> (bytes, content_type)
        Synchronous fetcher returning the raw bytes + Content-Type for an
        asset stored in object storage. We inline these as data: URLs so
        Chromium needs zero outbound requests to load the book artwork.
    public_base_url : str, optional
        Origin (e.g. `https://app.example.com`) used to absolutise
        `/api/files/...` URLs when the internal fetch fails — letting
        Chromium pull the image the same way the editor does. When unset
        the env var `PUBLIC_BACKEND_URL` is consulted.
    """
    # Make the public base URL available to _render_block via env so we
    # don't have to plumb it through every helper.
    if public_base_url:
        os.environ["PUBLIC_BACKEND_URL"] = public_base_url

    # Strategy: keep the HTML tiny (no base64 images), but intercept every
    # `/api/files/...` request Chromium makes and serve the bytes straight
    # from this process. This avoids both:
    #   - the OOM from inlining everything as base64 (the cause of 520s)
    #   - the unreliability of having Chromium re-enter the public ingress
    #     to fetch its own backend's files (some k8s setups block loopback).
    image_data_urls: dict[str, str] = {}  # only used as a last-resort fallback
    paths = _collect_image_paths(book)
    log.info("PDF export: book has %d unique image path(s)", len(paths))

    # Pre-fetch every image so the route handler can serve them instantly
    # without round-tripping to object storage during render.
    # Run fetches in parallel via a thread pool — `get_image` uses sync
    # `requests`, so asyncio.to_thread keeps the event loop free. With 35+
    # images this turns a 9-second sequential pre-fetch into ~1.5s.
    fetched_images: dict[str, tuple[bytes, str]] = {}

    async def _fetch_one(p: str) -> tuple[str, Optional[tuple[bytes, str]]]:
        try:
            data, ctype = await asyncio.to_thread(get_image, p)
            if data:
                return p, (data, ctype or "image/png")
            log.warning("PDF export: empty bytes for image %s", p)
        except Exception as e:
            log.warning("PDF export: failed to fetch %s: %s", p, e)
        return p, None

    if paths:
        results = await asyncio.gather(*(_fetch_one(p) for p in paths))
        for p, payload in results:
            if payload is not None:
                fetched_images[p] = payload
    log.info("PDF export: pre-fetched %d/%d images for route interception",
             len(fetched_images), len(paths))

    html, page_w, page_h = _build_html(book, image_data_urls)

    # Safety net: ensure Chromium is available. Startup tries this too, but
    # in production the binary might not be there on first boot.
    await ensure_chromium_installed()

    async with async_playwright() as pw:
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
            context = await browser.new_context(viewport={"width": page_w, "height": page_h})
            page = await context.new_page()

            # Intercept any /api/files/... request and answer it from our
            # in-memory cache. Anything we don't have falls through to the
            # network (so external assets like Google Fonts still work).
            async def _handle_route(route):
                req_url = route.request.url
                marker = "/api/files/"
                if marker in req_url:
                    key = req_url.split(marker, 1)[1].split("?", 1)[0]
                    cached = fetched_images.get(key)
                    if cached is not None:
                        data, ctype = cached
                        await route.fulfill(status=200, body=data, content_type=ctype)
                        return
                    log.warning("PDF export: route miss for %s (not pre-fetched)", key)
                await route.continue_()

            await page.route("**/api/files/**", _handle_route)

            # `load` is enough — it fires after all images + stylesheets are
            # fetched. `networkidle` adds a 500ms quiescence window that
            # offers little for static content and risks proxy timeout (520)
            # on cold containers.
            await page.set_content(html, wait_until="load", timeout=45_000)
            # Belt-and-braces: explicitly wait for the FontFace API to settle
            # so glyphs aren't measured with the fallback metrics.
            try:
                await page.evaluate("document.fonts && document.fonts.ready")
            except Exception:
                pass

            pdf_bytes = await page.pdf(
                width=f"{page_w}px",
                height=f"{page_h}px",
                print_background=True,
                prefer_css_page_size=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
            return pdf_bytes
        finally:
            await browser.close()
