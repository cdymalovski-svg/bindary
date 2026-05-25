"""Manuscript-to-Book importer.

Parses a `.docx`, `.txt`, or `.md` upload and returns a list of page-sized
text chunks suitable for one-text-block-per-page rendering in the editor.

Smart defaults:
  • Auto-detect splitting strategy:
      .docx → explicit page breaks → paragraphs (one per page) if none
      .md   → headings (#, ##) → paragraphs if none
      .txt  → blank-line separated paragraphs (one per page)
  • First chunk classified as a "title page" when it looks like a title
    (short, no terminal punctuation, ≤ 3 short lines). It gets centered
    cover-style typography; everything else uses body typography.
"""
from __future__ import annotations

import io
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Iterable, List, Optional

log = logging.getLogger("import")


# Tuned per page size; mirrors the editor's PAGE_MARGIN_PX (1cm ≈ 38px).
_PAGE_SIZES_PX = {
    "a4": (794, 1123),
    "letter": (816, 1056),
    "square": (800, 800),
    "book6x9": (576, 864),
}
_PAGE_MARGIN_PX = 38


# Hard cap so a runaway upload can't create thousands of pages.
MAX_PAGES = 200


@dataclass
class PageChunk:
    """One page's worth of text. `kind` controls editor typography."""
    text: str
    kind: str = "body"  # "title" | "heading" | "body"
    heading: Optional[str] = None  # used by chapter-style pages


# ---------------------------------------------------------------------------
# Format parsers
# ---------------------------------------------------------------------------

def _split_txt(text: str) -> List[PageChunk]:
    """Split plain text. Prefer explicit `Page N` markers; else fall back
    to blank-line-separated paragraphs (each paragraph becomes a page)."""
    # First-pass: every line on its own so the label detector can see them.
    by_label = _split_on_page_labels(text.splitlines())
    if by_label:
        return by_label
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [PageChunk(text=p) for p in paragraphs]


_MD_HEADING_RE = re.compile(r"^\s{0,3}(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


def _split_md(text: str) -> List[PageChunk]:
    """Split markdown on top-level headings. Each heading + the body below
    becomes one page (chapter-style). Fallback to paragraphs if no headings."""
    if not _MD_HEADING_RE.search(text):
        return _split_txt(text)
    chunks: List[PageChunk] = []
    # Split at every heading; the heading line stays with its body.
    parts = re.split(r"(?=^\s{0,3}#{1,3}\s)", text, flags=re.MULTILINE)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = _MD_HEADING_RE.match(part)
        heading = m.group(2).strip() if m else None
        body = _MD_HEADING_RE.sub("", part, count=1).strip() if m else part
        chunks.append(PageChunk(text=body, kind="heading" if heading else "body", heading=heading))
    return chunks


def _docx_paragraph_has_page_break(p) -> bool:
    """python-docx exposes runs with embedded breaks. We inspect the XML to
    find `<w:br w:type="page"/>` markers — these are the explicit page
    breaks authors insert via Ctrl+Enter."""
    for run in p.runs:
        for br in run._r.findall(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}br"
        ):
            if br.get(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}type"
            ) == "page":
                return True
    return False


_PAGE_LABEL_RE = re.compile(r"^\s*page\s+\d+\s*$", re.IGNORECASE)


def _split_on_page_labels(paragraphs: Iterable[str]) -> Optional[List[PageChunk]]:
    """If the manuscript uses explicit `Page N` label lines (a common
    convention in Word picture-book drafts), split on those labels and drop
    them. Returns None when no labels are found."""
    chunks: List[List[str]] = []
    current: List[str] = []
    found_any = False
    for raw in paragraphs:
        line = (raw or "").strip()
        if not line:
            continue
        if _PAGE_LABEL_RE.match(line):
            found_any = True
            if current:
                chunks.append(current)
                current = []
            continue
        current.append(line)
    if not found_any:
        return None
    if current:
        chunks.append(current)
    return [PageChunk(text="\n\n".join(lines)) for lines in chunks if lines]


def _split_docx(data: bytes) -> List[PageChunk]:
    """Parse a Word file. Detect page boundaries in this priority:
       1. Explicit `Page N` label paragraphs (common in picture-book drafts).
       2. Explicit Word page breaks (Ctrl+Enter).
       3. Each non-empty paragraph becomes its own page.
    """
    from docx import Document  # local import keeps cold-start light

    doc = Document(io.BytesIO(data))
    paragraphs = list(doc.paragraphs)

    # Strategy A: `Page N` labels — expand multi-line paragraphs into lines
    # because Word picture-book drafts often type "Page N" as the first line
    # of a paragraph rather than as its own block.
    all_lines: List[str] = []
    for p in paragraphs:
        for ln in (p.text or "").splitlines():
            all_lines.append(ln)
    by_label = _split_on_page_labels(all_lines)
    if by_label:
        return by_label

    # Strategy B: explicit Word page breaks.
    pages: List[List[str]] = [[]]
    has_explicit_breaks = False
    for p in paragraphs:
        if _docx_paragraph_has_page_break(p):
            has_explicit_breaks = True
            if pages[-1]:
                pages.append([])
        text = (p.text or "").strip()
        if text:
            pages[-1].append(text)

    if has_explicit_breaks:
        return [PageChunk(text="\n\n".join(lines)) for lines in pages if lines]

    # Strategy C: each paragraph becomes its own page.
    return [
        PageChunk(text=(p.text or "").strip())
        for p in paragraphs
        if (p.text or "").strip()
    ]


# ---------------------------------------------------------------------------
# Title-page heuristic
# ---------------------------------------------------------------------------

def _looks_like_title(text: str) -> bool:
    """Short, no terminal sentence punctuation, no more than 3 lines."""
    if not text:
        return False
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or len(lines) > 3:
        return False
    total = sum(len(ln) for ln in lines)
    if total > 90:
        return False
    last = lines[-1].rstrip()
    if last.endswith(("?", "!")):
        return False
    if last.endswith(".") and len(last) > 30:
        return False
    return True


def _classify_chunks(chunks: List[PageChunk]) -> List[PageChunk]:
    if chunks and chunks[0].kind == "body" and _looks_like_title(chunks[0].text):
        chunks[0] = PageChunk(text=chunks[0].text, kind="title")
    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_manuscript(filename: str, data: bytes) -> List[PageChunk]:
    """Returns a list of page chunks. Raises ValueError for unknown formats."""
    name = (filename or "").lower()
    if name.endswith(".docx"):
        chunks = _split_docx(data)
    elif name.endswith(".md") or name.endswith(".markdown"):
        chunks = _split_md(data.decode("utf-8", errors="replace"))
    elif name.endswith(".txt"):
        chunks = _split_txt(data.decode("utf-8", errors="replace"))
    else:
        raise ValueError(
            "Unsupported file type. Please upload a .docx, .md, or .txt file."
        )
    chunks = [c for c in chunks if c.text.strip()]
    if not chunks:
        raise ValueError("The document appears to be empty.")
    if len(chunks) > MAX_PAGES:
        log.warning(
            "Manuscript split into %d pages; capping at %d.", len(chunks), MAX_PAGES
        )
        chunks = chunks[:MAX_PAGES]
    return _classify_chunks(chunks)


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _text_to_html(text: str) -> str:
    """Newlines inside a chunk become <br/>; double newlines become paragraphs."""
    paragraphs = [_escape_html(p).replace("\n", "<br/>") for p in re.split(r"\n\s*\n", text)]
    return "".join(f"<p>{p}</p>" for p in paragraphs if p)


def chunks_to_pages(
    chunks: List[PageChunk],
    page_size_key: str = "a4",
) -> List[dict]:
    """Render parsed chunks into Page dicts ready for the Book model."""
    page_w, page_h = _PAGE_SIZES_PX.get(page_size_key, _PAGE_SIZES_PX["a4"])

    pages: List[dict] = []
    for chunk in chunks:
        if chunk.kind == "title":
            block = _title_block(chunk.text, page_w, page_h)
            page = _make_page(blocks=[block], show_page_number=False)
        elif chunk.kind == "heading":
            blocks = _heading_blocks(chunk.heading or "", chunk.text, page_w, page_h)
            page = _make_page(blocks=blocks, show_page_number=True)
        else:
            block = _body_block(chunk.text, page_w, page_h)
            page = _make_page(blocks=[block], show_page_number=True)
        pages.append(page)
    return pages


def _make_page(*, blocks: List[dict], show_page_number: bool) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "blocks": blocks,
        "background_color": "#FFF8DC",
        "show_page_number": show_page_number,
        "page_number_align": "right",
        "page_number_size": 14,
        "page_number_font": "Cormorant Garamond",
        "full_bleed": False,
    }


def _body_block(text: str, page_w: int, page_h: int) -> dict:
    """Centered text block in the top half — leaves room for an illustration
    below. Width = 70% of inner page; height generous enough for ~12 lines so
    the imported text rarely clips even at the default 18px size."""
    inner_w = page_w - _PAGE_MARGIN_PX * 2
    width = round(inner_w * 0.70)
    x = round((page_w - width) / 2)
    height = round((page_h - _PAGE_MARGIN_PX * 2) * 0.45)
    y = _PAGE_MARGIN_PX + 24
    return {
        "id": str(uuid.uuid4()),
        "type": "text",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "z_index": 1,
        "html": _text_to_html(text),
        "font_family": "Cormorant Garamond",
        "font_size": 18,
        "text_align": "left",
        "color": "#1C1B19",
        "is_chapter": False,
    }


def _heading_blocks(heading: str, body: str, page_w: int, page_h: int) -> List[dict]:
    """Chapter-style page: large heading at top, body below."""
    inner_w = page_w - _PAGE_MARGIN_PX * 2
    h_width = round(inner_w * 0.85)
    h_x = round((page_w - h_width) / 2)
    h_y = _PAGE_MARGIN_PX + 36
    heading_block = {
        "id": str(uuid.uuid4()),
        "type": "text",
        "x": h_x,
        "y": h_y,
        "width": h_width,
        "height": 100,
        "z_index": 2,
        "html": f"<p>{_escape_html(heading)}</p>",
        "font_family": "Playfair Display",
        "font_size": 40,
        "text_align": "center",
        "color": "#1C1B19",
        "is_chapter": True,
    }
    if not body.strip():
        return [heading_block]
    body_width = round(inner_w * 0.70)
    body_x = round((page_w - body_width) / 2)
    body_y = h_y + 120
    body_height = round((page_h - _PAGE_MARGIN_PX * 2) * 0.50)
    body_block = {
        "id": str(uuid.uuid4()),
        "type": "text",
        "x": body_x,
        "y": body_y,
        "width": body_width,
        "height": body_height,
        "z_index": 1,
        "html": _text_to_html(body),
        "font_family": "Cormorant Garamond",
        "font_size": 18,
        "text_align": "left",
        "color": "#1C1B19",
        "is_chapter": False,
    }
    return [heading_block, body_block]


def _title_block(text: str, page_w: int, page_h: int) -> dict:
    """Cover-style centered title in the upper third of the page."""
    inner_w = page_w - _PAGE_MARGIN_PX * 2
    width = round(min(inner_w, 640))
    x = round((page_w - width) / 2)
    # Roomy box so 1–3 line titles don't clip in the PDF (preflight resets <p>
    # margins to 0, so this is the actual rendered height).
    height = 260
    y = round(page_h * 0.22)
    # Scale title font down for longer titles so it stays on 1–2 lines.
    longest_line = max((len(ln) for ln in text.splitlines() if ln.strip()), default=12)
    base_size = round(page_w * 0.10)
    scale = 28 / longest_line if longest_line > 28 else 1.0
    font_size = max(28, round(base_size * scale))
    return {
        "id": str(uuid.uuid4()),
        "type": "text",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "z_index": 10,
        "html": _text_to_html(text),
        "font_family": "Playfair Display",
        "font_size": font_size,
        "text_align": "center",
        "color": "#1C1B19",
        "is_chapter": False,
    }


def derive_title_and_author(chunks: List[PageChunk]) -> tuple[str, str]:
    """Best-effort metadata extraction from the first chunk(s).

    Convention: the first chunk's first line is the title; if a second line
    exists and looks like a name, it's the author."""
    if not chunks:
        return "Untitled Book", ""
    first = chunks[0].text.strip()
    lines = [ln.strip() for ln in first.splitlines() if ln.strip()]
    if not lines:
        return "Untitled Book", ""
    title = lines[0]
    author = ""
    if len(lines) > 1:
        second = lines[1]
        # Reject obvious "by …" prefixes; keep just the name.
        author = re.sub(r"^\s*by\s+", "", second, flags=re.I).strip()
    return title, author
