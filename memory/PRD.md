# Bindery — Book Template Studio

## Original Problem Statement
Build me a book template app to be able to add texts and illustrations, page numbers, ability to edit text, size the illustrations. Sample attached, finally want to save the book to pdf.

## User Choices
- Page size: user-selectable per book (A4, US Letter, Square, 6x9)
- Text formatting: rich text (font, size, bold/italic/underline, alignment, color)
- Illustrations: image upload AND drag/drop reposition + resize
- Persistence: yes (MongoDB)
- Authentication: none (single-user / open)

## Architecture
- **Backend**: FastAPI + MongoDB. Books → pages → blocks (text or image). All ids are uuid4. Object storage for images via Emergent Object Storage (EMERGENT_LLM_KEY).
- **Frontend**: React + Tailwind + Shadcn UI. `react-rnd` for drag/resize. `html2canvas` + `jspdf` for client-side PDF export.
- **Design system**: Editorial/Old-Money-Tech aesthetic — Cormorant Garamond + Outfit, paper canvas (#F9F6F0), dark structural sidebar (#1C1B19), terracotta accent (#9E4532).

## User Personas
- Storybook author / illustrator who wants to lay out a children's book.
- Self-publishing writer composing a print-ready PDF manuscript.

## Core Requirements (static)
- Create, edit, delete books with multiple pages.
- Per-page text blocks with rich-text formatting and per-page image blocks with drag/resize.
- Page numbers (toggle per page).
- Add / duplicate / delete / reorder pages.
- Save book to MongoDB.
- Export full book to PDF.

## What's been implemented (2026-02 / iteration 1 MVP)
- Backend: Books CRUD, image upload via object storage, file serving, models with uuid ids.
- Frontend Dashboard: book library, create-book dialog, delete, navigation to editor.
- Frontend Editor: 3-pane layout (dark page sidebar, paper desk canvas, floating BlockProperties).
- Drag/resize via react-rnd, contentEditable rich-text with bold/italic/underline/align/color/size/font.
- Page size selector (A4 / Letter / Square / 6x9), automatic page numbers, toggle per page.
- Image upload via Emergent Object Storage, served through `/api/files/{path}`.
- Client-side PDF export (html2canvas + jsPDF).

## What's been implemented (2026-02 / iteration 11)
- Fixed: single-click on existing text block now reliably enters edit mode (mouseup-with-4px-threshold replaces unreliable onClick).
- Expanded font palette from 6 → 31 curated Google Fonts in 5 categories (Serif / Sans-serif / Display / Handwritten / Monospace) for both text blocks and page numbers; all fonts loaded via index.css @import.
- New "Contents" toolbar button (visible in Chapter-book mode) auto-inserts a TOC text block aggregating every chapter heading with its displayed page number. Empty placeholder blocks are auto-cleared on insert.
- New "Design cover" toolbar button (visible on page 1): one-click cover composer — sets full bleed, hides the page number, resizes the existing illustration to fill the page as a backdrop, and places a centered Playfair Display title + italic Cormorant author. Title font scales down for long titles. Replaces any prior text blocks on the cover.
- Verified end-to-end via testing agent (iteration_11) and main-agent smoke tests.

## What's been implemented (2026-02-25 / iteration 14 — WYSIWYG PDF)
- **PDF export now renders via headless Chromium (Playwright)** — replaces the ReportLab approach that substituted fonts and clipped text mid-sentence.
- `/app/backend/pdf_builder.py` builds a self-contained HTML mirroring the editor's exact CSS (page size, margins, block coordinates, padding, line-height, Google Fonts @import), inlines images as base64 data URLs, and prints via `page.pdf()` with `prefer_css_page_size=True` so the output is a 1:1 vector copy of what the editor displays.
- Added `PLAYWRIGHT_BROWSERS_PATH=/pw-browsers` to backend env so the FastAPI process finds the Chromium install.
- All 48 backend tests pass (including 5 PDF export tests covering small/large books, 404s, full-bleed pages, and fresh creation flow). 16-page test book exports in ~5s.

## What's been implemented (2026-05-26 / iteration 15 — production hardening + manuscript import)
- **Manuscript import**: `POST /api/books/import` accepts `.docx` / `.md` / `.txt` and smart-splits into pages. Detects `Page N` label lines (even when buried in multi-line Word paragraphs), explicit Word page breaks, or paragraph fallback. Detects title vs body. Page 1 styled as cover (Playfair Display); body uses Cormorant. Dashboard's New Book dialog gained an "Import manuscript (optional)" file picker.
- **Page sidebar UX**: Move-up / move-down arrows + between-pages "+" insert handle on every thumbnail.
- **Responsive toolbar**: Button labels collapse to icons on narrower widths; author input hides under md; never overflows.
- **PDF export — production-grade pipeline rewrite:**
  - **Job-based flow** (avoids long-lived requests): `POST /api/books/{id}/pdf-jobs` → poll `GET /pdf-jobs/{job_id}` → `GET /pdf-jobs/{job_id}/download`. Every request returns in < 1s, immune to proxy timeouts.
  - **Multi-pod safe**: job state stored in MongoDB (`pdf_jobs` collection with TTL index) + PDF bytes in object storage. Any pod can poll/serve any job.
  - **URL avoids `.pdf` in path** (some CDNs route dot-pdf URLs as static-file requests → 404).
  - **Chunked rendering**: render 10 pages at a time in a fresh Chromium session, merge with pypdf. Memory peak is constant regardless of book size.
  - **On-demand image fetching** via `page.route()` interception: only one image in RAM at a time.
  - **Image downscaling** at 300 DPI (3300 px long-edge max) via Pillow — bounds Chromium decoded-texture memory.
  - **Chromium self-installs** on first PDF request if binary missing (defensive). Production has it pre-baked at `/root/.cache/ms-playwright`.
  - **Frontend resilience**: 90s timeout per polling call, friendly "still building Xs" toast counter, auto-retry on transient 5xx for saves.
- **Library page robustness**: `list_books` reverted to simple find after an aggregation attempt broke prod (`$arrayElemAt`/`$size` issues on certain Mongo configurations).
- All 59 backend tests pass. Production deployed to `bindery.au` (custom domain).

## What's been implemented (2026-05-27 / iteration 16 — production PDF timeout fix)
- **Root cause of 290s production timeouts identified**: `wait_until="load"` was blocking each chunk on Google Fonts CSS + WOFF2 fetches. With egress latency to `fonts.googleapis.com`, every chunk hung up to 60s — 6 chunks × 60s ≈ matched the observed 290s frontend timeout.
- **Fixes:**
  - Switched `page.set_content` to `wait_until="domcontentloaded"` + a bounded 3-second `document.fonts.ready` race. Fonts load best-effort; if the network is slow Chromium falls back to its default serif rather than hanging the entire export.
  - **Single long-lived browser** across all chunks (saves ~3-5s per chunk launch overhead). Each chunk gets a fresh `context` (cheap) but reuses the launched browser.
  - **Chunk size 10 → 5 pages** to halve peak memory pressure in production containers.
  - Per-chunk PDF timeout reduced 60s → 30s — any longer is a stuck render, not a slow one.
- **Live progress reporting:** `build_book_pdf` now accepts a `progress_cb`. The job worker writes the current `stage` to Mongo on each transition ("launching chromium" → "rendering chunk 3/8" → "merging chunks" → "uploading"). Status endpoint returns `stage`; the frontend toast shows it ("Building PDF… rendering chunk 3/8 · 47s"). Failure path now includes a `trace` snippet so users see the actual exception, not just "PDF build failed".
- **New `/api/pdf-health` diagnostic endpoint** — reports chromium-launchable status, `PLAYWRIGHT_BROWSERS_PATH` env vs detected install path, disk free, memory (MemTotal/MemAvailable/SwapFree), and recent failed + pending jobs. Tells us in one curl whether the issue is Chromium, disk, memory, or stuck workers.
- **Frontend poll cap raised 5 min → 10 min** to comfortably accommodate large books with cold-start Chromium installs.
- All 53 backend tests pass (5 dedicated to the job-polling flow). Preview `/api/pdf-health` reports healthy.


## What's been implemented (2026-05-27 / iteration 17 — orphaned-asset recovery)
- **Root cause of broken thumbnails in production**: production's MongoDB had asset records whose `storage_path` pointed at objects only ever uploaded into preview's object-storage bucket (preview and production are isolated buckets). 21 of 23 assets in a real user's library were orphaned: DB record present, bytes missing.
- **Backend** — new `POST /api/assets/{asset_id}/replace` overwrites bytes at the asset's **existing** `storage_path`, leaving the id and URL untouched. Every block in every book that references the asset's `/api/files/...` URL is healed immediately without a single canvas edit.
- **Frontend AssetsPanel**:
  - Per-tile broken-state detection via `<img onError>` — no extra round-trip, robust to any failure mode (404, 500, network blocked).
  - Broken tiles render an amber "Missing" placeholder with an inline "Re-upload" button (plus a "Re-upload bytes" context-menu item) that fires the new replace endpoint and cache-busts the `<img>` so the new bytes appear immediately.
  - Broken tiles are non-draggable so users can't drop a placeholder onto canvas.
  - A panel-wide "Fix N missing images…" button (visible only when broken tiles are detected) opens a multi-file picker that matches uploaded files to broken slots by filename (case-insensitive, with stem-without-extension fallback). Unmatched files are appended as fresh assets so nothing is silently lost.
- **Canvas cache-busting**: Editor maintains an `assetCacheBuster` integer bumped after any replace. Canvas `<img>` URLs append `?v=<n>` so previously-cached failures are refetched and existing book pages light up the moment the user re-uploads, with no save/reload required.
- New tests in `tests/test_assets_api.py::TestAssetReplace`: verifies id+path preservation, byte overwrite, 404 on missing asset, and 400 on non-image uploads. All 56 backend tests now pass.

## What's been implemented (2026-05-30 / iteration 21 — live-sync TOC)
- **Table of Contents now auto-updates.** TOC blocks inserted via the toolbar are tagged `is_toc: true`. A live-sync `useEffect` watches `book` and any change to chapters (add / delete / rename / reorder pages / change `page_number_start`) regenerates the TOC html in-place so the displayed entries always match reality. Skips the run while the user is actively editing the TOC block so the caret never gets yanked. Uses `skipNextAutoSaveRef` so passive TOC refreshes piggy-back on the originating user edit's autosave instead of triggering a second round-trip.
- Pure `computeTocPayload(book)` helper extracted so the one-shot inserter and the live-sync use identical entry logic — they can never drift.
- Backend `Block` model accepts the new `is_toc: bool = False` field; all 87 tests still pass.
- Verified end-to-end: created a chapter book, added 2 chapters, inserted Contents (2 entries), added a 3rd chapter on a new page — TOC immediately grew to 3 entries and re-paginated the second chapter from blank → "2" without any user action.

## What's been implemented (2026-05-30 / iteration 20 — page navigation)
- **Editor canvas now scrolls between pages**: floating ← / → buttons on either side of the desk, plus keyboard ←/→ and one-finger horizontal swipe on iPad.
- **Step size matches view mode**: single view advances 1 page; spread view advances by a full spread (2 pages). Cover is treated as its own half-spread, so prev from "pages 1-2" lands on the cover.
- Arrows hide at page boundaries (no prev on the cover, no next on the last page). Keyboard handler ignores ←/→ while typing in inputs/textareas/contentEditable so text editing is never hijacked.
- Verified end-to-end via screenshot (single, spread, arrow click, keyboard).

## What's been implemented (2026-05-30 / iteration 19 — cover title visible in library)
- **Bug**: Library cards rendered only the cover's image block (`cover_image_url`), so any title/author text laid over the artwork (e.g. via "Design cover") was missing from the thumbnail. Editor's first-page thumbnail had no issue but was duplicating render logic.
- **Backend**: `BookSummary` now also includes `cover_page` — the full first-page object (image + text blocks). `list_books` populates it from `pages[0]`.
- **Frontend**: New `components/PagePreview.jsx` — a single shared component that renders a page composition (any blocks, optional page number, fitted to a given pixel width). `Dashboard.jsx` BookCard uses it with a `ResizeObserver` so covers always fit the card without cropping, regardless of grid breakpoint or page aspect ratio. Falls back to the legacy image render, then to the placeholder, if `cover_page` is missing.
- All 87 backend tests pass; library and editor verified via screenshot.

## What's been implemented (2026-05-30 / iteration 18 — AI illustration generator)
- **`POST /api/assets/generate`** — takes `{prompt, book_id?}`, calls Gemini Nano Banana (`gemini-3.1-flash-image-preview`) via `emergentintegrations` using the universal `EMERGENT_LLM_KEY`, decodes the base64 image, persists it through the same object-storage path as `/upload`, and returns the standard asset shape. The generated asset shows up in the Assets panel exactly like an uploaded one (drag-onto-page, replace, delete all work).
- **Generate dialog**: new `GenerateImageDialog.jsx` rendered via a React portal (so editor canvas transforms don't trap it). Has a 4-row textarea (2000-char limit), four curated example prompts, `⌘↵` to submit, Escape-to-close, loading spinner, and surfaces upstream errors directly. The dialog is reached from a new "Generate with AI…" button under "Upload images" in the Assets panel.
- DB records tagged with `source: "ai_generated"` and the original `ai_prompt` for future filtering / analytics.
- Smoke-tested end-to-end (~17s real generation), 1.14 MB JPEG persisted, listed in `/api/assets`, served by `/api/files/...`.

## Prioritized Backlog
### P1
- Refactor `Editor.jsx` (now ~1672 lines): split into toolbar / canvas / TOC builder modules + custom hooks for autosave & selection.
- Multi-select + alignment guides + snapping.
- Undo/Redo history.

### P2
- Live-sync TOC (currently a one-shot insert; auto-update when chapters change).
- Drag-to-reorder pages in the sidebar (arrows work; drag would be nicer for 70+ page books).
- Text on path / shape blocks / decorative dividers.
- More page templates ("Children's book", "Photo book", "Manuscript").
- Share read-only preview link.
- PDF preview button (open in new tab instead of immediate download).

### P3
- AI text editing assistance.
- Multi-user accounts + library sharing.
- Pan-while-zoomed gesture on iPad (drag canvas while pinch >100%).

## Next Tasks
- Refactor `Editor.jsx` for maintainability (now P1 — file size is regression-prone).
- Undo/Redo history.
- Live-sync TOC.
