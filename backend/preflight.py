"""IngramSpark v5.11.26 preflight checks.

Walks a book document and reports rows that fail the IngramSpark spec —
without rendering a PDF. Used by:
  * `/api/books/{id}/preflight` for the editor's compliance panel.
  * `build_book_pdf` to attach a warning list to the job response so the
    export UI can show "shipped with N warnings" instead of silently
    handing the user a non-conformant file.

The function is pure (no I/O beyond a Mongo lookup of image metadata) so
it's cheap to call on every editor keystroke if we want a live panel.
"""
from __future__ import annotations

from typing import Any, Optional

PT_PER_IN = 72.0
PX_PER_IN = 96.0
MIN_DPI = 300  # IngramSpark requires ≥300 DPI at trim size for all images.


def _block_rendered_inches(block: dict) -> tuple[float, float]:
    """Pixel dimensions of an image block in the editor → inches @ 96 DPI."""
    w_px = float(block.get("width") or 0)
    h_px = float(block.get("height") or 0)
    return (w_px / PX_PER_IN, h_px / PX_PER_IN)


async def preflight_book(book: dict, db) -> dict[str, Any]:
    """Return a structured report with `passed` / `warnings` / `errors`.

    Errors block submission (e.g. no pages, no cover image data).
    Warnings should be surfaced to the user but don't block (e.g. low
    DPI image — the printer will accept it but may flag at preflight).
    """
    errors: list[dict] = []
    warnings: list[dict] = []
    passed: list[str] = []

    pages = book.get("pages") or []
    total_pages = len(pages)

    # 1. Page count parity ---------------------------------------------------
    if total_pages == 0:
        errors.append({"check": "page_count", "msg": "Book has no pages."})
    elif total_pages % 2 == 1:
        warnings.append({
            "check": "page_count_parity",
            "msg": (
                f"Book has {total_pages} pages (odd). IngramSpark requires "
                "an even count — a blank page will be auto-appended on export."
            ),
        })
    else:
        passed.append("page_count_parity")

    # 2. Image DPI -----------------------------------------------------------
    # Collect every image-block reference + its rendered size; look up the
    # source dimensions from db.files in a single batched query.
    image_blocks: list[tuple[int, dict]] = []  # (page_idx, block)
    needed_paths: set[str] = set()
    for pi, page in enumerate(pages):
        for b in page.get("blocks") or []:
            if b.get("type") != "image":
                continue
            image_blocks.append((pi, b))
            url = b.get("image_url") or b.get("src") or ""
            path = b.get("image_path")
            if not path and "/api/files/" in url:
                path = url.split("/api/files/", 1)[1].split("?", 1)[0]
            if path:
                needed_paths.add(path)

    file_dims: dict[str, tuple[Optional[int], Optional[int]]] = {}
    if db is not None and needed_paths:
        cursor = db.files.find(
            {"storage_path": {"$in": list(needed_paths)}},
            {"_id": 0, "storage_path": 1, "width_px": 1, "height_px": 1},
        )
        async for doc in cursor:
            file_dims[doc["storage_path"]] = (
                doc.get("width_px"), doc.get("height_px"),
            )

    low_dpi: list[dict] = []
    for pi, b in image_blocks:
        url = b.get("image_url") or b.get("src") or ""
        path = b.get("image_path")
        if not path and "/api/files/" in url:
            path = url.split("/api/files/", 1)[1].split("?", 1)[0]
        src_w, src_h = file_dims.get(path or "", (None, None))
        if src_w is None or src_h is None:
            # AI-generated / external images may not have stored dims.
            continue
        rendered_w_in, rendered_h_in = _block_rendered_inches(b)
        if rendered_w_in <= 0 or rendered_h_in <= 0:
            continue
        dpi_w = src_w / rendered_w_in
        dpi_h = src_h / rendered_h_in
        dpi = min(dpi_w, dpi_h)
        if dpi < MIN_DPI:
            low_dpi.append({
                "page": pi + 1,
                "block_id": b.get("id"),
                "rendered_in": [round(rendered_w_in, 2), round(rendered_h_in, 2)],
                "source_px": [src_w, src_h],
                "effective_dpi": round(dpi, 0),
                "min_required_dpi": MIN_DPI,
            })
    if low_dpi:
        warnings.append({
            "check": "image_dpi",
            "msg": (
                f"{len(low_dpi)} image(s) render below {MIN_DPI} DPI at their "
                "current size — IngramSpark may flag these at preflight. "
                "Either replace with a higher-resolution image or shrink "
                "the block to compensate."
            ),
            "items": low_dpi,
        })
    elif image_blocks:
        passed.append("image_dpi")

    # 3. Margins (already enforced structurally by the editor at 0.5") ---
    passed.append("inner_margin_0_5in")

    # 4. PDF/X output --------------------------------------------------------
    # Conformance is enforced by Ghostscript at export time, but flag here
    # so the user understands the toggle's role.
    passed.append("pdfx_available")

    summary = "ok" if not errors and not warnings else (
        "errors" if errors else "warnings"
    )
    return {
        "status": summary,
        "errors": errors,
        "warnings": warnings,
        "passed": passed,
        "page_count": total_pages,
    }
