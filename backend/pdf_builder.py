"""Server-side PDF generation for the book editor.

Builds a print-ready PDF directly from the book's JSON document. Text is
emitted as native PDF text (vector — pixel-perfect at every zoom), images
are downloaded from object storage and embedded inline, and page numbers
match the editor's display rules.

The function is deliberately defensive: an unknown font falls back to a safe
PDF built-in, a broken image is silently skipped (rather than failing the
whole export), and out-of-page block coordinates are still drawn (the PDF
viewer simply clips them).
"""

from __future__ import annotations

import io
import logging
import re
from typing import Optional

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph
from reportlab.lib.utils import ImageReader

log = logging.getLogger("pdf")

# Frontend page sizes mirror /app/frontend/src/lib/pageSizes.js (96 DPI px).
PAGE_SIZES_PX = {
    "a4": (794, 1123),
    "letter": (816, 1056),
    "square": (800, 800),
    "book6x9": (576, 864),
}
PAGE_MARGIN_PX = 38  # ~1cm at 96dpi, matches the editor
PX_TO_PT = 72 / 96.0  # PDF user-space points

# Map our Google-Font families to the closest PDF built-ins. Custom font
# embedding is a follow-up — built-ins keep the export dependency-free and
# print-shop-safe.
FONT_MAP = {
    # Serif
    "Cormorant Garamond": "Times-Roman",
    "Playfair Display": "Times-Roman",
    "EB Garamond": "Times-Roman",
    "Crimson Text": "Times-Roman",
    "Libre Baskerville": "Times-Roman",
    "Lora": "Times-Roman",
    "Bitter": "Times-Roman",
    "Merriweather": "Times-Roman",
    "Georgia": "Times-Roman",
    "Times New Roman": "Times-Roman",
    # Sans
    "Outfit": "Helvetica",
    "Montserrat": "Helvetica",
    "Poppins": "Helvetica",
    "Nunito": "Helvetica",
    "Raleway": "Helvetica",
    "Work Sans": "Helvetica",
    "DM Sans": "Helvetica",
    "Helvetica": "Helvetica",
    "Arial": "Helvetica",
    # Display — treat as bold sans for impact
    "Bebas Neue": "Helvetica-Bold",
    "Abril Fatface": "Times-Bold",
    "Lobster": "Helvetica-Bold",
    "Pacifico": "Helvetica-Bold",
    # Handwritten — fall to italic serif
    "Caveat": "Times-Italic",
    "Dancing Script": "Times-Italic",
    "Indie Flower": "Times-Italic",
    "Kalam": "Times-Italic",
    "Sacramento": "Times-Italic",
    # Monospace
    "Fira Code": "Courier",
    "Inconsolata": "Courier",
    "JetBrains Mono": "Courier",
    "Courier New": "Courier",
}


def _resolve_font(family: Optional[str]) -> str:
    if not family:
        return "Times-Roman"
    return FONT_MAP.get(family, "Times-Roman")


def _align(text_align: Optional[str]) -> int:
    return {"center": TA_CENTER, "right": TA_RIGHT}.get((text_align or "left").lower(), TA_LEFT)


def _hex(color: Optional[str], default: str = "#000000") -> HexColor:
    try:
        return HexColor(color or default)
    except Exception:
        return HexColor(default)


def _is_dark_hex(color: str) -> bool:
    """Pick contrasting page-number colour. Mirrors isDarkHex() in Editor.jsx."""
    try:
        c = color.lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        return (r * 299 + g * 587 + b * 114) / 1000 < 128
    except Exception:
        return False


def _html_to_paragraph(html: str) -> str:
    """ReportLab's Paragraph supports a small XML-ish subset (<b>, <i>, <u>,
    <font color>, <br/>). Our editor stores `<p>...</p>` chunks with the same
    formatting tags, so we just normalise `<p>` to `<br/>` separators."""
    if not html:
        return ""
    # Convert paragraph wrappers to soft line breaks so multiple <p>s stay
    # vertically separated. Drop unsupported tags.
    s = html
    s = re.sub(r"</p>\s*<p[^>]*>", "<br/><br/>", s, flags=re.I)
    s = re.sub(r"</?p[^>]*>", "", s, flags=re.I)
    s = re.sub(r"<div[^>]*>", "", s, flags=re.I)
    s = re.sub(r"</div>", "<br/>", s, flags=re.I)
    s = re.sub(r"<span[^>]*>", "", s, flags=re.I)
    s = re.sub(r"</span>", "", s, flags=re.I)
    # ReportLab accepts <br/> not <br>
    s = re.sub(r"<br\s*/?>", "<br/>", s, flags=re.I)
    return s


def _draw_text_block(c, block: dict, page_h_pt: float):
    """Draw a text block at its declared geometry. The text is drawn at its
    natural rendered height but a canvas clipping path constrains it to the
    block's declared width × height — matching the editor's `overflow: hidden`
    behaviour without auto-growing the box."""
    font = _resolve_font(block.get("font_family"))
    size = float(block.get("font_size") or 18)
    color = _hex(block.get("color"))
    align = _align(block.get("text_align"))

    style = ParagraphStyle(
        name="b",
        fontName=font,
        fontSize=size,
        leading=size * 1.45,
        textColor=color,
        alignment=align,
    )
    raw = _html_to_paragraph(block.get("html") or "")
    if not raw.strip():
        return
    try:
        p = Paragraph(raw, style)
    except Exception:
        plain = re.sub(r"<[^>]+>", "", raw)
        p = Paragraph(plain, style)

    # Approximate the editor's 4px-top, 8px-left inner padding so text starts
    # at the same place inside the box.
    pad_x_pt = 8 * PX_TO_PT
    pad_y_pt = 4 * PX_TO_PT
    box_w_pt = float(block.get("width") or 100) * PX_TO_PT
    box_h_pt = float(block.get("height") or 100) * PX_TO_PT
    inner_w_pt = max(0.0, box_w_pt - 2 * pad_x_pt)
    box_x_pt = float(block.get("x") or 0) * PX_TO_PT
    box_y_top_pt = page_h_pt - float(block.get("y") or 0) * PX_TO_PT

    # Wrap to find the natural rendered height for the available width. The
    # text is drawn at this natural height (so it isn't squished) but the
    # canvas is clipped to the declared box so any overflow is hidden — the
    # same WYSIWYG behaviour the editor uses.
    _, natural_h_pt = p.wrap(inner_w_pt, 1_000_000)

    c.saveState()
    # ReportLab Y is bottom-up. The clip rectangle's bottom-left is at
    # (box_x_pt, box_y_top_pt - box_h_pt).
    path = c.beginPath()
    path.rect(box_x_pt, box_y_top_pt - box_h_pt, box_w_pt, box_h_pt)
    c.clipPath(path, stroke=0, fill=0)
    # Place the paragraph so its TOP aligns with the box's top edge (after
    # padding). drawOn(x, y) anchors at the paragraph's bottom-left.
    text_y_pt = box_y_top_pt - pad_y_pt - natural_h_pt
    p.drawOn(c, box_x_pt + pad_x_pt, text_y_pt)
    c.restoreState()


def _draw_image_block(c, block: dict, page_h_pt: float, get_image):
    img_url = block.get("image_url") or ""
    img_path = block.get("image_path") or ""
    if not img_path and img_url.startswith("/api/files/"):
        img_path = img_url[len("/api/files/") :]
    if not img_path:
        return
    try:
        data, _ = get_image(img_path)
    except Exception as e:
        log.warning("PDF export: image fetch failed for %s: %s", img_path, e)
        return
    try:
        reader = ImageReader(io.BytesIO(data))
    except Exception as e:
        log.warning("PDF export: image decode failed for %s: %s", img_path, e)
        return
    w_pt = float(block.get("width") or 100) * PX_TO_PT
    h_pt = float(block.get("height") or 100) * PX_TO_PT
    x_pt = float(block.get("x") or 0) * PX_TO_PT
    y_top_pt = page_h_pt - float(block.get("y") or 0) * PX_TO_PT
    c.drawImage(
        reader,
        x_pt,
        y_top_pt - h_pt,
        width=w_pt,
        height=h_pt,
        preserveAspectRatio=True,
        anchor="c",
        mask="auto",
    )


def build_book_pdf(book: dict, get_image) -> bytes:
    """Render a complete book to a bytes-buffer PDF.

    Parameters
    ----------
    book : dict
        The Book document straight from MongoDB.
    get_image : callable(path) -> (bytes, content_type)
        Function that fetches the binary contents of an image at the given
        storage path. Injected so this module stays decoupled from the storage
        provider (and so tests can stub it).
    """
    page_size = PAGE_SIZES_PX.get(book.get("page_size") or "a4", PAGE_SIZES_PX["a4"])
    w_pt = page_size[0] * PX_TO_PT
    h_pt = page_size[1] * PX_TO_PT

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(w_pt, h_pt))

    pages = book.get("pages") or []
    page_number_start = int(book.get("page_number_start") or 1)
    total_pages = len(pages)

    for i, page in enumerate(pages):
        bg = page.get("background_color") or "#FFF8DC"
        full_bleed = bool(page.get("full_bleed"))
        margin_pt = (0 if full_bleed else PAGE_MARGIN_PX) * PX_TO_PT

        # Background — for non-full-bleed pages, paint a white margin then
        # the colour panel inset by margin (matches the editor).
        c.setFillColor(_hex("#FFFFFF"))
        c.rect(0, 0, w_pt, h_pt, stroke=0, fill=1)
        c.setFillColor(_hex(bg))
        c.rect(margin_pt, margin_pt, w_pt - 2 * margin_pt, h_pt - 2 * margin_pt, stroke=0, fill=1)

        # Blocks in z-index order so stacking matches the editor.
        blocks = sorted(page.get("blocks") or [], key=lambda b: b.get("z_index") or 0)
        for b in blocks:
            if b.get("type") == "image":
                _draw_image_block(c, b, h_pt, get_image)
            elif b.get("type") == "text":
                _draw_text_block(c, b, h_pt)

        # Page number — match editor rules: hide before page_number_start,
        # always hide on the back cover when book has > 1 page.
        is_back_cover = total_pages > 1 and i == total_pages - 1
        is_before_start = (i + 1) < page_number_start
        if page.get("show_page_number") and not is_back_cover and not is_before_start:
            num = i + 1 - page_number_start + 1
            font = _resolve_font(page.get("page_number_font"))
            size = float(page.get("page_number_size") or 14)
            align = (page.get("page_number_align") or "right").lower()
            color = _hex("#E8E2D4" if _is_dark_hex(bg) else "#3A3833")
            c.setFont(font, size)
            c.setFillColor(color)
            text = str(num)
            text_w = c.stringWidth(text, font, size)
            y = margin_pt + 16 * PX_TO_PT
            if align == "left":
                x = margin_pt + 16 * PX_TO_PT
            elif align == "center":
                x = (w_pt - text_w) / 2
            else:
                x = w_pt - margin_pt - 16 * PX_TO_PT - text_w
            c.drawString(x, y, text)

        c.showPage()

    c.save()
    return buf.getvalue()
