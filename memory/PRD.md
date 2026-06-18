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

## What's been implemented (2026-05-31 / iteration 29 — Interior bleed for PDF/X-1a)
- **The last IngramSpark gap is closed**: when **Print-ready (PDF/X-1a)** is on, every interior page now ships with **0.125" bleed** on top + bottom + the OUTSIDE edge (bind stays flush). Page parity follows the standard recto/verso convention: even page-index = right page (bleed on right), odd = left page (bleed on left).
- **Background colour fills the bleed strip** so the printer's trim cuts through coloured paper, not a white edge.
- **TrimBox + BleedBox** are written into every page via pypdf so the file is fully PDF/X-1a conformant — the printer knows exactly where to cut. Verified on a 3-page export:
  - MediaBox 609.1 × 618 pt
  - TrimBox 600.1 × 600 pt
  - Trim offset (0, 9) on right pages, (9, 9) on left pages — alternating by parity
  - All four flags present: `/TrimBox` ✓, `/BleedBox` ✓, `/GTS_PDFX` ✓, `/DeviceCMYK` ✓
- **`pdf_builder.py`** changes: new `INTERIOR_BLEED_PX`, `_page_bleed_sides()`, `pdfx_bleed` parameter threaded through `_build_html` → `_render_page`. Single-chunk path now also goes through pypdf when bleed is on so per-page TrimBox can be written.
- **Backend wiring**: worker passes `pdfx_bleed=pdfx` so the interior bleed is enabled in lock-step with the PDF/X conversion — no separate toggle needed.
- **New regression test** `test_pdfx_export_adds_interior_bleed_and_trimbox` validates page-parity alternation and bleed math. 90/90 backend tests pass.

### Updated IngramSpark compliance status
| Requirement | Status |
|---|---|
| PDF/X-1a:2001 covers + interiors | ✅ |
| CMYK / no /DeviceRGB / no /sRGB | ✅ |
| All fonts embedded | ✅ |
| Cover bleed 0.125" outside edges | ✅ |
| **Interior bleed 0.125" top/bottom/outside** | ✅ **CLOSED** |
| **TrimBox + BleedBox per page** | ✅ **NEW** |
| Cover layout BACK ∣ SPINE ∣ FRONT | ✅ |
| 300 ppi cap | ✅ |
| Single-page interior PDFs | ✅ |
| No crop / reg marks | ✅ |
| `_cov.pdf` naming | ✅ |
| Total ink ≤ 240% | 🟡 effectively yes via default CMYK ICC (Ink-warning toggle helps designers catch it before export) |

## What's been implemented (2026-05-31 / iteration 28 — Streaming PDF/X-1a progress)
- **Live page-by-page progress** during PDF/X-1a conversion. Toast now shows `converting to PDF/X-1a (12/100) · 18s` ticking in real time instead of a static "converting" string.
- **Backend `pdfx_converter.py`** rewritten to use `asyncio.create_subprocess_exec` and stream Ghostscript's stdout. Drops `-dQUIET`; parses `Page N` lines; calls `progress_cb(f"converting to PDF/X-1a ({n}/{total})")` after each tick. Both stdout and stderr are drained concurrently so the subprocess pipe buffer never fills.
- Async-native 10-minute `asyncio.wait_for` ceiling. On timeout the gs subprocess is `kill()`-ed cleanly (no zombies) and the user sees the friendly "conversion timed out — try a smaller range" message.
- **Backend worker** reads the page count out of the freshly-rendered RGB PDF with `pypdf` and threads it into `convert_to_pdfx(total_pages=…)` so the progress fraction has a denominator.
- **Mongo writes coalesced** by the existing `_on_stage` dedup — each page tick is a distinct string and writes are fire-and-forget Motor ops; load is ~1.5/sec on a 100-page book.
- **Frontend** required zero changes — the existing poll loop already displays `${stage} · ${elapsed}s` in the toast.
- Verified: 4-page book streams "(1/4)" → "(2/4)" → "ready" through the poll endpoint.

## What's been implemented (2026-05-31 / iteration 27 — Fix: PDF/X-1a export timeout)
- **Bug**: large books (100+ pages) failed PDF/X-1a export with `"Export failed: Command ['gs', '-dPDFX', '-dBATCH', …"`. That string is `subprocess.TimeoutExpired.__str__()` — Ghostscript hit the 180s ceiling.
- **Fixes in `pdfx_converter.py`**:
  - Bump subprocess timeout 180s → **600s** (10 min).
  - Switch `-dPDFSETTINGS=/prepress` → `/printer` — still PDF/X-1a conformant with `-dPDFX`, but ~40–60% faster on large colour books.
  - Add `-dQUIET` so per-page stderr chatter no longer risks pipe backpressure stalling the subprocess.
  - Add `-dNumRenderingThreads=2` to use both cores when available.
  - Catch `TimeoutExpired` explicitly and surface "PDF/X-1a conversion timed out (>10 min) — try a smaller page range or disable Print-ready" instead of the raw command-list dump.
- Verified end-to-end: 4-page PDF/X-1a export still takes ~16s and emits PDF 1.3 + `/GTS_PDFX` + `/DeviceCMYK` with `/DeviceRGB` absent.

## What's been implemented (2026-05-31 / iteration 26 — Ink-coverage warning overlay)
- New **"Ink"** toggle in the editor toolbar (next to the Single/Spread switch). When on, any block whose estimated CMYK total ink exceeds 240% gets a red ring overlay — the threshold most commercial printers (IngramSpark, KDP, Lulu) reject above.
- Text blocks: synchronous compute from `block.color` (and the page background that bleeds behind it) via the standard RGB→CMYK formula in `frontend/src/lib/inkCoverage.js`.
- Image blocks: load asset into a 64×64 offscreen canvas, sample every pixel, compute average CMYK total. Result cached per URL so we sample each image once for the session.
- Toggle state persists in `localStorage('bindery_ink_warnings')`. Off by default — zero cost when disabled.
- `CanvasBlockWithInkWarning` wrapper isolates the hook lifecycle per block so the cost scales with visible blocks, not the whole book.
- Verified end-to-end: pure black `#000000` (100% K only) → no warning ✓; navy `#000080` (249.8%) → red ring ✓; toggle OFF removes the class cleanly ✓.

## What's been implemented (2026-05-31 / iteration 25 — Fix: PDF export 401)
- **Bug**: PDF export from the Editor failed with `"Not authenticated"`. Cause: the three PDF-job calls (start, poll, download) used raw `fetch()` which bypasses the axios interceptor that injects the JWT Bearer token. The global `/api/*` AuthGuard then rejected the requests.
- **Fix**: read `localStorage('bindery_token')` once at the top of `onExportPdf` and merge an `Authorization: Bearer <token>` header into all three fetch calls.
- **Also fixed**: undefined-variable bug (`rangeLabel` left over from the cover-spread refactor) that would have thrown a `ReferenceError` inside the poll loop when reporting progress.
- Verified end-to-end via screenshot — toast reads "PDF exported in 10.8s".

## What's been implemented (2026-05-31 / iteration 24 — Print-ready cover spread)
- **Cover spread export**: new "Print-ready cover" section in the Export popover. One click produces a single wide PDF in IngramSpark layout — `BACK | SPINE | FRONT` with 0.125" bleed on every outside edge. Filename suffix `_cov.pdf` (or `_cov_pdfx.pdf` with PDF/X-1a). Spine width auto-computes from `(page_count − 2) × 0.002252` inches (IngramSpark white-paper caliper) or accepts a manual override per printer.
- **New `build_cover_spread_pdf` in `pdf_builder.py`** — reuses `_render_page` so the cover artwork is byte-identical to the editor. Spine background harmonises with the front cover's `background_color`. Bleed bands tint outside-edge area in spine colour for clean trim.
- **API**: `POST /api/books/{id}/pdf-jobs` accepts `{cover_spread: bool, spine_width_in: float}`. Combines with `pdfx` for the full PDF/X-1a cover deliverable.
- **Verified**: Square 8.33" book + 0.25" spine → 17.17" × 8.58" final spread (= 2 × 8.33" trim + 0.25" spine + 0.125" bleed each side). PDF/X-1a markers present when combined with `pdfx=true`.
- **Test**: `test_cover_spread_export_produces_single_wide_page_with_bleed` asserts single-page, trim+bleed math, and filename suffix. 89/89 backend tests pass.

### IngramSpark print-guide compliance status
| Requirement | Status |
|---|---|
| PDF/X-1a:2001 covers | ✅ |
| CMYK / no /DeviceRGB / no /sRGB | ✅ |
| All fonts embedded | ✅ |
| Cover bleed 0.125" outside edges | ✅ NEW |
| Cover layout BACK ∣ SPINE ∣ FRONT | ✅ NEW |
| 300 ppi cap | ✅ |
| Single-page interior PDFs | ✅ |
| No crop / reg marks | ✅ |
| `_cov.pdf` naming | ✅ |
| **Interior bleed 0.125" on top/bottom/outside** | ❌ **Gap** — interior still renders at trim size |
| Total ink ≤ 240% | 🟡 effectively yes via default CMYK ICC, not enforced |

## What's been implemented (2026-05-31 / iteration 23 — Print-ready PDF/X-1a:2001 export)
- **New toggle in the Export popover**: "Print-ready (PDF/X-1a:2001)". When on, the rendered RGB PDF is post-processed through Ghostscript producing a fully PDF/X-1a:2001-compliant file: PDF 1.3, all colour converted to CMYK, RGB / sRGB profiles stripped, all fonts embedded & subsetted, transparency flattened, SWOP v2 OutputIntent baked in, /GTS_PDFX conformance marker present.
- **Backend** — new `pdfx_converter.py` module wraps `gs -dPDFX … -sColorConversionStrategy=CMYK -sOutputICCProfile=…/default_cmyk.icc` plus a generated `PDFX_def.ps` that defines the OutputIntent dictionary. Best-effort auto-install of ghostscript on first request (production-safe) — clean error surface if unavailable. Filename gets a `_pdfx` suffix so print-ready exports are unmistakable in Finder.
- **API contract**: `POST /api/books/{id}/pdf-jobs` now accepts `{"pdfx": true}`; works in combination with the existing range slicing.
- **Progress reporting**: the worker emits a `"converting to PDF/X-1a"` stage update so the export toast tells the user where time is going.
- **Tests**: new `test_pdfx_export_produces_pdfx1a_compliant_file` validates filename suffix, PDF version (1.3), and presence of /GTS_PDFX, /OutputIntent, /DeviceCMYK + absence of /DeviceRGB. 88/88 backend tests pass.

## What's been implemented (2026-05-30 / iteration 22 — clickable TOC rows)
- TOC rows are now **clickable hyperlinks**. Each row is emitted with `data-toc-target="<page-index>"` and `cursor: pointer`. `CanvasBlock` intercepts mouseup / touchend on `[data-toc-target]` and routes to `onTocJump(idx)` → `setActivePageIndex(idx)` — only when the block is NOT in edit mode (clicks inside an editing TOC still position the caret normally).
- Works on mouse and iPad touch. The sidebar thumbnails also render the data attribute but the thumbnail click is intercepted by its parent button — no behaviour conflict.
- Sanitiser `ALLOWED_ATTR` whitelist extended to include `data-toc-target` so DOMPurify doesn't strip it on save/load.
- Verified by a 3-chapter book test: clicked "Chapter 2" row → canvas jumped to page 2, sidebar thumbnail 2 became active, page header switched to "PAGE 2".

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

## What's been implemented (2026-06-16 / iteration 37 — IngramSpark v5.11.26 Phase 2)
- **Phase 2** (quality / spec adherence) — all four planned items shipped:
- **2.1 Pure-K text override** (`pdfx_converter.py`): added `-dBlackText=true -dBlackVector=true` to the Ghostscript invocation so RGB(0,0,0) text and vector black render as DeviceCMYK 0/0/0/100 instead of "rich" composite black via the SWOP profile. Images still go through the full ICC pipeline.
- **2.2 240% TAC ceiling** (`preflight.py`): walk every text-block colour and page-background and surface any element whose CMYK total ink exceeds 240% as an `ink_coverage` warning row. Image ink coverage stays on the client-side overlay (canvas-sampling on the server would require rendering each image — too expensive for a preflight check).
- **2.3 ISBN-based file naming**: added `isbn` field to the `Book` model. Valid 10/13-digit ISBNs (hyphens stripped) drive IngramSpark naming convention — `{isbn}_txt.pdf` for interior, `{isbn}_cvr.pdf` for cover, `_txt_pdfx.pdf` for Print-ready. Blank/invalid ISBNs fall back to title-slug naming (e.g. `My_Book_pdfx.pdf`). New ISBN input lives in the toolbar (visible on xl+ screens).
- **2.4 Editor-side compliance panel** (`CompliancePanel.jsx`): new shield-icon pill in the toolbar that polls `/api/books/{id}/preflight` on every book save. Colour reflects worst-case severity (green Print-ready / amber N warnings / red N errors). Popover panel lists each row with actionable detail per page + block + DPI + TAC%. Manual refresh button included. Visible on lg+ screens.
- **6 new test cases** in `tests/test_ingramspark_phase2.py`. 37/37 PDF + preflight tests pass (Phase 1 + Phase 2 combined).
- **Not yet shipped** (Phase 3): casebound cover spread, ICC profile picker, PDF document metadata, starter-pack templates.

## What's been implemented (2026-06-09 / iteration 36 — IngramSpark v5.11.26 Phase 1)
- **Spec-compliance Phase 1 (3 critical items)** addressing items that would cause IngramSpark to *reject* a submission today. Evaluated against the user-supplied Learning Smart agent prompt (IngramSpark v5.11.26).
- **1.1 Canonical box scheme** (`pdf_builder.py:apply_print_boxes`): every interior page now has the IngramSpark-required boxes — MediaBox `[0,0,630,630]`, BleedBox = MediaBox, TrimBox alternates per parity (`[0,9,621,621]` recto / spine-left, `[9,9,630,621]` verso / spine-right). Implemented as a pypdf post-process so it's independent of Chromium's rendering. Chromium's @page now emits a SYMMETRIC 630×630 MediaBox (previously asymmetric 621×630). The 9-pt strip on the spine side is binding gutter, filled with the page background so the bind cuts through colour.
- **1.2 Even page count enforcement** (`pdf_builder.py:ensure_even_page_count`): full-book exports auto-append a verso-parity blank if the page count is odd. Partial-range exports (proofing) keep their exact count. A `padded to even page count` progress tick fires so the user sees what happened.
- **1.3 Image DPI preflight** (new `preflight.py` module + `/api/books/{id}/preflight` endpoint): walks the book and reports `errors` / `warnings` / `passed` lists. Computes effective DPI per image block from stored source dimensions (`width_px`/`height_px` now captured in `db.files` on upload). Flags any image rendering below 300 DPI at its current trim size. Cheap enough to call on every selection change for a live editor compliance panel.
- **Tests**: 6 new cases in `tests/test_ingramspark_phase1.py` covering all three items. 31/31 PDF-related tests pass.
- **Not yet shipped** (Phase 2 / Phase 3): pure-K text override in Ghostscript, 240% ink ceiling in PDF, ISBN naming, casebound cover spread, output-intent ICC picker, document metadata stamping. Editor-side compliance panel UI also TBD.

## What's been implemented (2026-06-09 / iteration 35 — Retroactive cascade for legacy/imported text)
- **Bug**: First implementation of the text-preset cascade only updated blocks that already had a `text_role` field. Books created before the feature shipped — and books built via the `.docx/.txt` manuscript importer — had blank `text_role` on every text block, so the "Save as default Page text" action saved the preset but didn't visibly change anything. User's exact report: editing page 20 of a 50-page book should sync the other 49 pages, but didn't.
- **Fix (frontend)**: `saveBlockAsPreset` now detects legacy untagged blocks by EXACT style-match against the role's previous canonical style (book preset if set, otherwise built-in `TEXT_PRESETS` default for that role). Matching blocks are tagged with the role AND updated in lockstep. One-off blocks styled deliberately differently (italic quote, footnote, etc.) don't match the canonical style and stay untouched. The toast surfaces the auto-detected count, e.g. *"applied to 49 other Page text blocks (49 auto-detected)"*.
- **Fix (backend importer)**: `book_importer.py` now tags the three block kinds it emits with appropriate text roles — `title` for cover titles, `title` for chapter headings (so the heading cascade works), `body` for body text. Imported books cascade from day one without needing the retroactive matcher.
- **Verified** end-to-end on a synthetic 5-page legacy book (no `text_role` on any block) — after clicking Save as default Page text, the backend confirms all 5 body blocks now have `text_role: 'body'` and the preset is stored. Toast correctly reports the auto-detected count.

## What's been implemented (2026-06-09 / iteration 34 — Text preset cascade)
- **Feature**: When the user clicks **Save as default → Title / Subtitle / Page text** on a selected text block, the new style now cascades to every other text block of the same type across the book. Previously the preset was saved but existing blocks kept their old style.
- **Data model**: added `text_role: Optional[str]` to `Block` in `backend/server.py`. New text blocks created via the toolbar's Text presets, and the Design Cover action (title + author italic), are tagged with their role at insertion time. Chapter headings remain untagged so they're never caught by the cascade.
- **Frontend cascade**: `saveBlockAsPreset(role)` now (a) tags the selected block with `text_role: role` idempotently, (b) walks every page and updates `font_family / font_size / text_align / color` on every block whose `text_role` matches, then (c) persists the book immediately. Toast shows the cascade count, e.g. *"Saved as default 'Title' for this book · applied to 3 other Title blocks"*.
- **Backwards-compat**: blocks pre-dating this feature have no `text_role` so they're invisible to the cascade — they can be opted in by clicking on them and re-running Save as default (which tags them going forward).
- Verified end-to-end via Playwright: a book with three pages each containing a title block had its preset updated; backend roundtrip confirmed all three matching titles updated in lockstep while body blocks remained untouched.
- **Lint cleanup** done as part of this work — fixed three empty-`catch` blocks in `Editor.jsx` so the platform linter only reports four pre-existing React Compiler warnings on legitimate setState-in-effect patterns (full refactor of this 2400-line file remains queued).

## What's been implemented (2026-05-31 / iteration 33 — Chromium prep lock deadlock fix)
- **Bug (production)**: User's `/api/pdf-health` revealed a job stuck at `stage: "preparing chromium"` with `stage_at` only 22 ms after `created_at` — i.e. the worker emitted the stage, hit `ensure_chromium_installed()`, and **never returned**. `chromium_launchable: true` on the answering pod confirmed multi-pod roulette: one pod healthy, another silently hung.
- **Root cause**: `ensure_chromium_installed()` acquired `_chromium_lock` (process-wide async lock) and ran `_try_launch_chromium()` + `_run_playwright_install()` with **no timeouts**. A hung launch probe (dev/shm exhaustion, missing libs) or a stalled Playwright CDN download would hold the lock **forever**. The startup background task that holds the lock also has no timeout, so it can freeze every PDF job that lands on that pod for the lifetime of the container.
- **Fixes in `pdf_builder.py`**:
  - `_try_launch_chromium()` wrapped in `asyncio.wait_for(..., timeout=30)`. A hung browser launch can no longer block.
  - `_run_playwright_install()` wrapped in `asyncio.wait_for(communicate(), timeout=300)`. A stalled 200 MB CDN download now kills the subprocess after 5 min and raises a clear error instead of holding the lock indefinitely.
  - `ensure_chromium_installed()` split into a thin outer wrapper + inner. Outer wraps the whole operation (including lock acquisition) in `asyncio.wait_for(..., timeout=360)`. Worst case the worker's `preparing chromium` stage fails after 6 min with `"Chromium preparation timed out (>6 min)"` — visible in `recent_failures` on `/pdf-health` — instead of sitting in `pending` until the TTL purges it.
- 17/17 PDF-job + cancel tests pass after the refactor.

## What's been implemented (2026-05-31 / iteration 32 — Defensive fixes for silent PDF/X-1a hangs)
- **Bug (production)**: User reported a 22-page Print-ready export visibly reached "chunk 4/5" then the progress bar disappeared and only the duration counter ticked for 5–6 minutes — no final "uploading" stage, no error toast, no completion. Job sat in `pending` indefinitely with no obvious diagnostic.
- **Root cause #1 — Ghostscript stdout block-buffering**: When `gs` writes to a pipe (not a TTY), libc default-buffers stdout in 4–64KB blocks. Our `_drain_stdout` `readline()` loop saw nothing until the buffer flushed — which on small books can be the entire conversion. The toast went silent for the full Ghostscript run.
- **Root cause #2 — no heartbeat during long phases**: If Ghostscript hit a slow PDF-parse / ICC-profile-load phase before its first `Page N` line, no progress callback fired for many seconds. Toast looked frozen even when the worker was healthy.
- **Root cause #3 — no upload timeout**: `put_object` (Emergent object-storage SDK) had no wall-clock guard. A network blip or stalled upstream connection could leave the worker hanging forever — job would never flip to `failed`, never resolve. This is the most likely explanation for the user's "never saw uploading" report.
- **Fixes in `pdfx_converter.py`**:
  - Wrap the `gs` command with `stdbuf -oL` (coreutils, available in every Debian/Ubuntu image) — forces Ghostscript stdout to line-buffer mode so `Page N` ticks arrive in real time instead of in an end-of-process burst.
  - Added a `_heartbeat` task running alongside the drainers. Every 2 seconds, if nothing else has emitted a tick in the last 2.5s, it fires `progress_cb` with either `converting to PDF/X-1a (P/N) · Xs` (preserving the last known fraction) or `converting to PDF/X-1a · working Xs`. Toast can never go silent while the subprocess is alive.
- **Fix in `server.py`**: Wrapped the `put_object` upload in `asyncio.wait_for(..., timeout=120)`. A stalled storage upload now flips the job to `failed` after 2 minutes with the message "PDF upload to object storage timed out after 2 min" instead of hanging forever in `pending`.
- **Verified** on preview with a full Print-ready export — `stdbuf` is present at `/usr/bin/stdbuf`, first `(1/4)` tick arrived within 4s of submit, bar visibly held at `4/4` through the upload phase. All 17 PDF-job + cancel tests pass.

## What's been implemented (2026-05-31 / iteration 31 — Progress bar stays visible between phases)
- **Bug (production)**: The progress bar would visibly "disappear" between phases — e.g. after "rendering chunk 4/5" the next stage ("merging chunks", "uploading", or Ghostscript warming up before its first page tick) emits a stage string with no numeric fraction, which collapsed the bar to a 30%-wide indeterminate sweep that users couldn't see well. The duration counter ticked alone for several seconds before the next phase's fraction kicked in.
- **Fix in `Editor.onExportPdf`**: persist the last seen `{done, total}` across poll iterations. When the current stage has no fraction (e.g. "merging chunks", "uploading", "converting to PDF/X-1a" pre-tick), the toast now keeps the bar pinned at the previous phase's 100% so progress looks continuous. As soon as the next phase emits its first numeric tick, the bar resets to that phase's denominator.
- Verified on the preview environment by sampling the toast once per second through a full Print-ready export — `[BAR]` (determinate) mode held continuously from "rendering chunk 1/1" → "converting to PDF/X-1a (1/4)" → "(2/4)" → …, no indeterminate sweep mid-export.

## What's been implemented (2026-05-31 / iteration 30 — PDF export Cancel)
- **Cancel button on the export toast.** The progress toast (`PdfExportToast`) now wires its X button to a backend cancellation flow so users can abort runaway exports instead of waiting 10 minutes.
- **Backend**: `POST /api/books/{book_id}/pdf-jobs/{job_id}/cancel` marks the job's `cancel_requested` flag. The worker calls `_check_cancelled()` at every coarse checkpoint (between chunks, before/after Ghostscript), raising an internal `_Cancelled` exception that flips the job to `failed` with `error="Cancelled by user"` and frees resources immediately. The status endpoint surfaces `cancel_requested: true` so the frontend can stop polling without waiting for the worker to notice.
- **Frontend**: `Editor.onExportPdf` shares a `cancelState = { cancelled, jobId }` between the cancel handler (inside the custom toast) and the poll loop. Cancel triggers `toast.dismiss` + "Cancelling export…" then fires the backend cancel; the poll loop bails at its next checkpoint. Catch block special-cases the cancellation message and shows a calm "Export cancelled" toast instead of the red error toast.
- **Race fix**: added a `cancelState.cancelled` guard right before the toast.custom redraw in the poll loop so a late status fetch can never overwrite the "Cancelling…" message.
- **Tests**: new `tests/test_pdf_cancel.py` (3 cases) verifies 404 on unknown job, cancel flips a pending job to failed with the clean reason, and cancelling a finished job is a 200 no-op. 17/17 PDF tests pass.

## What's been implemented (2026-06-16 / iteration 39 — Phase 3.1 Casebound hardcover spread)
- **Feature**: The cover-spread exporter now supports two binding modes, surfaced as a Perfect-bound / Casebound hardcover toggle in the export popover.
  - **Perfect-bound** (unchanged default): 0.125" bleed on every outside edge; spine = `interior_pages × paper_caliper`.
  - **Casebound hardcover**: 0.625" wrap (turn-in) on every outside edge — replaces the bleed — and the spine widens by `CASEBOUND_SPINE_ALLOWANCE_IN = 0.125"` to account for the spine board + hinge gap. Matches the cover-file geometry IngramSpark casebound POD jobs expect.
- **Backend** (`pdf_builder.py`): `_build_cover_spread_html` and `build_cover_spread_pdf` accept `binding: str`. Constants `CASEBOUND_WRAP_PX = 60` and `CASEBOUND_SPINE_ALLOWANCE_IN = 0.125` codify the spec.
- **Backend** (`server.py`): `PdfJobStartRequest.binding` (defaults to `"perfect"`, normalised + validated; unknown values fall back). Stored on the `pdf_jobs` doc for traceability.
- **Frontend** (`ExportPopover.jsx`): pill-style radio group (`data-testid="binding-perfect"` / `binding-casebound"`) with explanatory copy that flips between bleed/wrap context. Last choice persisted in `localStorage["bindery_binding"]`. Editor.jsx pipes `binding` into the request body only when casebound is selected.
- **Tests** (`/app/backend/tests/test_ingramspark_phase3.py`): 10 cases — 5 unit (pixel-exact geometry) + 3 API (binding persisted, default stays perfect, invalid binding normalised) + 2 parametric. All 22 IngramSpark tests pass (Phase 1+2+3).
- **Smoke-tested** end-to-end in preview: popover toggles cleanly between perfect/casebound, help text updates, casebound renders a wider PDF.

## What's been implemented (2026-06-16 / iteration 40 — On-canvas Cover Spread Preview)
- **Feature**: New **Cover** view-mode in the editor (`data-testid="view-mode-cover"`, `LayoutTemplate` icon) — sits next to Single/Spread. Renders a read-only BACK · SPINE · FRONT layout at the exact backend pixel dimensions, scaled to fit the viewport.
- Overlay markers (SVG):
  - Trim rectangle (solid terracotta) — doubles as the wrap-fold line on casebound
  - 0.5" safe-margin rectangle (dashed green)
  - Spine fold lines (dotted ink) flanking the spine band
  - Casebound-only: board outline (dashed terracotta, inset 0.083" — helps spot text the wrap will hide)
- Inline binding toolbar inside the preview lets the user flip Perfect-bound ↔ Casebound and override the spine width without leaving the canvas. Changes write to the same `localStorage` keys the ExportPopover uses.
- `ExportPopover` now re-syncs its binding + spine width from `localStorage` every time the popover opens, so changes made in the preview are reflected immediately.
- Spec chip (top) shows live geometry: `Trim 17.16″ × 8.50″ · Spine 0.157″ · Wrap 0.625″`. Legend (bottom) explains each overlay.
- New file: `/app/frontend/src/components/CoverSpreadPreview.jsx`. Geometry constants mirror `pdf_builder.py` exactly (`COVER_BLEED_IN`, `CASEBOUND_WRAP_IN = 0.625`, `CASEBOUND_SPINE_ALLOWANCE_IN = 0.125`).
- Smoke-tested end-to-end in preview: toggling binding in the Cover view immediately updates trim/wrap/spine geometry, board outline appears for casebound, and the ExportPopover picks up the same selection on next open.

## What's been implemented (2026-06-17 / iteration 41 — High-DPI image export ceiling)
- **Feature**: New **Image quality** picker in the export popover (`data-testid="dpi-section"`): Standard 300 DPI · High 450 DPI · Maximum 600 DPI. Persisted in `localStorage["bindery_dpi"]` and re-synced on every popover open.
- **Default stays 300 DPI** — production-safe for everyday exports. 450/600 are opt-in for print masters.
- **Backend** (`pdf_builder.py`): `build_book_pdf` and `build_cover_spread_pdf` accept `dpi: int`. New `DPI_LONG_EDGE_CAPS = {300: 3300, 450: 4950, 600: 6600}` table + `_resolve_dpi()` helper that snaps unknown values to 300. JPEG re-encode quality also bumps from 85 → 92 for High/Maximum so the extra pixels actually carry detail.
- **Backend** (`server.py`): `PdfJobStartRequest.dpi` (validated → 300 default). Stored on the `pdf_jobs` document and surfaced on the status endpoint so the client + history view can confirm what was rendered.
- **Frontend** (`Editor.jsx`): pipes `dpi` through the API body only when non-default (300 stays implicit so existing clients keep working).
- **Tests**: 18 fast tests in `/app/backend/tests/test_export_dpi.py` (resolver + persistence) + 1 end-to-end High-DPI render. All 23 PDF tests (IngramSpark + DPI) pass.
- **On-screen**: already full-resolution — uploaded artwork is stored at original pixel dimensions and displayed as-is in the canvas. No change needed.

## What's been implemented (2026-06-18 / iteration 42 — Image-block background fill picker)
- **Feature**: Image blocks now support a **Background fill** colour, exposed in the Block properties panel with three input affordances:
  - 4×4 swatch grid (pastels + editorial darks tuned for kids' books)
  - Native browser colour wheel (`<input type="color">`) for any RGB value
  - Plain-text hex field for paste-from-Figma workflows (accepts both `#FFE9C8` and `FFE9C8`)
- **Behaviour**: The fill is rendered behind the image so it shows through transparent PNG regions (e.g. a hand-drawn illustration over a coloured backdrop). When the block has no image yet, the colour fills the whole block — turning it into a pure colour-tile (handy as an accent panel behind text).
- **Backend** (`Block` model): new optional `background_color: str | None`. Round-trips via the existing `PUT /books/{id}` save path; no schema migration needed.
- **Backend** (`pdf_builder.py`): `_render_block` emits `background-color:` on the wrapper div for image blocks. Renders the wrapper even when no image source resolves so colour-tile mode works end-to-end.
- **Frontend**: `CanvasBlock.jsx` paints the colour beneath the `<img>`; the "No image" placeholder is replaced by the solid colour when a background is set. `CoverSpreadPreview.jsx` mirrors the same rule.
- **Trigger affordance**: a checkerboard swatch icon signals "no fill" (transparent). A small "Clear" link in the section header removes the fill.
- **Tests**: 5 new tests in `/app/backend/tests/test_image_background_color.py` (render + persistence). All pass.
- **Smoke-tested** in preview: applied terracotta to the cover image block → colour visibly bled through the transparent PNG bottom strip.

## What's been implemented (2026-06-18 / iteration 43 — Rich text-colour picker with eyedropper)
- **Feature**: Replaced the basic 8-swatch text colour picker with a rich four-affordance picker (matches the image-bg picker pattern):
  - **Eyedropper** button at the top — opens the native `window.EyeDropper` so the user can sample any pixel on the page, **including pixels inside placed illustrations**. The sampled hex is applied to the current text block instantly.
  - 4×4 swatch grid (editorial palette tuned for cream-paper backgrounds).
  - Native `<input type="color">` colour wheel for any RGB.
  - Plain-text **Hex** field (accepts `#FFE9C8` or `FFE9C8`, normalises to uppercase, Enter to apply, Esc/blur to revert if invalid).
- **Graceful degradation**: Eyedropper button is **only rendered on browsers that support `window.EyeDropper`** (Chrome 95+, Edge 95+, Opera). Safari/Firefox users see a one-line tip recommending Chrome/Edge for the eyedropper.
- **Eyedropper added to image-background picker too** for consistency — designers can sample a brand colour off a photograph and re-use it as an accent fill.
- **Files**: `/app/frontend/src/components/BlockProperties.jsx` (text ColorPicker + ImageBackgroundPicker upgrades).
- **Smoke-tested** in preview: opened picker on the cover title → eyedropper button present → typed `AA3344` in the Hex field → text rendered burgundy.

## What's been implemented (2026-06-18 / iteration 44 — PDF timeout-defence + auto-retry)
- **Diagnosed**: production `/api/pdf-health` showed 2 jobs frozen on `rendering chunk 1/N` since 04:28 / 04:34 UTC with healthy infrastructure (24 GB free RAM, Chromium launchable, Ghostscript ready). Root cause: two unbounded waits in `pdf_builder.py` — `get_image` had no `asyncio.wait_for` and `page.pdf()` had no wall-clock timeout. A single slow CDN image fetch (or an internal Chromium hang) could strand the whole job indefinitely.
- **Fix 1 — Per-image fetch timeout** (`_handle_route`): every `get_image` call wrapped in `asyncio.wait_for(..., timeout=20.0)`. On timeout the route is fulfilled with `408` so Chromium renders the page with a broken-image marker and the chunk moves on. Same defence applied to `route.continue_()` / `route.fulfill()` so a stale route at chunk-close time can't crash the worker.
- **Fix 2 — Per-chunk wall-clock guard** (`_render_chunk`): `page.pdf()` now wrapped in `asyncio.wait_for(..., timeout=90.0)`; `page.set_default_timeout(60_000)` lowers Playwright's per-action default; `context.close()` capped at 10s so a wedged Chromium can't block retry.
- **Fix 3 — Auto-retry with degradation** (`_render_chunk_safe`): if a chunk fails (timeout or exception), retry the full chunk once. If it fails again AND the range > 1 page, fall back to **single-page rendering and pypdf merge** — isolates a single bad page so the rest of the book still exports. A book with one missing page is far more recoverable than a job that never completes.
- **Fix 4 — Background stuck-job sweeper** (`server.py` startup): every 60s, marks `pdf_jobs` with `stage_at` older than 10 min as `failed` with a friendly error. Users see "Export timed out — no progress for 10 minutes." instead of an infinite spinner, even if the worker pod itself dies.
- **Tests**: 4 new tests in `/app/backend/tests/test_pdf_export_timeouts.py` (image-fetch timeout race, sweeper query math, retry subdivision). All 67 PDF tests across the suite pass.

## What's been implemented (2026-06-18 / iteration 45 — Chromium-relaunch-per-attempt + stage diagnostics)
**Recurrence**: even after iteration 44's `wait_for` guards, an 11-page export hung on `rendering chunk 1/3` in production. Sweeper correctly marked it failed at 10 min — but the user still didn't get a PDF.

**Real root cause**: the browser was launched ONCE for all chunks. When Chromium wedges at the **process** level (not page or context level), every chunk retry runs against the dead browser. Closing the context and opening a new one on the same dead process doesn't help.

**Fixes shipped**:
- **Fresh Chromium browser per attempt** — `_render_chunk_safe` now launches a brand-new browser for each of its 2 attempts. ~3s relaunch overhead per attempt; total worst-case per chunk: ~160s.
- **30s timeout on `pw.chromium.launch()`** — catches binary hangs during sandbox setup.
- **5s timeout on `browser.close()`** — wedged browsers can't block teardown.
- **Per-stage timings** captured during render (set_content / fonts / images / page.pdf) and surfaced in the error message: e.g. `"chunk 1/3 failed at stage 'page.pdf' (timings: {set_content: 1.2, fonts: 0.3, images: 4.1}): TimeoutError"`. Designers + agents can now see exactly which step is stalling.
- **15s heartbeat** during chunk renders — pings `_emit(stage)` every 15s so `stage_at` stays fresh; the 10-min sweeper no longer false-fires on legitimately-progressing chunks.
- **Tighter per-attempt timeout**: 80s (down from 150s). With 3 chunks × 2 attempts × 80s = 480s = 8 min — well under the sweeper's 10 min and gives users fast failure feedback.
- **Same guards applied to `build_cover_spread_pdf`** — launch timeout, page.pdf timeout, context.close timeout. Cover-spread exports get the same defence.
- **No more per-page fallback** — the 11×150s = 27 min worst case it added always exceeded the sweeper threshold, so users never benefited. Removed to keep total bounded.

## What's been implemented (2026-06-18 / iteration 46 — WeasyPrint PDF engine)
**Confirmed by `/api/pdf-health`**: even after iteration-45 fixes, jobs hung on `"launching chromium"` for 5+ minutes — `chromium_launchable: true` in isolation but the actual job-launched Chromium never returned. **Root cause**: Playwright's `pw.chromium.launch()` spawns an internal Node subprocess; `asyncio.wait_for(timeout=30)` raises `TimeoutError` on the coroutine but cannot cancel the underlying subprocess. So launches never actually fail — they just leave Python pretending they timed out while the Playwright connection stays locked.
- **Fix**: Shipped a pure-Python WeasyPrint renderer (`pdf_builder_weasy.py`) as the new default engine. No Chromium, no subprocess, no async-cancellation gotchas. Renders the SAME HTML the Chromium pipeline produces via Cairo + Pango.
- **Engine selection**: `PDF_ENGINE=weasy` (default) or `PDF_ENGINE=chromium` env var. `/api/pdf-health` now reports `pdf_engine` + `weasyprint_version` + `weasyprint_ready`.
- **Reuses** the existing HTML builders (`_build_html`, `_build_cover_spread_html`), the same `apply_print_boxes` IngramSpark stamping, the same `ensure_even_page_count` padding, and the same DPI image downscaling. Visual output ≈ identical to Chromium for our static absolutely-positioned block layout.
- **Custom URL fetcher** wires WeasyPrint to the same `get_object` helper Chromium used — images served from in-pod Object Storage, downscaled to the chosen DPI, JPEG re-encoded at the same quality.
- **5-min hard timeout** via `asyncio.wait_for(asyncio.to_thread(write_pdf), 300)` — WeasyPrint never hangs, but the cap is defensive.
- **Tests**: 67/67 PDF tests pass including all IngramSpark Phase 1+2+3, page-range exports, even-page padding, PDF/X conversion, and cancel flows. Tests use the new default engine, proving the Chromium → WeasyPrint swap is invisible at the API level.

## Next Tasks
- Phase 3.3 — PDF document metadata (Title, Author, ISBN, Publisher into PDF properties).
- Phase 3.2 — ICC profile picker (SWOP v2 vs Fogra39) in export popover.
- Phase 3.4 — Starter-pack templates (8.5×8.5 Square / Casebound seeded on first launch).
- Pure-K text strict override (PostScript `defs.ps` colour map — currently blocked).
- Refactor `Editor.jsx` for maintainability (P1 — >2500 lines).
- Drag-to-reorder pages in the sidebar.
