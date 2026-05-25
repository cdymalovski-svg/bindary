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

import base64
import html as html_lib
import logging
import re
from typing import Callable, Optional

from playwright.async_api import async_playwright

log = logging.getLogger("pdf")


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
        src = image_data_urls.get(path or "", "")
        if not src:
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


async def build_book_pdf(book: dict, get_image: Callable[[str], tuple[bytes, str]]) -> bytes:
    """Render the book to a PDF that exactly mirrors the editor view.

    Parameters
    ----------
    book : dict
        The Book document straight from MongoDB.
    get_image : callable(path) -> (bytes, content_type)
        Synchronous fetcher returning the raw bytes + Content-Type for an
        asset stored in object storage. We inline these as data: URLs so
        Chromium needs zero outbound requests to load the book artwork.
    """
    # Inline all images as data URLs — avoids cross-process network hops.
    image_data_urls: dict[str, str] = {}
    for path in _collect_image_paths(book):
        try:
            data, ctype = get_image(path)
            image_data_urls[path] = _data_url(ctype or "image/png", data)
        except Exception as e:
            log.warning("PDF export: failed to inline image %s: %s", path, e)

    html, page_w, page_h = _build_html(book, image_data_urls)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            context = await browser.new_context(viewport={"width": page_w, "height": page_h})
            page = await context.new_page()
            # `wait_until='networkidle'` lets Google-Fonts CSS + woff2 requests
            # complete before we attempt to print.
            await page.set_content(html, wait_until="networkidle", timeout=60_000)
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
