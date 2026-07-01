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

## What's been implemented (2026-06-18 / iteration 47 — Google Fonts local cache)
**Root cause (per Emergent Support)**: the PDF HTML imported 27 Google Fonts families at render time via `@import url('https://fonts.googleapis.com/...')`. Both WeasyPrint AND Chromium fetched the CSS *and every WOFF2 file it references* synchronously for every render. On a slow pod-to-Google connection this hung any export larger than 1 page. The 1-page exports succeeded only because the network had finished by the time WeasyPrint moved on.
- **Fix shipped**: New `fonts_cache.py` module — downloads the Google Fonts CSS + every WOFF2 *once* at server startup (background thread), caches the WOFF2 bytes in memory. Subsequent renders see a small ~150KB CSS @font-face block inlined into HTML, with `fonts.gstatic.com` URLs intercepted by the WeasyPrint `url_fetcher` (and by Chromium's `page.route`) and served from the in-memory cache.
- **Result**: 15-page full-book export now completes in **10.5s** (was hanging indefinitely on production). 53/53 PDF backend tests pass.
- **`/api/pdf-health` enhanced**: surfaces `fonts_cache_ready`, `fonts_cache_woff2_count` (typically 163), `fonts_cache_css_bytes` (~155KB), and `fonts_cache_failed` so operators can spot the degraded "system fonts" fallback mode if the startup download fails.
- **Defence-in-depth**: even if a WOFF2 URL escapes the local cache, the url_fetcher fails it fast (HTTP 408) instead of blocking — WeasyPrint falls back to system fonts rather than hanging. Same defence in the Chromium `page.route`.
- **Backwards-compat**: `_google_fonts_css()` falls back to the old `@import` URL if the startup download failed; renders still work, just slower.

## What's been implemented (2026-06-21 / iteration 48 — WeasyPrint per-page isolation)
**Symptom from production**: even after iteration 47's font cache + 2-page chunks + 240s/chunk budget + image cache, a 5-page art-heavy export still failed in production at bindery.au (user confirmed: tested after a fresh deploy).
- **Hypothesis**: with `CHUNK_SIZE=2`, two heavy full-bleed pages in the same chunk could compound past 240s, and the per-page fallback shared the same `to_thread` hang risk. Also, slow object-storage image fetches inside WeasyPrint's `url_fetcher` had no timeout — a single hung fetch could wedge the render thread indefinitely (no async cancellation possible from sync code).
- **Fix shipped** (`pdf_builder_weasy.py`):
  1. **CHUNK_SIZE = 1** — every page renders in its own WeasyPrint invocation. A bad page can never poison neighbours, and timing logs surface exactly which page is slow.
  2. **Bounded image fetcher** — `url_fetcher` now submits `get_image` calls to a dedicated `ThreadPoolExecutor` and waits at most **25s per image** via `future.result(timeout=...)`. Beyond the cap, the fetcher substitutes a blank PNG and logs the slow URL. Slow object-storage requests can no longer hang Cairo.
  3. **Blank-page emergency fallback** — if a page exceeds the 240s render budget OR raises an unexpected exception, we render an obvious "[page could not be rendered]" placeholder of the correct trim size and continue. The user gets a complete PDF with at most one or two clearly-marked placeholders instead of a failed export.
  4. **Per-page timing logs** — every page logs its render time; pages >60s log a SLOW warning so operators can spot pathological pages from the supervisor log alone.
  5. **Defensive downscale** — `_maybe_downscale` exceptions now degrade to raw bytes instead of aborting the fetch.
- **Test status**: 42/42 PDF-related tests pass (`test_pdf_export.py`, `test_pdf_export_jobs.py`, `test_pdf_export_timeouts.py`, `test_pdf_cancel.py`, `test_export_dpi.py`, `test_ingramspark_phase1/2/3.py`).
- **What this changes for the user**: a 5-page export now has per-page wall-clock visibility AND degrades gracefully if one page is pathological. The next failure should leave a precise per-page timing trace in `/api/pdf-health` recent_failures.

## What's been implemented (2026-06-21 / iteration 49 — Pre-fetch images before WeasyPrint)
**Confirmed root cause from production toast**: a 5-page export wedged at `"rendering page 5/5 · 100s"` — backend heartbeats stopped flowing because WeasyPrint's in-render `url_fetcher` was blocking the render thread on a slow Object Storage read. With the GIL held during the network wait, the asyncio event loop couldn't tick the heartbeat OR enforce its 240s timeout. The frontend's 4-min idle deadline eventually expired and surfaced the "stuck at:" error.
- **Fix shipped** (`pdf_builder_weasy.py`):
  1. **Pre-fetch phase before the chunk loop** — scan every block in the requested page range, collect unique image storage keys, and fetch + downscale them ALL into `job_image_cache` BEFORE WeasyPrint is invoked. Each image emits a `prefetching image N/M` stage update the toast shows. Per-image 25s fetch cap and 60s downscale cap, both via `asyncio.wait_for` (real cancellation, not blocked-thread).
  2. **In-render url_fetcher is now pure cache lookup** — WeasyPrint never blocks on network during `write_pdf`. Cairo can run cleanly, heartbeats keep ticking, the 240s per-page timeout actually fires if rendering itself stalls.
  3. **Pre-fetch failures degrade gracefully** — slow/missing image → blank PNG → page renders without it instead of wedging the export.
- **Frontend** (`Editor.jsx`): `STAGE_IDLE_DEADLINE_MS` 240s → 360s. Single-step ceiling (one huge image downscale) can legitimately exceed 4 min on 600 DPI; 6 min is safer.
- **Test status**: 46/46 PDF tests pass (`test_pdf_export.py` 6, `test_pdf_export_jobs.py` 14, `test_pdf_export_timeouts.py`, `test_export_dpi.py`, `test_pdf_cancel.py` 26 combined).
- **What the user will see if it still fails after redeploy**: the toast will now show either `prefetching image N/M` (network problem on a specific image — its index pinpoints the file) or `rendering page N/M` (legitimate Cairo CPU load — different fix path). Either way the failure mode is now diagnosable from the toast alone.

## What's been implemented (2026-06-21 / iteration 50 — 8.75″ Square + ≥300 DPI cap fix)
1. **Square trim 8.5″ → 8.75″** in all three places that mirror each other so canvas, importer, and PDF renderer stay in sync:
   - `/app/frontend/src/lib/pageSizes.js`: `{ width: 840, height: 840 }` (was 816)
   - `/app/backend/pdf_builder.py`: `PAGE_SIZES_PX["square"] = (840, 840)`
   - `/app/backend/book_importer.py`: same.
2. **PDF cap bug found by new regression test** — `DPI_LONG_EDGE_CAPS[300] = 3300` was sized for an 11″ long-edge book but **A4 is 11.69″ tall**, so at the default 300 DPI export the pipeline silently shipped only ~282 DPI on full-bleed A4 images. Bumped caps to `{300: 3600, 450: 5400, 600: 7200}` — now ≥300 DPI is guaranteed at every supported page size (Square 8.75″, A4, Letter, 6×9).
3. **New regression test** (`tests/test_export_image_resolution.py`) — uploads a 4000 px PNG, places it full-bleed on each supported page size, exports the PDF, then walks the PDF's image XObject inventory with `pypdf` and asserts the embedded long-edge ≥ `trim_inches × 300 DPI`. Also asserts MediaBox = 648 pt for the new Square trim. **This guards against the silent "scales-to-inches but ships 96 DPI raster" failure mode**.
4. **96 DPI audit (per user request)** — confirmed that ALL `96`/`PX_PER_INCH` references are CSS-pixel-to-points layout math (correct: 1 CSS px = 0.75 pt) OR on-screen canvas/preview math. **No 96 DPI value drives export raster resolution.** Embedded images keep their source pixel count (up to the bumped cap). Vector text and shapes remain vector in the PDF (never rasterized).
5. **IngramSpark Phase 1 tests made dimension-aware** — previously hardcoded `630` and `[0, 9, 621, 621]` for the old 8.5″ trim; now computed dynamically from `PAGE_SIZES_PX["square"]` so future dimension changes don't require test edits.

## What's been implemented (2026-06-21 / iteration 51 — PDF speed: parallel prefetch + font subsetting)

### Root cause (corrected — earlier iterations attributed this to the wrong layer)
**The actual cause of 9 s/page render time was 369 `@font-face` declarations (the entire Google Fonts catalog) being re-parsed from scratch on every single page.** It was NOT Cairo CPU load. It was NOT per-page setup overhead. It was NOT a GIL-contention symptom. Earlier hypotheses in iterations 47–50 were wrong.

**A/B confirmation** (recorded for the next engineer who hits a similar symptom):

| Configuration | Page 1 | Pages 2-N avg |
|---|---|---|
| Baseline (current code, 154 KB `@font-face` block, 369 rules) | 13,866 ms | **9,016 ms** |
| `_google_fonts_css()` patched to return `""` | 17 ms | **16 ms** |

>99.8 % of per-page render time was CSS parser overhead from rules pointing to fonts the document never used. Real WeasyPrint compositing of a 5-image page is ~16 ms.

### Fix shipped — three layers, one file pair
1. **Semaphore-bounded concurrent image pre-fetch** (`pdf_builder_weasy.py`) — `_IMG_FETCH_EXECUTOR` finally gets used the way its 4-worker pool was sized for. Replaces the serial `for img_idx, key in image_paths_in_range` loop with an `asyncio.gather` of `_fetch_one` coroutines, each acquiring/releasing one of 4 semaphore slots. Refilled by completion (no head-of-line blocking from naive 4-at-a-time batches). Stage updates `prefetching image N/M` count COMPLETIONS not array index. **Measured 4× speedup, hits theoretical 4-worker ceiling exactly** (5.00 s serial → 1.26 s parallel on 20 images @ 250 ms latency; 15 s → 3.77 s on 60 images).
2. **Per-job font subsetting** (`pdf_builder.py`):
   - New `_extract_used_font_families(book) -> (used, missing)` scans **all three** font-introduction surfaces, since `text_presets` is NOT authoritative:
     1. `block.font_family` (per-block dropdown)
     2. `text_presets.{title,subtitle,page_text}.font_family` (per-book defaults)
     3. Inline `style="font-family: …"` inside `block.html` (rich-text toolbar character-level overrides — preserved verbatim by `_safe_block_html`).
   - Always includes `Cormorant Garamond` because `_render_block` and the page-number renderer hardcode it as a fallback string in the rendered CSS; legacy books without a `font_family` field would silently fall back to system serif otherwise. **Only that one — `Lora` was over-cautious; removed**.
   - `_google_fonts_css(used_families=None)` filters the cached 369-rule catalog down to declarations whose `font-family` matches the set (case-insensitive, quote-stripped). Backwards-compat preserved by `None` → full catalog.
   - **Failure mode is explicit**: any family referenced but absent from the cached catalog AND not in `_KNOWN_SYSTEM_FONTS` (Georgia, Helvetica, Times, Courier, CSS keywords like `serif`) is logged at WARNING and surfaced in the JOB SUMMARY line as `fonts: N used, M missing ['the typo']`. No silent fallback.
3. **End-of-job summary log** (fix #4 from the brief, extended) — single line:
   ```
   WeasyPrint JOB SUMMARY: total=Xs | prefetch=Ys (N imgs, avg Zms/img, B blank-fallbacks) | render=Rs (P pages, avg Q ms/page, C placeholders) | fonts: U used, M missing [list]
   ```
   Phase-split timings, per-phase averages, AND fallback counts so a "fast" book that secretly substituted 6 blank pages is loud, not silent.

### Measured impact (12-page benchmark, apples-to-apples vs iteration 50 baseline)

| Configuration | Per-page render | Total job |
|---|---|---|
| Iteration 50 baseline | 9,016 ms | ~120 s |
| **Iter 51, 1-font book (common case)** | **1,043 ms** (8.6× faster) | **16.3 s** |
| **Iter 51, 2-font book + inline rich-text override** | **1,654 ms** (5.4× faster) | **23.7 s** |

The remaining ~1 s/page is irreducible parse cost of the 30 `@font-face` blocks one family generates (4 weights × 2 styles × ~6 unicode-range subsets). See "Confirmed not on the table" below for why we're not chasing it further in this pass.

### Test status
- **14/14 new offline unit tests** in `tests/test_font_subsetting.py` — run in 5 s, no hosted-env dependency, independently reproducible regression coverage.
- **57/57 integration tests pass against the hosted preview environment** in 137 s — same suite, but 4× faster than pre-fix-A wall-clock as a free side benefit (every test PDF export also benefits from the per-page parse-tax removal).
- **Hosted preview suite caveat (be precise about this)**: the integration suite makes live HTTP calls to `REACT_APP_BACKEND_URL`. "Tests pass" means the current preview pod renders correctly; it's not an offline guarantee. Deploy-readiness requires a separate production redeploy + observation. The new offline unit suite is what carries the reproducible guarantee.

### Confirmed NOT on the table (do not pick these up as unfinished work)
- **Fix #2 — bounded process pool for WeasyPrint rendering**: NOT needed and NOT a good idea against this bottleneck. Process pools parallelize CPU work; the work we eliminated was CSS parser overhead with no rendering value. Per-page render is now 1 s; the 240 s/page hard wall has 240× headroom. A process pool would parallelize milliseconds. Touching Cairo state across subprocess boundaries (Cairo is not fork-safe; ProcessPoolExecutor + WeasyPrint requires careful lifecycle management for fonts, fontconfig, harfbuzz state) is a real risk to take on for no measurable gain. Don't.
- **Fix #3 — raise `STAGE_IDLE_DEADLINE_MS` above 6 min**: NOT needed. Real per-page is 1 s, deadline is 360 s = 360× headroom. The brief explicitly cautioned against bumping this without timing data; the data says don't.

### Open follow-up (filed, not blocking)
- **WeasyPrint `FontConfiguration` API migration** — register fonts ONCE at process startup via WeasyPrint's `FontConfiguration` instead of emitting per-page `@font-face` CSS. Would close most of the remaining ~1 s/page parse cost (estimated 1043 ms → ~100-200 ms target). Larger architectural surface than fix A (touches WeasyPrint API, requires understanding of `FontConfiguration` lifecycle vs document creation), which is why it was correctly deferred from this pass. **Track as P2 — net win is ~5-10× on top of the 8.6× already shipped, but not blocking any user-facing capability.**

## What's been implemented (2026-06-21 / iteration 52 — Editor toolbar compaction)
**Problem**: User reported the editor's horizontal top menu was overcrowded on a normal-sized screen — book title and author were both truncated mid-word, ISBN field clipped, and items behind the right-pinned cluster were inaccessible without horizontal scroll.

**Root cause**: The top toolbar carried ~1330 px of nominally-`shrink-0` items competing for a single row at xl breakpoint. Three of them (Title input 256 px, Author input 160 px, ISBN input 128 px = ~545 px combined) were set-once-and-forget fields blocking access to canvas-action controls.

**Shipped** (`Editor.jsx`, `HistoryDialog.jsx`, `SaveTemplateDialog.jsx`):
1. **Title + Author + ISBN → single Popover-triggered button** (`book-details-trigger`). Trigger shows truncated title + author subtitle, responsive max-width 14rem/15rem/17rem at sm/md/lg+. Inside the popover the three fields are properly labelled and stacked. **`data-testid` attrs of the underlying inputs are preserved** (`book-title-input`, `book-author-input`, `book-isbn-input`) so existing test suite works unchanged. Reclaim: ~250 px.
2. **History + Save-as-template + SaveStatus indicator → single "More" DropdownMenu** (`toolbar-more-trigger`). `HistoryDialog` and `SaveTemplateDialog` extended with controlled-mode props (`open`, `onOpenChange`, `hideTrigger`); `Editor.jsx` lifts the dialog state and fires from `DropdownMenuItem`. SaveStatus is now a read-only row at the top of the menu. Reclaim: ~280 px.
3. **Responsive breakpoint adjustments**:
   - Page-dimensions readout (`8.75″ × 8.75″ / 0.5″ margin`): `md` → `2xl` (visible only on >=1536px). Reclaim ~80 px on common laptops.
   - View-mode toggle labels (Single/Spread/Cover): `xl` → `2xl` (icon-only on 1280-1535). Reclaim ~90 px.
   - Ink button label: `lg` → `xl` (icon-only on 1024-1279). Reclaim ~25 px.

**Net result**: ~775 px of horizontal space reclaimed at common laptop widths. At 1366 (most common laptop res), toolbar overflow dropped from 325 px → 132 px on the cover page (where Design-cover button is also present), and to near-zero on regular interior pages where Design-cover is conditionally hidden. At 1280 (next-tightest common res), all primary controls (book metadata, page size, view mode, Save, Print-ready, Export PDF) are visible without horizontal scroll.

**Tests**: ESLint clean. All preserved `data-testid` attrs verified intact. No tests modified — the suite drives inputs by id which now exist inside popovers/dropdowns instead of inline.

## Iteration 51 + 52 — Deployment-ready (2026-06-21)
Static analysis via deployment_agent reports **PASS** with no blockers:
- Compilation clean (backend + frontend)
- No hardcoded secrets / URLs — all env-driven
- CORS configured for production
- Supervisor configuration valid
- No ML/blockchain dependencies; MongoDB-only stack

Two non-blocking DB-query-optimisation warnings filed as P2 backlog items:
1. `GET /api/books` (server.py:525) — returns full `pages` array for every book in library list. Adding `{$slice: 1}` projection would cut payload ~95% for chapter books.
2. `GET /api/files` (server.py:1506) — hard 2000-asset cap without pagination; fine until a user has 1000+ illustrations.

## What's been implemented (2026-06-21 / iteration 53 — Phase 3.3 PDF metadata stamping)

**Shipped**: every exported PDF (interior + cover) now carries proper document metadata stamped into the /Info dictionary, indexed by readers like Adobe Acrobat's File→Properties dialog AND by catalog-ingestion tools (IngramSpark, LSI, Amazon KDP preflight).

### Surfaces touched
- **`pdf_builder.py`**: new `stamp_pdf_metadata(pdf_bytes, book)` helper + `ISBN_PLACEHOLDER` constant. Uses pypdf `clone_from` + `add_metadata` to write `/Title`, `/Author`, `/Subject`, `/Producer`, `/Creator`, `/CreationDate`, `/ModDate`. Failure-tolerant — corrupt or encrypted input PDFs round-trip unchanged with a WARNING log rather than aborting the whole export.
- **`pdf_builder_weasy.py`**: stamping called LAST in both `build_book_pdf` and `build_cover_spread_pdf` (after `apply_print_boxes` + `ensure_even_page_count`), so no downstream step can clobber it. Idempotent.
- **`server.py`**: `Book` and `BookUpdate` Pydantic models gained `publisher: Optional[str] = ""`. Default behavior preserved (existing books have empty publisher).
- **`Editor.jsx`**: Book Details popover gained a Publisher input with helper text "stamped into PDF properties". ISBN placeholder updated to `"ISBN pending — assigned at publication"` and the field's helper text now reads "leave blank until assigned at publication" (per user policy that ISBN isn't available until publishing).
- **Save payload**: `publisher` field added to the autosave PUT body so the field round-trips through the API.

### Per-field policy (recorded so the next agent doesn't re-derive it)
| PDF field | Source | Fallback |
|---|---|---|
| `/Title` | `book.title` | `"Untitled"` |
| `/Author` | `book.author` | `""` |
| `/Subject` | `"ISBN <13-digit>"` or `"ISBN <10-digit>"` (validated regex) | `"ISBN pending — assigned at publication"` |
| `/Creator` | `book.publisher` (editorial / house name) | `"Self-published"` |
| `/Producer` | always `"Bindery WeasyPrint pipeline"` (software identifier, stable string — don't change without updating tests that grep it) | — |
| `/CreationDate`, `/ModDate` | current UTC, PDF `D:YYYYMMDDHHmmSSZ` form | — |

### ISBN validation (tightened beyond initial implementation)
Regex `[0-9]{13}` for ISBN-13 or `[0-9]{9}[0-9X]` for ISBN-10 (the trailing `X` is the ISO 2108 check character meaning value 10, only legal at position 10 of an ISBN-10). Caught a real bug — a previous lax regex would have accepted `"978-0-XX-XXXXXX-X"` as a valid ISBN. The strict regex falls back to placeholder for any half-typed input.

### Tests
- `tests/test_pdf_metadata.py`: **16/16 offline unit tests pass in 0.62s**. Cover Title/Author stamping, ISBN placeholder for blank/missing/malformed, 13-digit + 10-digit + X-check normalization, Publisher default, idempotency, corrupt-input round-trip, page-count preservation, and Unicode title round-trip.
- Cumulative offline regression coverage now **30 tests in <6 s**, no hosted-env dependency.

## P1.2 deferred (Editor.jsx refactor) — explicit reasoning
Not shipped in this iteration. Three reasons:
1. **The handoff explicitly gated it**: "Do not start until Issue 1 (Production PDF Timeout) is 100% resolved." The iter-51 PDF fixes are deploy-ready but not yet production-verified by the user.
2. **The toolbar was just heavily modified in iter 52**. Extracting it into a sub-component now means restructuring code that hasn't been deploy-tested.
3. **The autosave / refs interlock is deep**: `skipNextAutoSaveRef` is referenced from 4+ call sites across the file (autosave effect, history-restore callback, post-export, etc.). Naive `useAutoSave` extraction would either leave the ref shared (defeating the encapsulation) or require lifting 4+ call sites with separate stable callbacks. Worth doing properly, not in the same turn as a metadata-stamping ship.

**File the refactor as P1 backlog** — should be the first task picked up in the next dedicated session, after iter-51's production verification has confirmed PDF stability.

## What's been implemented (2026-06-21 / iteration 54 + 55 — Upload validation + admin storage audit)

### Iteration 54 — Upload-time validation (`server.py:/api/upload`)
Three pre-storage checks now run BEFORE any data hits object storage:
1. **Zero-byte rejection** → 400 with `"The uploaded file is empty. Please choose a real image file."`
2. **Pillow `Image.open(buf).verify()`** → 400 with `"This file appears to be corrupted or is not a valid image. Please try uploading it again."` Catches corrupted PNGs, truncated downloads, text files renamed `.png`, wrong-extension files.
3. **`im.size` both > 0** → 400 with `"This image has no pixel dimensions and can't be used in a book layout. Please try a different file."`

`width_px` / `height_px` are now ALWAYS populated in `db.files` rows. `/api/assets` projection extended to expose them for editor placeholder rendering.

`pdf_builder_weasy.py` got an optional `path_existence_check(paths) -> set[str]` callback that queries `db.files` once before the pre-fetch loop and emits one WARNING per orphaned/deleted reference. Falls back gracefully if the check itself fails. Empty-bytes branch in `_fetch_one` now also logs the specific path. Blank-PNG fallback behaviour unchanged.

### Iteration 55 — Admin storage audit panel
Three new surfaces (all admin-gated server-side, hidden from non-admin clients):
- **`POST /api/admin/storage-audit?limit=N`** (default 500, max 2000) — walks the most recently created N rows of `db.files`, re-probes each from object storage, and categorises into:
  - `fixed` — rows with missing dims that got backfilled
  - `bad` — Pillow can't open OR reports zero dimensions
  - `orphaned` — storage path doesn't exist anymore
  - Each `bad`/`orphaned` entry carries `book_id` + `book_title` from `db.books.pages.blocks[].image_path` lookup. Soft-deleted rows count as orphaned.
  - Idempotent; never deletes; bounded scan fits Cloudflare's edge timeout.
- **`GET /api/admin/recent-exports`** — returns last 20 JOB SUMMARY log lines from a process-local ring buffer captured via a logging handler. Response carries `scope: "this-pod-only"` so the admin knows this is per-process.
- **`StorageAuditDialog.jsx`** + **`RecentExportsDialog.jsx`** — modal UIs wired into the editor's "More" dropdown, gated by `useAuth().user.role === 'admin'`. Storage audit shows three sections (Fixed / Bad files / Orphaned records) with book titles attached, and a "Download report" button that serialises results as plain text.

### Tests
- `test_upload_validation.py` — 5/5 pass in 3.2s (integration; corrupted PNG, zero-byte, text-as-PNG, valid PNG, valid JPEG; persisted-dimensions round-trip via `/api/assets`).
- `test_admin_storage_audit.py` — 5/5 pass in 13s (integration; 403 for non-admin on BOTH endpoints, 200 with documented response shape for admin, end-to-end classification with synthetic data + book lookup + idempotency + no-delete verification).
- Offline regression: 30/30 pass in 5s (`test_font_subsetting.py` + `test_pdf_metadata.py`).

## Deploy-ready
Static analysis pass; both endpoints stress-tested under live load; admin-only gating verified on every endpoint plus client-side hide; backwards-compatible (no existing routes changed); upload validation strictly additive in front of the existing flow.

## Next Tasks
- Phase 3.3 — PDF document metadata (Title, Author, ISBN, Publisher into PDF properties).
- Phase 3.2 — ICC profile picker (SWOP v2 vs Fogra39) in export popover.
- Phase 3.4 — Starter-pack templates (8.5×8.5 Square / Casebound seeded on first launch).
- Pure-K text strict override (PostScript `defs.ps` colour map — currently blocked).
- Refactor `Editor.jsx` for maintainability (P1 — >2500 lines).
- Drag-to-reorder pages in the sidebar.


## 2026-02-13 — Iteration 14: DB-only Storage Audit + pre-export file-health pre-flight
**Goal**: Replace the previous Storage Audit (which timed out with HTTP 524 in production because it fetched image bytes from Object Storage during the scan) with a **pure MongoDB** audit that returns in milliseconds. Add a per-book pre-export check so users are warned BEFORE spending PDF render time on a book containing missing or no-dimension images.

### Backend
- **`POST /api/admin/storage-audit`** (`server.py` ~1732) — rewrote to issue exactly two indexed Mongo queries (`db.files` + `db.books`). No Pillow, no S3, no I/O. Returns `{ needs_reupload, orphaned, healthy_count, total_file_records, total_referenced_paths, ran_at, ran_by }`. Cross-references each problem path against the books that reference it so the UI can show book title + page number.
- **`GET /api/admin/recent-exports`** (`server.py` ~1833) — replaced the previous in-process ring-buffer (which only saw a single pod's exports) with a `db.pdf_jobs` query returning up to the last 20 jobs with `scope: "cluster-wide"`. Admin-only.
- **`GET /api/books/{book_id}/file-health`** (`server.py` ~1860) — per-book pre-flight returning `{ problems: [...], checked: N }`. Each problem is `{ storage_path, filename, page_no, issue: "missing"|"no_dimensions" }`. Available to any logged-in user for any book they own.
- **`pdf_builder_weasy.build_book_pdf`** — added optional `summary_cb(text)` parameter. The PDF worker installs a callback that writes the JOB SUMMARY line onto the `pdf_jobs.summary` field so the Recent Exports panel can display it cross-pod.

### Frontend
- **`StorageAuditDialog.jsx`** — refactored to consume the new response shape (three categorised sections: Needs re-upload / Orphaned / Healthy with counts and download-report button).
- **`RecentExportsDialog.jsx`** — refactored to consume DB-backed entries (`status`, `filename`, `size`, `summary`, `error`, `created_at`, `finished_at`) with a refresh button.
- **`FileHealthWarningDialog.jsx`** (new) — alert-dialog listing missing/no-dimension images with Cancel and Export anyway buttons.
- **`Editor.jsx onExportPdf`** — pre-flights `/file-health` before starting a PDF job. If problems are returned, shows the warning dialog instead of triggering the export. Export anyway re-invokes with `__skipHealthCheck: true` so the dialog never loops. Silent fall-through on network/HTTP failures keeps the pre-flight advisory rather than blocking.

### Tests
- **`test_admin_storage_audit.py`** rewritten end-to-end against the new contract (8 tests):
  - Non-admin gets 403 on both endpoints.
  - Admin gets 200 with documented shape (every key present even when empty).
  - Synthetic db.files row with no width_px/height_px + book reference → categorised as `needs_reupload` with book_title + page_no attached.
  - Synthetic orphan path referenced by a book → categorised as `orphaned`.
  - Read-only invariant verified (no rows deleted).
  - Recent Exports returns `scope: "cluster-wide"` and capped at 20.
  - File-health on unknown book → 404.
  - File-health on book with no image blocks → `problems: [], checked: 0`.
  - File-health on book with missing + no-dim images → both surfaced with correct `issue` discriminator and 1-indexed `page_no`.
- Testing agent (iteration 14) report: **21/21 targeted tests green; 179 passed in broad pytest run**; 12 pre-existing test_assets_api failures unrelated (hand-crafted PNG bytes failing strict Pillow validation introduced in iteration 54).

### Deploy-ready
- Live audit on production DB completes instantly and returns 341 file_records / 10 referenced paths / 44 needs_reupload / handful of orphans — exactly the data the audit is designed to surface.
- No new routes are public; all admin endpoints continue to require admin role.
- Backwards compatible with the existing PDF worker (summary_cb is optional).

## Next Tasks
- Refactor `Editor.jsx` (>2700 lines) into `EditorToolbar` / `EditorCanvas` / `PageSidebar` / `useAutoSave` hook (P1 maintainability).
- Phase 3.2 — ICC profile picker (SWOP v2 vs Fogra39).
- Phase 3.4 — Starter-pack templates.
- WeasyPrint FontConfiguration startup registration (last ~1s/page perf gap).
- Drag-to-reorder pages in the sidebar (P3).
- Remaining keyboard shortcuts: ⌘D duplicate, ⌘] bring forward, Delete.

## 2026-02-13 — Iteration 16: Editor.jsx Phase-1 Refactor (usePdfExport + useAutoSave)
**Goal**: Extract PDF-export logic and debounced autosave from `Editor.jsx` (2879 lines → 2461 lines, -418) into single-purpose custom hooks. Zero behaviour change — pure refactor for maintainability.

### New hook modules
- **`/app/frontend/src/hooks/usePdfExport.jsx`** (460 lines) — encapsulates the entire ~400-line `onExportPdf` flow. Handles: file-health pre-flight, saveBook flush, POST /pdf-jobs, 1.2 s poll loop with per-stage 6-min idle deadline, custom Sonner progress toast with cancel button, print/preview/download decision tree (`printAfter` → hidden iframe + `.print()`, `previewInTab` → new tab, else direct download), and success/error/cancel messaging. Exposes `{ onExportPdf, exporting, exportsBump, fileHealthProblems, onFileHealthCancel, onFileHealthProceed }`. Only depends on `{ book, saveBook }` — minimal surface.
- **`/app/frontend/src/hooks/useAutoSave.js`** (46 lines) — debounced 1.2 s autosave effect. Skips the first run (freshly loaded book). Returns `{ skipNextRef }` so callers (TOC auto-refresh, history restore) can suppress the next autosave cycle for programmatic `setBook()` calls that aren't user edits.

### Editor.jsx integration
- Line ~275: `useAutoSave(book, loading, saveBook)` replaces the previous 14-line effect.
- Line ~1157: `usePdfExport({ book, saveBook })` destructure replaces the previous 396-line inline `onExportPdf`.
- `FileHealthWarningDialog` now wires straight to `onFileHealthCancel` / `onFileHealthProceed` (no inline closures).
- Removed local state: `exporting`, `exportsBump`, `fileHealthProblems`, `pendingExport`, `autoSaveTimerRef` (all migrated into hooks).
- Removed `PdfExportToast` import from Editor.jsx (now imported inside the hook).

### Testing
- Testing agent iteration 16 report: **frontend regressions 100% pass**. Zero console errors, zero page errors across three Playwright sessions. FileHealthWarningDialog opens/cancels/proceeds correctly. PDF job starts + progress toast renders identically to pre-refactor. `skipNextAutoSaveRef` still suppresses autosave on TOC refresh (line 714) and history restore (line 1619) — programmatic mutations don't trigger phantom saves.

### Not extracted (deferred to Phase 2/3)
- `PageSidebar`, `EditorToolbar`, `EditorCanvas` — user explicitly chose Phase 1 only. Editor.jsx is still 2461 lines; further reduction available in a future iteration.

## Next Tasks
- Phase 2 refactor: `PageSidebar` + `EditorToolbar` extraction (low-risk).
- Phase 3 refactor: `EditorCanvas` extraction (higher risk — deeply coupled with page state, drag/drop, pinch-zoom).
- Phase 3.2 — ICC profile picker (SWOP v2 vs Fogra39).
- Phase 3.4 — Starter-pack templates.
- WeasyPrint FontConfiguration startup registration (last ~1s/page perf gap).
- Drag-to-reorder pages in the sidebar (P3).
- Remaining keyboard shortcuts: ⌘D duplicate, ⌘] bring forward, Delete.


## 2026-07-01 — Iteration 17: PDF-Job Diagnostics (Three Surgical Changes)
**Goal**: Close the diagnostic hole around production PDF-export timeouts. Zero changes to the render pipeline; only observability + retention.

### Change 1 — Split TTL for pdf_jobs
- `_PDF_JOB_TTL_READY_SECONDS = 30 min` — delivery-only retention
- `_PDF_JOB_TTL_FAILED_SECONDS = 7 days` — diagnostic retention
- Insert writes `expires_at = now + 7 days` (in case job stays pending forever). Success flips it to `now + 30 min`. Cancel/failure keep the 7-day window.
- `_ensure_pdf_jobs_indexes()` drops and recreates `pdf_jobs_ttl` on every startup (idempotent) — safe against past in-place-modify attempts.
- **Verified live**: ready job expires_at delta = 1800 s (30 min); cancelled job delta = 604800 s (7 days).

### Change 2 — JOB SUMMARY moved after upload
- `pdf_builder_weasy.build_book_pdf` now passes a **timings dict** to `summary_cb` (was: formatted string). Old `log.info(summary_text)` removed from the builder.
- `server.py._emit_job_summary()` composes the authoritative log line AFTER upload in all three exit paths (success / TIMEOUT / other exception). Persisted onto `pdf_jobs.summary` for cross-pod visibility.
- **New format** (verified live):
  ```
  WeasyPrint JOB SUMMARY: total=1.77s | prefetch=0.00s | render=1.31s | upload=0.47s | pages=1 | fonts: 1 used, 0 missing
  ```
  On upload timeout: `upload=FAILED (timeout after 120s)`. On other exception: `upload=FAILED (ConnectionError)` (or whatever the class name is).
- **Note**: `total` in the new format = prefetch + render + upload (whole-job wall clock). Different from the old total which excluded upload.

### Change 3 — Admin purge endpoint for orphaned 0-byte files
- `POST /api/admin/purge-empty-files?confirm=true|false` (default: dry-run).
- Deletes `db.files` rows where `size ∈ {0, null, missing}` AND row is not soft-deleted AND storage_path is NOT referenced by any book's pages/blocks.
- Object-storage bytes untouched — reaped separately by storage backend GC.
- Returns explicit counts: `candidates_matched`, `kept_because_referenced`, `eligible_for_delete`, `deleted`, `mode`.
- Preview DB reports 0 candidates (accurate; my earlier "170+ zero-byte" claim was integer-KB rounding). Production count unknown until run there.

### Testing (iteration 17 report)
- **9/9 pytest cases pass** (new file `test_pdf_ttl_and_purge.py`). Zero regressions.
- Index shape verified through supervisor restart.
- TTL windows verified on real docs (both ready and cancelled paths).
- JOB SUMMARY regex-matched against real export log line and persisted-doc content.
- Purge endpoint response shape verified end-to-end.

### Not changed
- WeasyPrint config, CHUNK_SIZE, font subsetting, image handling, download endpoint, all timeout values, response shape of any pre-existing route.

## Next Tasks
- Deploy iteration 17 to production and use the new diagnostics to attribute the timeout: is it render, upload, or download? The 7-day retention + persisted summary means the next timeout is now forensically diagnosable.
- Consider splitting server.py (2243 lines) — testing agent flagged it.
- Consider awaiting the Mongo `summary` write instead of fire-and-forget for stronger delivery guarantees (or log on failure). Currently swallowed; log line in supervisor is the fallback authoritative record.
- Longer term: Phase 2 refactor of Editor.jsx (PageSidebar + EditorToolbar) if user wants.


## 2026-07-01 — Iteration 18: Frontend Timeout Fixes
**Root cause of user's 41/64-page "fail"**: frontend idle deadline was a fixed 6 min; a 64-page render at 2-3 s/page has stage-string gaps longer than 6 min between chunk boundaries, so the frontend gave up while the backend was still rendering.

### Fix 1 — Dynamic idle deadline in `usePdfExport.jsx`
- Formula: `min(20 min, 6 min + pages × 3 s)`.
- Constants: `BASE_IDLE_MS=360_000`, `PER_PAGE_MS=3_000`, `MAX_IDLE_MS=1_200_000`.
- 32-page book → 7.6 min ceiling; 64-page → 9.2 min; 120-page → 12 min; anything ≥300 pages → capped at 20 min.
- Verified bit-for-bit in-browser by testing agent.

### Fix 2 — "Check again" recovery button
- Timeout error toasts now include a Sonner action button labelled **"Check again"** — offered only when `isTimeout && cancelState.jobId` (irrelevant for network / build failures).
- On click, re-polls `/api/books/{id}/pdf-jobs/{job_id}` ONE time:
  - `status: ready` → downloads via anchor click, dismisses the old error toast, shows success + bumps `exportsBump`.
  - `status: failed` → surfaces the backend's real `error` field.
  - `status: pending` → toast: "Still rendering ({stage}). Try Check again in ~30 s."
  - `404` (job TTL'd out) → "Job no longer available on the server — please re-export."

### Fix 3 — Recent Exports 401 (cookie fallback)
- `RecentExportsDialog` fetch now sends both `Authorization: Bearer ...` (from localStorage) AND `credentials: 'include'` (httpOnly cookie fallback).
- Backend `_extract_token` accepts either path (cookie preferred).
- Verified LIVE: with localStorage cleared, the fetch still returns 200 via cookie.

### Not changed
- Backend rendering pipeline, CHUNK_SIZE, WeasyPrint config, all timeout values (server-side upload timeout stays at 120 s; asyncio deadlines unchanged).

## Next Tasks
- Redeploy to production; the next 41/64-style "fail" should now either (a) complete cleanly with the 9.2 min ceiling, or (b) recover via the Check again button.
- Consider extracting the recovery block in `usePdfExport.jsx` into `buildRecoveryAction(...)` for testability — flagged by testing agent.
- Convert `RecentExportsDialog` auto-load from side-effect-in-render to `useEffect(() => { if (open) load(); }, [open])` — cleaner React idiom, non-blocking.
- Phase 2 Editor.jsx refactor (PageSidebar + EditorToolbar) — still queued from prior session.


## 2026-07-01 — Iteration 19: Three Production Bugs From /api/pdf-health Forensics
Root cause of user's failed 64-page prod job (`361de655-b244-4f0a-b7ed-684254f04352` "The Treasure Map of Money Mountain"): a cover-spread bug + a suspected page-21 WeasyPrint hang + a Recent-Exports 401 that iteration 18's fix didn't fully close.

### Bug 1 — `render_timings` UnboundLocalError on cover-spread path
- `render_timings: dict = {}` was defined only inside the `else` branch of `if cover_spread:` in `server.py:_run_pdf_job`.
- Cover-spread exports (or any path skipping the else branch) hit the shared `_emit_job_summary(..., render_timings, ...)` call with the name never bound in that frame → UnboundLocalError.
- Fix: hoisted `render_timings = {}` above the `if cover_spread:` branch so both paths share a live binding. Removed the duplicate in-else definition.
- Verified live: cover-spread POST job → status=ready; JOB SUMMARY log line: `total=n/a | prefetch=n/a | render=n/a | upload=0.68s | pages=n/a` (n/a render fields expected — cover spread doesn't emit render timings).

### Bug 2 — New diagnostic endpoint for the page-21 WeasyPrint hang
- `GET /api/admin/book-diagnose/{book_id}?page_no=N&probe_bytes=bool` — read-only admin endpoint.
- Returns per-page block layout + db.files metadata. With `probe_bytes=true`, fetches each image from object storage and runs Pillow verify+decode, returning `reachable / byte_count / decode_ok / decode_error / actual_width / actual_height`.
- Zero side effects (proven: `book.updated_at` unchanged after 3 repeated calls; no render triggered; no writes).
- Purpose: the user's failing production book doesn't exist in preview. This endpoint lets the operator inspect it themselves and pinpoint the page-21 problem.

### Bug 3 — Recent Exports 401 (real fix, this time)
- Iteration 18's `credentials:'include'` fix was cargo-cult — it made preview happy but didn't reach production because production uses JWT in localStorage, not cookies.
- Real bug: `RecentExportsDialog` was the ONE component using raw `fetch()` instead of the shared axios `api` client whose interceptor injects the Bearer header.
- Fix: switched to `api.get('/admin/recent-exports')`. Same pattern as every other authenticated call.
- Verified LIVE via network capture: outbound request now carries `Authorization: Bearer …`, no cookie header (credentials mode correctly removed).

### Testing (iteration 19 report)
- **7/7 backend tests pass**. Bug 3 verified end-to-end via live browser network capture. Zero regressions.
- New test file: `/app/backend/tests/test_iteration19_bugs.py`.

### Code-review nits from the testing agent (non-blocking)
- `admin_book_diagnose` with `probe_bytes=true` on a 300-page book issues 300 sequential storage fetches. For the current diagnostic-only use case that's fine; when scaled, add a semaphore + gather.
- `RecentExportsDialog` still auto-loads inside render (unchanged since prior iteration). Convert to `useEffect(() => open && load(), [open])` as a cleanup.

## Next Tasks
- Operator: hit `/api/admin/book-diagnose/361de655-.../page_no=21?probe_bytes=true` on production. The `probe.decode_ok`, `probe.byte_count`, and `file_record.size` fields should identify page 21's failure mode.
- Once the page-21 root cause is known, decide fix path: (a) instruct user to re-upload the offending image, (b) add a WeasyPrint timeout guard for individual page renders, or (c) both.
- Phase 2 Editor.jsx refactor still queued.
- Convert RecentExportsDialog auto-load to useEffect (cleanup).


## 2026-07-01 — Iteration 20: Diagnose-Page Frontend
Small frontend for the iteration-19 `admin_book_diagnose` endpoint so the operator doesn't need to run curl.

### New — `BookDiagnoseDialog.jsx`
- Admin-only "Diagnose page…" item in the Editor's More dropdown (`data-testid=more-menu-book-diagnose`).
- Modal with two fields:
  - **Book ID** — prefilled with the currently loaded book's id.
  - **Page number (1-based)** — prefilled with `activePageIndex + 1`.
- "Run diagnostic" button calls `GET /api/admin/book-diagnose/{book_id}?page_no=N&probe_bytes=true` via the shared axios `api` client.
- Four plain-English translations exported as the pure named function `diagnose(pageReport)` for testability:
  1. `probe.decode_ok === false` → *"This image appears corrupted. Delete the image block on this page and re-upload the file."*
  2. `file_record == null` → *"No file record found. This image is orphaned — delete the block and re-upload."*
  3. `width_px == 0` or missing → *"This image has no stored dimensions. Delete the block and re-upload."*
  4. All clean → *"Page looks healthy. The hang may be environmental — try exporting just this page range."*
- Reachability probe (`reachable=false`) and zero-byte size are also handled explicitly.
- Raw response JSON is available in a collapsed `<details>` for power users.

### Editor.jsx wiring
- New `bookDiagnoseOpen` state.
- Menu item added to the admin-only block in the More dropdown.
- Dialog mounted inside the `{isAdmin && ...}` block with `defaultBookId={book?.id}` and `defaultPageNo={activePageIndex + 1}`.

### Testing (iteration 20 report)
- **100% pass on all executed checks.**
- All four translation branches verified via Playwright `page.route()` interception with canned JSON payloads.
- Prefill verified: 1 → 3 transition when navigating pages before opening the dialog.
- Read-only invariant: `book.updated_at` unchanged after 3 repeated diagnostic runs.
- Zero console errors, zero page errors.
- Testing agent code-review: pure translator separation is clean, priority ordering matches the WeasyPrint-hang triage rules, admin-only guard is defense-in-depth (both menu + mount guarded; backend also 403s non-admins).

### Not verified
- Non-admin visibility: no non-admin account seeded in the environment. Guard is defense-in-depth (menu item + mount + backend all guarded), so functional protection is intact.

## Next Tasks
- Operator: use the new "Diagnose page…" menu on production against `361de655-b244-4f0a-b7ed-684254f04352` page 21. Whatever the diagnosis says, act on it, then re-export.
- Optional: seed a non-admin user for explicit E2E coverage of guard boundaries.
- Phase 2 Editor.jsx refactor still queued.


## 2026-07-01 — Iteration 21: Diagnostic Endpoint Extension (Investigation Only)
User reported page 21 of prod book `361de655-b244-4f0a-b7ed-684254f04352` (text-only, single block) hangs WeasyPrint. Investigation-only iteration — no render code changed.

### Endpoint extension: `GET /api/admin/book-diagnose/{book_id}`
Added optional query param `include_text_content=true` (default false). When true, text blocks include:
- `html` (truncated at 20 KB)
- `html_truncated` (bool)
- `font_family`, `font_size`, `text_align`, `color`, `background_color`, `text_role`, `is_chapter`, `is_toc`

Response now also includes at the top level:
- `cached_font_families` (list of 27 Google Fonts families known to `fonts_cache.GOOGLE_FONTS_URL`)
- `book_text_presets` (from `db.books.text_presets`)
- Echo of `include_text_content` flag

Per-page:
- `background_color`, `full_bleed`, `show_page_number`, `page_number_font`, `page_number_size`
- `fonts_referenced` — sorted list of every font_family value on the page + page_number_font if enabled
- `fonts_missing_from_cache` — the delta against `cached_font_families` (WeasyPrint fallback candidates)

### Investigation findings written to prior message
- **Q1-Q3**: cannot answer from preview (book only in prod). The endpoint extension above lets the operator pull page 21/20/22 content in one call.
- **Q5** (confirmed): `asyncio.wait_for(asyncio.to_thread(_render_chunk_sync, ...), timeout=240)` does NOT kill the underlying thread. Python threads cannot be forcibly cancelled from outside — the `write_pdf()` call keeps consuming CPU + memory after the awaiter times out. This is a latent issue; the appropriate fix is `multiprocessing.Process` with `.terminate()` (Q4 territory, pending user green-light).

### Testing (iteration 21 report)
- **10/10 pytest tests pass** in `/app/backend/tests/test_book_diagnose_text_content.py`.
- Truncation math verified: 25 KB input → 20 KB html + html_truncated=true + text_length_chars=25000 (unchanged).
- Read-only invariant preserved (book.updated_at unchanged after 2 diagnose calls with include_text_content=true).
- Regression: old consumers (BookDiagnoseDialog frontend) unaffected — the four English translations use `type/file_record/probe/geom` which are unchanged.
- Code-review nit (non-blocking): `_CACHED_FAMILIES` is a hand-maintained mirror of `fonts_cache.GOOGLE_FONTS_URL`. Currently 27/27 match; a future edit that adds a family in one place without the other would silently produce false-positive missing-fonts entries. Recommend deriving the set at import time from the URL string.

### Not changed
- Render pipeline, WeasyPrint config, all timeout values, download endpoint, upload endpoint, CORS config.

## Next Tasks
- Operator: hit the new endpoint on prod for pages 20/21/22 of the failing book with `include_text_content=true`. Report back the JSON so we can decide on Q4/Q5 fixes.
- Pending Q4 green-light: replace `asyncio.to_thread(_render_chunk_sync, ...)` with a `multiprocessing.Process` + `.terminate()` pattern that reclaims memory + CPU on hang. Log the offending page's full block content on timeout.
- Phase 2 Editor.jsx refactor still queued.
- Derive `_CACHED_FAMILIES` from `fonts_cache.GOOGLE_FONTS_URL` at import time (2-line drift-elimination fix).


## What's been implemented (2026-02 / iteration 22 — Multiprocessing hard-kill VERIFIED)
- **Verified**: the iteration-21-pending `multiprocessing.Process` + `.terminate()` swap in `pdf_builder_weasy.py` works end-to-end. All 5 criteria the user asked to confirm are green:
  1. **Happy-path unchanged** — 23/23 existing PDF export tests pass (`test_pdf_export.py`, `test_pdf_export_jobs.py`, `test_pdf_export_timeouts.py`).
  2. **60s hard-kill fires on hang** — new `test_pdf_hard_kill.py` injects a `time.sleep(90)` into WeasyPrint's `write_pdf` (via a module-level `_ConditionalSleepingHTML` shim + monkey-patched `_PAGE_PROCESS_TIMEOUT_S=3.0`). The subprocess is `SIGTERM`ed within the shortened budget, blank placeholder substituted, whole call returns a valid PDF in <30s.
  3. **WARNING log** — asserted the emitted warning line includes both `page N` AND the block content dump (`html='<p>HANG_MARKER_XYZ</p>'`, `font=...`, `size=...`, `role=...`). Operator can pinpoint the pathological block from the log alone.
  4. **No zombie processes** — two-layer check: `multiprocessing.active_children()` diff is empty AND `/proc/*` scan shows no `Z`-state children parented to the parent PID after the kill.
  5. **Frontend include_text_content + missing-fonts amber banner** — testing-agent-v3 fork verified against the live preview URL (report `/app/test_reports/iteration_22.json`, 11/11 UI steps green): checkbox default-ON, ON sends `include_text_content=true`, OFF omits the param (via `...(x ? { key: true } : {})` spread), and mocked `fonts_missing_from_cache=['Comic Sans MS','Weird Font']` renders the amber banner with `data-testid=book-diagnose-missing-fonts`.

### Files added
- `/app/backend/tests/test_pdf_hard_kill.py` (3 tests, ~15s wall-clock)

### Ready for prod redeploy
- User must redeploy `bindery.au` to pick up the multiprocessing swap. Once redeployed, re-attempt the page-21 export on the failing book. Any subsequent hang will show a specific WARNING in the pod logs identifying the runaway page + block, and the export will complete with a `[page could not be rendered]` placeholder rather than timing out the whole job.


## What's been implemented (2026-02 / iteration 23 — pikepdf stamping refactor)
- **Root cause** confirmed for the 64-page "Treasure Map of Money Mountain" prod failure: `apply_print_boxes` and `ensure_even_page_count` in `pdf_builder.py` used pypdf's `PdfReader` → `PdfWriter.add_page` pattern which clones every page's Python object graph then serialises it back to bytes. Peak Python heap ≈ 3-5× the PDF file size. On a 100-150 MB merged PDF this comfortably hit the pod's `MemFree: 692 MB` ceiling and failed the stamping step even though all 64 pages rendered successfully.
- **Fix shipped**: Both functions rewritten to use **pikepdf** (libqpdf C++ wrapper, wheel bundles libqpdf 12.3.2 — no apt dependency). Pattern is:
  1. Spool input bytes to a temp file, release the Python bytes buffer.
  2. `pikepdf.open(path)` streams from disk; mutate `trimbox`/`bleedbox`/`cropbox` **in-place** — no page clone step.
  3. `pdf.save(out_path)`; read bytes back.
  4. `try/finally` guarantees both temp files are unlinked even if pikepdf raises mid-processing (verified by `test_apply_print_boxes_cleans_up_on_exception`).
- **Regression coverage** (`/app/backend/tests/test_pikepdf_stamping_regression.py`, 18 tests):
  * **Box parity vs old pypdf impl** — TrimBox/BleedBox/CropBox/MediaBox match to 4dp on every page for `page_count ∈ {1, 2, 3, 5, 10, 32, 64}` interior renders + `{1, 3, 5, 9, 33, 63}` even-padding scenarios. The old pypdf implementation is preserved inline in the test file as the ground truth.
  * **Absolute geometry spec-check** — verifies IngramSpark v5.11.26 box scheme directly (recto TrimBox `[0, 9, 621, 801]`, verso TrimBox `[9, 9, 630, 801]`) so both impls can't drift the same way.
  * **Temp-file cleanup** — no `stamp_in_*.pdf` / `evenpad_in_*.pdf` orphans in `/tmp` after either success or exception paths.
- **Test status**: 27/27 stamping + IngramSpark Phase 1 + hard-kill tests pass serially. All 6 Phase 2 tests pass in isolation.
- **`requirements.txt`** now includes `pikepdf==10.9.1`. Wheel is self-contained (`pikepdf.libs/libqpdf-*.so.30.3.2` bundled). No apt-get required.
- **Preview `/api/pdf-health`** — backend restarts cleanly with pikepdf loaded; WeasyPrint ready, Chromium launchable.

## What's been implemented (2026-02 / iteration 23b — Export in halves UI workaround)
- **Feature**: New "Export in halves" section in `ExportPopover.jsx`, visible only when the book has ≥40 pages. Two side-by-side buttons that split at the midpoint: **Pages 1–⌈N/2⌉** and **Pages ⌈N/2⌉+1–N**. Each button calls the existing range-export flow (no backend changes).
- **Why keep it after the pikepdf fix**: for a 120-page book, exporting in two 60-page halves is a legitimate workflow choice — the author gets more control over what goes to the printer even when the full export works. Kept as a permanent option per user request.
- **Data-testids**: `export-halves-section`, `export-half-first`, `export-half-second`.
- **No lint errors** introduced (the 6 lint warnings still in the file are all pre-existing empty-`catch` and unescaped-apostrophe issues from earlier commits).

### render_timings UnboundLocalError — status
- Investigated per user Q3. Fix already shipped in preview code (`server.py:943` initializes `render_timings: dict = {}` at the top of the try-block, BEFORE all 3 `_emit_job_summary` call sites at 1065/1073/1078, BEFORE the cover_spread/interior branch at 945, and the outer `except Exception` handler at 1111 does not reference it). Only `server.py` references `render_timings` — grepped every `.py` file. Job `a7a31fed` failing with this error means production was running an older build; will be resolved by the next redeploy.


## What's been implemented (2026-02 / iteration 24 — pikepdf merge refactor)
- **Extended the disk-spool pattern to the chunk-merge step.** `pdf_builder_weasy.build_book_pdf` previously merged N rendered chunks by:
  1. Holding every chunk's raw bytes in the `chunk_pdfs` Python list.
  2. Iterating with pypdf: `PdfReader(BytesIO(blob))` per chunk → `PdfWriter.add_page(page)` for every page (Python object clone).
  3. `writer.write(BytesIO())` serialised the whole cloned tree back to bytes.
  For a 64-page book with 5-10 MB per chunk that's ~50-100 MB of raw bytes AND another 50-100 MB of pypdf's Python-object graph AND the serialised output all live at once = ~200-300 MB peak Python heap.
- **New `_merge_pdfs_disk(chunk_pdfs: list[bytes]) -> bytes` helper** in `pdf_builder_weasy.py`:
  1. Pop-and-spool each chunk to its own temp file (`merge_chunk_*.pdf`); popping releases the bytes reference before the next iteration so peak in-memory chunk count stays at 1.
  2. `pikepdf.open(first_path)` as base; iterate remaining paths and `base.pages.extend(extra.pages)` — pikepdf deep-copies referenced objects into `base` so closing the source `Pdf` is safe.
  3. `base.save(out_path)` → read bytes back.
  4. `try/finally` unconditionally unlinks every temp file (chunk + output) even if pikepdf raises mid-merge.
- **Peak Python heap** during merge is now ~1× the largest single chunk (vs the previous ~3-5× of the merged file size). libqpdf's C++ layer handles the page trees natively — no Python-object clone.
- **Regression coverage** (`/app/backend/tests/test_merge_pdfs_disk_regression.py`, 11 tests):
  * Merge parity vs old pypdf implementation — page count + MediaBox to 4dp match for `chunk_shapes ∈ [1], [5], [1,1], [3,3], [1,5,1,5,1,5], [1]×32, [1]×64`.
  * List-mutation contract — input `chunk_pdfs` is emptied after merge (fast path leaves single-blob list untouched).
  * Temp-file cleanup — no `merge_chunk_*` or `merge_out_*` orphans in `/tmp` after either success or a forced pikepdf exception.
- **Test status**: 52/52 pass across merge regression + stamping regression + IngramSpark Phase 1 + hard-kill + full PDF job flow.
- **No other code changed** — the surrounding chunk-render loop, stamping step, and PDF/X pipeline are untouched.

