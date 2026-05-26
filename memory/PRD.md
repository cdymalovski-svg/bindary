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
- AI-assisted illustration generation per page (Nano Banana).
- AI text editing assistance.
- Multi-user accounts + library sharing.

## Next Tasks
- Refactor `Editor.jsx` for maintainability (now P1 — file size is regression-prone).
- Undo/Redo history.
- Live-sync TOC.
