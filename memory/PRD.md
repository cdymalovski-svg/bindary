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

## Prioritized Backlog
### P1
- Per-page background color & cover page template.
- Drag-drop image directly onto the canvas (currently file picker only).
- Multi-select + alignment guides + snapping.
- Undo/Redo history.

### P2
- Reorder pages by drag in the sidebar.
- Text on path / shape blocks / decorative dividers.
- Theme templates ("Children's book", "Photo book", "Manuscript").
- Share read-only preview link.
- Auto-save with debounce.

### P3
- AI-assisted illustration generation per page (Nano Banana).
- AI text editing assistance.
- Multi-user accounts + library sharing.

## Next Tasks
- Drag-and-drop image upload onto the page canvas.
- Sidebar page reorder via drag.
- Auto-save with debounce + dirty-state indicator.
