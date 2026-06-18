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
import io
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
    """Lightweight liveness probe — succeeds iff Chromium is launchable.

    Wrapped in a 30-second timeout so a hung launch (dev/shm exhaustion,
    missing system libs, ptrace blocked, etc.) can never block the
    process. Without this, a single bad pod state would keep
    `_chromium_lock` held forever, freezing every PDF job that lands on
    it at the `preparing chromium` stage.
    """
    async def _probe() -> bool:
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

    try:
        return await asyncio.wait_for(_probe(), timeout=30)
    except asyncio.TimeoutError:
        log.warning("Chromium launch probe timed out after 30s")
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
    Streams output to logs so deployment debugging is easier.

    Wrapped in a 5-minute timeout: a 200 MB download over a stable link
    finishes in under a minute; anything longer means a stalled CDN
    connection and we need to surface the failure rather than hold the
    `_chromium_lock` indefinitely (which would freeze every PDF job
    landing on this pod at the `preparing chromium` stage).
    """
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
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        log.error("playwright install chromium timed out after 5 min")
        raise RuntimeError(
            "Chromium download timed out (>5 min). The Playwright CDN "
            "may be unreachable. Retry the export, or ask Emergent "
            "support to pre-bake Chromium into the deployment image."
        ) from None
    text = (out or b"").decode(errors="replace")
    if proc.returncode != 0:
        log.error("playwright install failed (rc=%s): %s", proc.returncode, text[-2000:])
        raise RuntimeError(f"playwright install chromium failed: {text[-500:]}")
    log.info("Chromium installed successfully.")


async def ensure_chromium_installed() -> None:
    """Ensure a launchable Chromium exists. Safe to call from concurrent
    requests — guarded by a process-wide lock so we install at most once
    per container.

    The whole operation is wall-clock bounded (6 min total). If anything
    along the chain (lock acquisition, launch probe, CDN download) hangs
    we raise — keeping the lock held forever would freeze every PDF job
    that lands on this pod at the `preparing chromium` stage. Callers
    catch the raise and flip the job to `failed` with the diagnostic.
    """
    global _chromium_ready
    if _chromium_ready:
        return
    try:
        await asyncio.wait_for(_ensure_chromium_installed_inner(), timeout=360)
    except asyncio.TimeoutError:
        raise RuntimeError(
            "Chromium preparation timed out (>6 min). The pod may be "
            "stuck installing or launching the browser. Retry the export; "
            "if it persists, contact Emergent support to pre-bake "
            "Chromium into the deployment image."
        ) from None


async def _ensure_chromium_installed_inner() -> None:
    global _chromium_ready
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
    "square": (816, 816),
    "book6x9": (576, 864),
}
PAGE_MARGIN_PX = 48  # 0.5" @ 96dpi — matches frontend PAGE_MARGIN_PX.

# IngramSpark / commercial-print interior bleed. Applied only when
# `pdfx_bleed=True` is passed into build_book_pdf — adds 0.125" on top,
# bottom, and the OUTSIDE edge of every page (the bind side stays flush).
INTERIOR_BLEED_IN = 0.125
INTERIOR_BLEED_PX = round(INTERIOR_BLEED_IN * 96)  # = 12


def _page_bleed_sides(page_index: int) -> dict:
    """Positioning offsets for placing the 612×612 trim rectangle within
    the symmetric 630×630 MediaBox (IngramSpark v5.11.26).

    Convention: index 0 is the cover (recto = right-hand). Even indices
    are right-hand (recto, spine on LEFT); odd indices are left-hand
    (verso, spine on RIGHT). The trim sits flush against the spine edge
    of the MediaBox; the bleed strip extends past the trim on the OUTER
    side. The extra 9-pt strip between the bleed and the MediaBox edge
    on the spine side is binding gutter — filled with the page's
    background colour so the bind cuts through colour, not white.

    The four values are pixels @ 96 DPI added BEYOND the trim rectangle:
    `top`/`bottom` are always equal (9 pt bleed = 12 px). `left`/`right`
    are asymmetric: outer side gets bleed; spine side gets the binding
    gutter (same width but conceptually different — both filled with bg).
    """
    is_right_page = (page_index % 2 == 0)
    return {
        "top": INTERIOR_BLEED_PX,
        "bottom": INTERIOR_BLEED_PX,
        # OUTER side: this is where the bleed art shows (and where the
        # cutter trims). SPINE side: binding gutter, gets bound away.
        "right": INTERIOR_BLEED_PX * 2 if is_right_page else 0,
        "left": INTERIOR_BLEED_PX * 2 if not is_right_page else 0,
    }


_ZERO_BLEED = {"top": 0, "right": 0, "bottom": 0, "left": 0}


def apply_print_boxes(pdf_bytes: bytes, page_w_px: int, page_h_px: int) -> bytes:
    """Stamp IngramSpark-canonical TrimBox + BleedBox on every page of an
    interior-body PDF that was rendered with `pdfx_bleed=True`.

    Pre-condition: every page's MediaBox is the symmetric trim + 2× bleed
    rectangle (= `page_w + 24px` × `page_h + 24px` @ 96 DPI). Each page's
    rendered content sits flush against the SPINE edge of that rectangle
    with the outer bleed extension on the far side.

    Per IngramSpark v5.11.26 (Learning Smart adaptation):
      * MediaBox: 630 × 630 pt (= page+2bleed) — left untouched.
      * BleedBox: full MediaBox.
      * TrimBox:
          - Odd PDF pages  (1, 3, 5… → right-hand, spine LEFT):
            `[0, 9, page_w_pt + 9, page_h_pt + 9]`
          - Even PDF pages (2, 4, 6… → left-hand,  spine RIGHT):
            `[9, 9, page_w_pt + 18, page_h_pt + 9]`
        The `9` is the bleed amount (0.125" × 72 pt/in) and TrimBox
        explicitly extends INTO the bleed on the outer side so the cutter
        receives the cut line at the bleed boundary.

    The function rewrites the boxes in-place (via pypdf) and re-emits the
    PDF. No content is moved. Returns the new PDF bytes."""
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import RectangleObject

    # 1 px @ 96 DPI = 0.75 pt. Convert all geometry once.
    px_to_pt = 0.75
    trim_w_pt = round(page_w_px * px_to_pt, 4)
    trim_h_pt = round(page_h_px * px_to_pt, 4)
    bleed_pt = round(INTERIOR_BLEED_PX * px_to_pt, 4)  # 9.0 pt

    reader = PdfReader(BytesIO(pdf_bytes))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        is_right_page = (i % 2 == 0)  # cover (idx 0) = right-hand
        # MediaBox and BleedBox always = symmetric outer rectangle.
        media = page.mediabox
        media_w = float(media.width)
        media_h = float(media.height)
        # Set TrimBox per parity. The cutter cuts at this rectangle.
        if is_right_page:
            # Right-hand page (recto): spine on LEFT (x=0..bleed is binding
            # gutter, untouched here), bleed extends past trim on RIGHT.
            tx0 = 0.0
            ty0 = bleed_pt
            tx1 = trim_w_pt + bleed_pt
            ty1 = trim_h_pt + bleed_pt
        else:
            # Left-hand page (verso): spine on RIGHT (binding gutter on
            # the far-right of MediaBox), bleed extends LEFT of trim.
            tx0 = bleed_pt
            ty0 = bleed_pt
            tx1 = bleed_pt + trim_w_pt + bleed_pt
            ty1 = trim_h_pt + bleed_pt
        page.trimbox = RectangleObject([tx0, ty0, tx1, ty1])
        page.bleedbox = RectangleObject([0.0, 0.0, media_w, media_h])
        # Cropbox = MediaBox so on-screen viewers show the full bleed
        # rectangle (some PDF viewers crop to TrimBox by default which
        # would hide the bleed strip in preview).
        page.cropbox = RectangleObject([0.0, 0.0, media_w, media_h])
        writer.add_page(page)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def ensure_even_page_count(pdf_bytes: bytes) -> tuple[bytes, bool]:
    """IngramSpark rejects odd page counts. If the PDF has an odd number
    of pages, append a single blank page sized to match the last page's
    MediaBox so the file is submission-ready.

    The appended blank inherits the correct parity TrimBox/BleedBox for
    its new position (it becomes an even / verso page since the previous
    page was odd / recto). Without this, the appended page would be the
    wrong parity and IngramSpark preflight would flag it.

    Returns the (possibly modified) PDF bytes and a bool indicating
    whether a page was appended (so callers can surface a UX warning)."""
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import RectangleObject

    reader = PdfReader(BytesIO(pdf_bytes))
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
    # New blank page is at index `page_count` (0-based) — flip parity from
    # the previous page. Odd new index → verso (spine on RIGHT). Trim
    # follows the IngramSpark v5.11.26 box scheme so the appended page
    # passes preflight identically to its neighbours.
    new_idx = page_count  # 0-based index after append
    is_right_page = (new_idx % 2 == 0)
    # Reuse the same geometry the body renderer used. Bleed amount is
    # encoded in the existing pages' TrimBox — extract it once.
    bleed_pt = float(last.trimbox.bottom)  # = INTERIOR_BLEED_PX * 0.75 = 9.0
    # Trim dimensions: width = media_w - 2*bleed_pt; height same.
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
    out = BytesIO()
    writer.write(out)
    return out.getvalue(), True


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
        # Optional solid background fill — rendered as the wrapper div's
        # background colour so it shows through transparent areas of the
        # image. When the block has no image yet it functions as a pure
        # colour-tile block (handy for accent panels behind text).
        bg = block.get("background_color")
        wrapper_style = style
        if bg:
            wrapper_style = style + f"background-color:{_css_color(bg, 'transparent')};"
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
            # No image — but if there's a background colour, the block
            # still has visual meaning (colour-tile mode). Render the
            # empty wrapper with the background applied.
            return f'<div style="{wrapper_style}"></div>'
        img_style = (
            "width:100%;height:100%;"
            "object-fit:contain;"
            "display:block;"
            "user-select:none;-webkit-user-drag:none;"
        )
        return (
            f'<div style="{wrapper_style}">'
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
    bleed: Optional[dict] = None,
) -> str:
    """Render a single page to HTML. When `bleed` is non-zero on any side,
    the outer wrapper grows by `(left+right, top+bottom)` and the trim
    content is offset into the bleed-padded area. The page background
    fills the entire outer (including the bleed strip) so the printer's
    trim cuts through colour, not through a white edge."""
    bleed = bleed or _ZERO_BLEED
    b_top, b_right, b_bottom, b_left = bleed["top"], bleed["right"], bleed["bottom"], bleed["left"]
    outer_w = page_w + b_left + b_right
    outer_h = page_h + b_top + b_bottom

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

    # Background fill — fills the FULL outer rect so the bleed area shows
    # the page colour, not white. For full-bleed pages this is the only
    # bg layer; for non-full-bleed pages we additionally draw a margin-
    # bounded fill (kept for parity with the editor's appearance).
    bg_css = _css_color(bg, "#FFF8DC")
    bg_layer_outer = (
        f'<div style="position:absolute;top:0;left:0;'
        f'width:{outer_w}px;height:{outer_h}px;background:{bg_css};"></div>'
    )
    if full_bleed:
        bg_layers = bg_layer_outer
    else:
        # Margin-bounded fill matches the editor's inner page rect. Drawn
        # ON TOP of the outer fill so the page reads identically to the
        # editor — the outer fill only shows through in the bleed strip.
        bg_layers = (
            bg_layer_outer
            + f'<div style="position:absolute;top:{b_top + margin}px;left:{b_left + margin}px;'
            f'width:{inner_w}px;height:{inner_h}px;background:{bg_css};"></div>'
        )

    # The trim-area wrapper holds blocks + page number at their original
    # (0,0)→(page_w, page_h) coordinates. We just translate it into the
    # bleed-padded region. This way the existing block/page-number
    # rendering needs zero further changes.
    trim_wrapper_open = (
        f'<div style="position:absolute;top:{b_top}px;left:{b_left}px;'
        f'width:{page_w}px;height:{page_h}px;">'
    )

    return (
        f'<div class="book-page" style="'
        f"position:relative;width:{outer_w}px;height:{outer_h}px;"
        f"background:#FFFFFF;overflow:hidden;{page_break}\">"
        f"{bg_layers}"
        f"{trim_wrapper_open}"
        f"{blocks_html}"
        f"{page_number_html}"
        f"</div>"  # trim wrapper
        f"</div>"  # book-page
    )


def _build_html(
    book: dict,
    image_data_urls: dict,
    page_range: Optional[tuple[int, int]] = None,
    pdfx_bleed: bool = True,
) -> tuple[str, int, int]:
    """Render a (slice of a) book to a self-contained HTML document.

    `page_range = (start, end)` renders pages[start:end] but still passes the
    GLOBAL page index to `_render_page`, so page numbering and cover/back-
    cover detection stay correct when the book is rendered in chunks.

    `pdfx_bleed=True` adds 0.125" bleed on top/bottom/outside of every page
    — required by IngramSpark and most other commercial printers."""
    page_size_key = book.get("page_size") or "a4"
    page_w, page_h = PAGE_SIZES_PX.get(page_size_key, PAGE_SIZES_PX["a4"])
    pages = book.get("pages") or []
    page_number_start = int(book.get("page_number_start") or 1)
    total_pages = len(pages)
    start, end = (0, total_pages) if page_range is None else page_range

    # IngramSpark v5.11.26 — symmetric MediaBox for every page. Each page's
    # outer canvas is `trim + 0.125" bleed on top/bottom + 0.125" bleed on
    # the outer side + 0.125" binding gutter on the spine side`. Total
    # added on each axis is therefore 2 × INTERIOR_BLEED_PX, regardless of
    # parity, giving identically-sized MediaBoxes throughout the book.
    # The asymmetry (which side is bleed vs which is binding-gutter) is
    # encoded in the TrimBox stamped by `apply_print_boxes` after render.
    if pdfx_bleed:
        outer_w = page_w + INTERIOR_BLEED_PX * 2
        outer_h = page_h + INTERIOR_BLEED_PX * 2
    else:
        outer_w, outer_h = page_w, page_h

    pages_html = "".join(
        _render_page(
            pages[i], i, total_pages, page_number_start,
            page_w, page_h, image_data_urls,
            bleed=_page_bleed_sides(i) if pdfx_bleed else None,
        )
        for i in range(start, end)
    )

    css = (
        f"{GOOGLE_FONTS_IMPORT}"
        f"@page {{ size: {outer_w}px {outer_h}px; margin: 0; }}"
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
    return html, outer_w, outer_h


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

# IngramSpark casebound hardcover wrap / turn-in: 0.625" of extra material
# on every OUTSIDE edge that folds over the board edges and adheres inside
# the case. Replaces the perfect-bound 0.125" bleed (the wrap *is* the
# bleed for casebound — anything inside the wrap can be trimmed off when
# the book block is glued in).
CASEBOUND_WRAP_IN = 0.625
CASEBOUND_WRAP_PX = round(CASEBOUND_WRAP_IN * PX_PER_INCH)  # = 60

# IngramSpark casebound spine allowance: extra material added to the spine
# beyond the raw `page_count × paper_caliper` calculation, to account for
# the board hinge & spine board thickness. Empirically derived from
# IngramSpark's casebound cover-spec generator.
CASEBOUND_SPINE_ALLOWANCE_IN = 0.125

# IngramSpark white-paper interior caliper. Spine width (in) =
# interior_page_count * 0.002252. We round generously for kids' books
# (lots of art + heavy paper) but expose it to the caller so they can
# match a specific printer.
DEFAULT_PAPER_CALIPER_IN = 0.002252

# Allowed image-downscale DPI ceilings for PDF export. Each value picks
# the long-edge pixel cap when `_maybe_downscale` resizes uploaded images
# before handing them to Chromium. 300 is IngramSpark's minimum print
# spec (also default — keeps a 60-page book under the production pod
# memory limit). 450 and 600 escalate quality for print masters at the
# cost of render time + PDF size.
#   • 300 DPI on an 11" letter long-edge ⇒ 3300 px cap
#   • 450 DPI ⇒ 4950 px
#   • 600 DPI ⇒ 6600 px
# Mapping is computed from `dpi × 11` so 600 DPI on an A4 page still
# resolves to ≥ 600 PPI when rendered.
DPI_LONG_EDGE_CAPS: dict[int, int] = {300: 3300, 450: 4950, 600: 6600}
DEFAULT_EXPORT_DPI = 300


def _resolve_dpi(dpi: Optional[int]) -> int:
    """Normalise the requested DPI to one of the allowed values. Anything
    else (None, garbage, unsupported numbers) snaps to the safe default."""
    if dpi is None:
        return DEFAULT_EXPORT_DPI
    try:
        d = int(dpi)
    except (TypeError, ValueError):
        return DEFAULT_EXPORT_DPI
    return d if d in DPI_LONG_EDGE_CAPS else DEFAULT_EXPORT_DPI


def _build_cover_spread_html(
    book: dict,
    image_data_urls: dict,
    spine_width_in: Optional[float] = None,
    paper_caliper_in: float = DEFAULT_PAPER_CALIPER_IN,
    binding: str = "perfect",
) -> tuple[str, int, int]:
    """Build a single-page wide HTML for the cover spread:

        ┌──────────┬─────┬──────────┐
        │   BACK   │SPINE│  FRONT   │   (+ outer-edge allowance all around)
        └──────────┴─────┴──────────┘

    Two binding modes:
      * `perfect` (default) — IngramSpark perfect-bound. 0.125" bleed on
        every outside edge; spine width = interior_pages × paper_caliper.
      * `casebound` — IngramSpark casebound hardcover. 0.625" of wrap
        (turn-in) material on every outside edge (folds over the boards
        and adheres inside the case), and the spine widens by
        `CASEBOUND_SPINE_ALLOWANCE_IN` (0.125") to account for the spine
        board thickness + hinge gap. The wrap replaces the bleed — there
        is no separate 0.125" bleed for casebound files.

    Layout convention: when you look at the printed cover laid flat from
    the front, the back cover is on the left, the spine in the middle,
    and the front cover on the right. The "front cover" is `pages[0]`
    (the cover the editor designs); the "back cover" is the final page
    (`pages[-1]`).
    """
    page_size_key = book.get("page_size") or "a4"
    page_w, page_h = PAGE_SIZES_PX.get(page_size_key, PAGE_SIZES_PX["a4"])
    pages = book.get("pages") or []
    total = len(pages)
    if total < 1:
        raise ValueError("Cover spread requires at least one page in the book")

    binding = (binding or "perfect").lower()
    if binding not in ("perfect", "casebound"):
        binding = "perfect"
    is_casebound = binding == "casebound"

    # Outer allowance: how many extra pixels are added on each outer edge
    # of the trim rectangle. Bleed for perfect-bound, wrap for casebound.
    outer_allowance_px = CASEBOUND_WRAP_PX if is_casebound else COVER_BLEED_PX

    # Spine width: explicit user override beats the page-count formula.
    # When the book has fewer than 24 interior pages, the spine is too
    # thin for printing — clamp to a 1 mm minimum so we still render.
    if spine_width_in is None:
        # interior pages = total - 2 (subtract front + back covers).
        interior_pages = max(0, total - 2)
        spine_width_in = interior_pages * paper_caliper_in
        if is_casebound:
            # Casebound spines need extra room for the spine board +
            # hinge gap — add the standard IngramSpark allowance.
            spine_width_in += CASEBOUND_SPINE_ALLOWANCE_IN
    spine_px = max(4, round(float(spine_width_in) * PX_PER_INCH))

    # Trim dimensions = back + spine + front (no outer allowance yet).
    trim_w = page_w * 2 + spine_px
    trim_h = page_h
    # Final canvas = trim + outer allowance all around.
    total_w = trim_w + outer_allowance_px * 2
    total_h = trim_h + outer_allowance_px * 2

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
        f" top: {outer_allowance_px}px; overflow: hidden; }}"
        f".slot-back  {{ left: {outer_allowance_px}px; }}"
        f".slot-front {{ left: {outer_allowance_px + page_w + spine_px}px; }}"
        # Outer-band tint — pure spine colour so the bled/wrapped area
        # visually continues the spine instead of producing a white halo
        # on trim (perfect-bound) or a visible white strip wrapping
        # inside the case (casebound).
        f".bleed-band {{ position: absolute; background: {spine_bg}; }}"
    )

    outer_bands = (
        f'<div class="bleed-band" style="top:0;left:0;width:100%;height:{outer_allowance_px}px"></div>'
        f'<div class="bleed-band" style="bottom:0;left:0;width:100%;height:{outer_allowance_px}px"></div>'
        f'<div class="bleed-band" style="top:0;left:0;width:{outer_allowance_px}px;height:100%"></div>'
        f'<div class="bleed-band" style="top:0;right:0;width:{outer_allowance_px}px;height:100%"></div>'
    )

    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{css}</style></head>"
        f"<body><div class='spread'>"
        f"{outer_bands}"
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
    binding: str = "perfect",
    dpi: int = DEFAULT_EXPORT_DPI,
) -> bytes:
    """Render the front-cover + spine + back-cover as a single wide PDF
    page. Two binding modes (see `_build_cover_spread_html`):

      * `perfect`   — 0.125" bleed all-around, no board allowance.
                      Output is what IngramSpark / KDP perfect-bound POD
                      printers expect.
      * `casebound` — 0.625" wrap (turn-in) all-around + 0.125" board
                      allowance on the spine. Output is what IngramSpark
                      casebound hardcover printers expect (the printer
                      glues the cover to greyboard panels and folds the
                      wrap inside)."""

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
        book, {}, spine_width_in=spine_width_in, paper_caliper_in=paper_caliper_in,
        binding=binding,
    )
    outer_allowance = CASEBOUND_WRAP_PX if (binding or "perfect").lower() == "casebound" else COVER_BLEED_PX
    log.info(
        "Cover spread (%s): %dx%d px (trim %dx%d, outer %dpx each side, spine %d px)",
        binding,
        total_w, total_h,
        total_w - 2 * outer_allowance, total_h - 2 * outer_allowance,
        outer_allowance,
        total_w - 2 * outer_allowance - 2 * PAGE_SIZES_PX.get(book.get("page_size") or "a4", PAGE_SIZES_PX["a4"])[0],
    )

    async with async_playwright() as pw:
        _emit("launching chromium")
        browser = await asyncio.wait_for(
            pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                    "--disable-background-networking", "--no-zygote",
                ],
            ),
            timeout=30.0,
        )
        try:
            _emit("rendering cover spread")
            context = await browser.new_context(viewport={"width": total_w, "height": total_h})
            try:
                page = await context.new_page()
                page.set_default_timeout(45_000)
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
                          new Promise(r => setTimeout(r, 15000))
                        ])
                        """
                    )
                except Exception:
                    pass
                pdf_bytes = await asyncio.wait_for(
                    page.pdf(
                        width=f"{total_w}px",
                        height=f"{total_h}px",
                        print_background=True,
                        prefer_css_page_size=True,
                        margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                    ),
                    timeout=60.0,
                )
            finally:
                try:
                    await asyncio.wait_for(context.close(), timeout=5.0)
                except Exception:
                    pass
        finally:
            try:
                await asyncio.wait_for(browser.close(), timeout=5.0)
            except Exception:
                pass

    _emit("done")
    return pdf_bytes


async def build_book_pdf(
    book: dict,
    get_image: Callable[[str], tuple[bytes, str]],
    public_base_url: Optional[str] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    start_page: Optional[int] = None,
    end_page: Optional[int] = None,
    # Defaulted ON so every export embeds the 0.125" print bleed required
    # for IngramSpark / commercial perfect-bound printing. Interior pages
    # come out at trim+bleed (e.g. an 8.5×8.5 Square book exports as
    # 8.625×8.75) so the printer can crop to the trim without leaving a
    # white sliver at the edges. PDF/X-1a exports still flip this on
    # because the PDF/X spec mandates a TrimBox/BleedBox annotation
    # alongside the bleed pixels.
    pdfx_bleed: bool = True,
    # Image-downscale ceiling, expressed in DPI relative to the page's
    # trim+bleed long edge. 300 is IngramSpark's minimum print spec and
    # keeps the per-image decoded memory low enough to survive the
    # production pod memory limit. 450/600 raise the ceiling for
    # designers who want every last detail in their print master at the
    # cost of render time and PDF size. Clamped to the allowed set.
    dpi: int = 300,
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
    # Image downscale ceiling — selected by the requested `dpi` parameter.
    # 300 DPI → 3300 px (default, prod-safe); 450 → 4950; 600 → 6600.
    # Indistinguishable from the source at the chosen DPI to the eye, but
    # halves Chromium's per-image decoded memory vs serving full-res
    # 8000+ px source files.
    resolved_dpi = _resolve_dpi(dpi)
    MAX_DIM = DPI_LONG_EDGE_CAPS[resolved_dpi]
    # JPEG re-encode quality. Higher DPI exports usually run alongside
    # PDF/X for a print master — bump the JPEG quality so the extra
    # pixels actually retain detail instead of being eaten by chroma
    # compression artifacts.
    JPEG_QUALITY = 92 if resolved_dpi >= 450 else 85
    log.info(
        "PDF export: image quality = %d DPI (long-edge cap %d px, JPEG q=%d)",
        resolved_dpi, MAX_DIM, JPEG_QUALITY,
    )
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
                resized.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
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
                    # Cap each upstream image fetch at 20s. The synchronous
                    # `requests.get` inside `get_image` already has a 60s
                    # socket timeout, but a slow-trickling response (TCP
                    # alive but bytes-per-second crawl) can still drag past
                    # that. `asyncio.wait_for` enforces a HARD wall-clock
                    # bound — if the fetch isn't done in 20s, we fulfill
                    # with 408 and Chromium renders the page without that
                    # image (broken-image icon, infinitely better than a
                    # silent freeze that strands the whole chunk).
                    raw, ctype = await asyncio.wait_for(
                        asyncio.to_thread(get_image, key),
                        timeout=20.0,
                    )
                    if not raw:
                        log.warning("PDF export: empty bytes for %s", key)
                        await route.fulfill(status=404, body=b"")
                        return
                    data, out_type = _maybe_downscale(raw, ctype or "image/png")
                    cached = (data, out_type)
                    image_cache[key] = cached
                except asyncio.TimeoutError:
                    log.warning("PDF export: image fetch timed out (20s) for %s", key)
                    try:
                        await route.fulfill(status=408, body=b"")
                    except Exception:
                        pass
                    return
                except Exception as e:
                    log.warning("PDF export: fetch failed for %s: %s", key, e)
                    try:
                        await route.fulfill(status=502, body=b"")
                    except Exception:
                        pass
                    return
            data, ctype = cached
            try:
                await route.fulfill(status=200, body=data, content_type=ctype)
            except Exception as e:
                # The page may already be closing (chunk timeout fired) —
                # don't crash the rest of the job over a stale route.
                log.debug("PDF export: route.fulfill ignored: %s", e)
            return
        try:
            await route.continue_()
        except Exception:
            pass

    async def _render_chunk(browser, idx: int, total: int, start: int, end: int,
                            heartbeat_label: str) -> bytes:
        html, outer_w, outer_h = _build_html(
            book, image_data_urls, page_range=(start, end), pdfx_bleed=pdfx_bleed,
        )
        # Heartbeat task — pushes a fresh `stage_at` to Mongo every 15s
        # while the chunk renders. Without it, a chunk that takes 60s+
        # would let the 10-minute stuck-job sweeper falsely mark the
        # job as failed (the sweeper looks at stage_at, not internal
        # progress). Heartbeat survives the per-attempt cancel via
        # `finally`.
        stop_hb = asyncio.Event()

        async def _heartbeat():
            while not stop_hb.is_set():
                try:
                    await asyncio.wait_for(stop_hb.wait(), timeout=15.0)
                    return  # event fired — exit cleanly
                except asyncio.TimeoutError:
                    pass
                try:
                    _emit(heartbeat_label)
                except Exception:
                    pass

        hb_task = asyncio.create_task(_heartbeat())
        context = await browser.new_context(viewport={"width": outer_w, "height": outer_h})
        # Track per-stage timings for diagnostics. Surfaced on the error
        # message if anything fails so we can tell whether the slow
        # stage was set_content (HTML parse), font load, image load, or
        # the actual page.pdf rasterisation.
        timings: dict[str, float] = {}
        last_stage = "init"
        try:
            page = await context.new_page()
            page.set_default_timeout(45_000)
            await page.route("**/api/files/**", _handle_route)

            last_stage = "set_content"
            t = asyncio.get_event_loop().time()
            await page.set_content(html, wait_until="domcontentloaded", timeout=30_000)
            timings[last_stage] = asyncio.get_event_loop().time() - t

            last_stage = "fonts"
            t = asyncio.get_event_loop().time()
            try:
                await page.evaluate(
                    "Promise.race(["
                    "  (document.fonts ? document.fonts.ready : Promise.resolve()),"
                    "  new Promise(r => setTimeout(r, 3000))"
                    "])"
                )
            except Exception:
                pass
            timings[last_stage] = asyncio.get_event_loop().time() - t

            last_stage = "images"
            t = asyncio.get_event_loop().time()
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
                      new Promise(r => setTimeout(r, 15000))
                    ])
                    """
                )
            except Exception:
                pass
            timings[last_stage] = asyncio.get_event_loop().time() - t

            last_stage = "page.pdf"
            t = asyncio.get_event_loop().time()
            result = await asyncio.wait_for(
                page.pdf(
                    width=f"{outer_w}px",
                    height=f"{outer_h}px",
                    print_background=True,
                    prefer_css_page_size=True,
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                ),
                timeout=60.0,
            )
            timings[last_stage] = asyncio.get_event_loop().time() - t
            log.info(
                "PDF chunk %d/%d timings (s): %s",
                idx + 1, total,
                " ".join(f"{k}={v:.1f}" for k, v in timings.items()),
            )
            return result
        except Exception as e:
            # Annotate the exception so the outer retry / failure path
            # can record exactly WHICH stage stalled. Massively reduces
            # debug time on production hangs.
            raise RuntimeError(
                f"chunk {idx + 1}/{total} failed at stage '{last_stage}' "
                f"(timings: {timings}): {type(e).__name__}: {e}"
            ) from e
        finally:
            stop_hb.set()
            try:
                await asyncio.wait_for(hb_task, timeout=2.0)
            except Exception:
                hb_task.cancel()
            try:
                await asyncio.wait_for(context.close(), timeout=5.0)
            except (asyncio.TimeoutError, Exception) as e:
                log.warning("PDF export: context.close() did not return cleanly: %s", e)

    async def _launch_browser(pw):
        # Capped Chromium launch — sometimes the binary itself hangs
        # during sandbox setup on tight pods. 30s gives plenty of room
        # for the worst legitimate cold start while still surfacing a
        # hang as a real error.
        return await asyncio.wait_for(
            pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
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
            ),
            timeout=30.0,
        )

    async def _render_chunk_safe(pw, idx: int, total: int, start: int, end: int,
                                  heartbeat_label: str) -> bytes:
        """Render a chunk with up to 2 attempts. Each attempt gets a
        **fresh Chromium browser** (not just a fresh context) — when
        Chromium hangs in production it's almost always the browser
        process itself that wedges, not the page/context. Spending
        ~3s relaunching is a much better trade than retrying on a
        zombie browser.

        Per-attempt timeout: 80s (covers cold-launch + slow set_content
        + max font/image waits + 60s page.pdf). Total worst case per
        chunk: ~160s. With 3 chunks that's under 9 minutes — comfortably
        below the 10-minute sweeper threshold."""
        per_attempt_timeout = 80.0
        last_err: Optional[Exception] = None
        for attempt in (1, 2):
            browser = None
            try:
                browser = await _launch_browser(pw)
            except Exception as e:
                last_err = e
                log.warning("PDF export: browser launch attempt %d failed: %s", attempt, e)
                continue
            try:
                return await asyncio.wait_for(
                    _render_chunk(browser, idx, total, start, end, heartbeat_label),
                    timeout=per_attempt_timeout,
                )
            except (asyncio.TimeoutError, Exception) as e:
                last_err = e
                log.warning(
                    "PDF export: chunk %d/%d attempt %d failed: %s",
                    idx + 1, total, attempt, e,
                )
            finally:
                if browser is not None:
                    try:
                        await asyncio.wait_for(browser.close(), timeout=5.0)
                    except Exception:
                        pass
        raise RuntimeError(
            f"chunk {idx + 1}/{total} (pages {start + 1}-{end}) failed after 2 attempts. "
            f"Last error: {last_err}"
        )

    chunk_pdfs: list[bytes] = []
    async with async_playwright() as pw:
        _emit("launching chromium")
        # Browser is now (re)launched INSIDE _render_chunk_safe per
        # attempt, not once-for-all-chunks. The old shared-browser
        # model meant a wedged browser process from chunk 1 could
        # never recover; the fresh-per-attempt model is bulletproof
        # against Chromium hangs at the cost of ~3s relaunch overhead
        # per chunk attempt.
        for idx, (start, end) in enumerate(chunks):
            heartbeat_label = f"rendering chunk {idx + 1}/{len(chunks)}"
            _emit(heartbeat_label)
            t0 = asyncio.get_event_loop().time()
            chunk_bytes = await _render_chunk_safe(
                pw, idx, len(chunks), start, end, heartbeat_label
            )
            t1 = asyncio.get_event_loop().time()
            log.info(
                "PDF export: chunk %d/%d (pages %d-%d) rendered in %.1fs, %d bytes",
                idx + 1, len(chunks), start + 1, end, t1 - t0, len(chunk_bytes),
            )
            chunk_pdfs.append(chunk_bytes)

    # Single chunk? Skip pypdf re-encoding unless we still need to stamp
    # print boxes / enforce even page count.
    if len(chunk_pdfs) == 1 and not pdfx_bleed:
        _emit("done")
        return chunk_pdfs[0]

    # Merge with pypdf. Use a writer rather than the deprecated PdfMerger.
    _emit("merging chunks")
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for blob in chunk_pdfs:
        reader = PdfReader(io.BytesIO(blob))
        for page in reader.pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    merged = out.getvalue()

    if pdfx_bleed:
        # Stamp IngramSpark-canonical TrimBox + BleedBox on every page.
        # Look up the trim dimensions from the book's page_size.
        size_key = book.get("page_size", "a4")
        if size_key not in PAGE_SIZES_PX:
            size_key = "a4"
        page_w_trim, page_h_trim = PAGE_SIZES_PX[size_key]
        merged = apply_print_boxes(merged, page_w_trim, page_h_trim)
        # Auto-pad to an even page count ONLY for full-book exports.
        # Partial-range exports (proofing a single chapter, exporting one
        # cover, etc.) keep their exact page count — the user can pad
        # explicitly if they're submitting that partial to IngramSpark.
        is_full_book = (start_page is None and end_page is None)
        if is_full_book:
            merged, appended_blank = ensure_even_page_count(merged)
            if appended_blank and progress_cb is not None:
                try:
                    progress_cb("padded to even page count")
                except Exception:
                    pass

    _emit("done")
    return merged
