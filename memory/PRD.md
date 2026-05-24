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
- Verified end-to-end via testing agent (iteration_11): click-to-edit, double-click edit, font groups, Pacifico application, page-number font, chapter visibility gating, TOC contents, PDF export, save status — all PASS.

## Prioritized Backlog
### P1
- Refactor `Editor.jsx` (1236 lines): split into toolbar / canvas / TOC builder modules.
- Multi-select + alignment guides + snapping.
- Undo/Redo history.

### P2
- Live-sync TOC (currently a one-shot insert; auto-update when chapters change).
- Reorder pages by drag in the sidebar.
- Text on path / shape blocks / decorative dividers.
- More page templates ("Children's book", "Photo book", "Manuscript").
- Share read-only preview link.

### P3
- AI-assisted illustration generation per page (Nano Banana).
- AI text editing assistance.
- Multi-user accounts + library sharing.

## Next Tasks
- Refactor `Editor.jsx` for maintainability.
- Live-sync TOC.
- Undo/Redo history.
