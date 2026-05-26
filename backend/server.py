from fastapi import FastAPI, APIRouter, UploadFile, File, Form, HTTPException, Response, Request
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import re
import logging
import uuid
import asyncio
import requests
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Any
from datetime import datetime, timezone, timedelta


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Storage configuration
STORAGE_URL = "https://integrations.emergentagent.com/objstore/api/v1/storage"
EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY")
APP_NAME = "booktemplate"
storage_key: Optional[str] = None


def init_storage():
    global storage_key
    if storage_key:
        return storage_key
    resp = requests.post(
        f"{STORAGE_URL}/init",
        json={"emergent_key": EMERGENT_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    storage_key = resp.json()["storage_key"]
    return storage_key


def put_object(path: str, data: bytes, content_type: str) -> dict:
    key = init_storage()
    resp = requests.put(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key, "Content-Type": content_type},
        data=data,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def get_object(path: str):
    key = init_storage()
    resp = requests.get(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")


MIME_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


# ===== Models =====
def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class Block(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str  # 'text' | 'image'
    x: float = 40
    y: float = 40
    width: float = 300
    height: float = 120
    z_index: int = 1
    # Text block
    html: Optional[str] = ""
    font_family: Optional[str] = "Cormorant Garamond"
    font_size: Optional[int] = 18
    text_align: Optional[str] = "left"
    color: Optional[str] = "#000000"
    # Image block
    image_url: Optional[str] = None
    image_path: Optional[str] = None
    # Chapter heading metadata (text blocks only)
    is_chapter: bool = False


class Page(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    blocks: List[Block] = []
    background_color: Optional[str] = "#FFF8DC"
    show_page_number: bool = True
    page_number_align: Optional[str] = "right"  # 'left' | 'center' | 'right'
    page_number_size: Optional[int] = 14
    page_number_font: Optional[str] = "Cormorant Garamond"
    full_bleed: bool = False  # when true, ignore the 1cm white margin (edge-to-edge)


class TextPreset(BaseModel):
    model_config = ConfigDict(extra="ignore")
    font_family: Optional[str] = None
    font_size: Optional[int] = None
    text_align: Optional[str] = None
    color: Optional[str] = None


class TextPresets(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: Optional[TextPreset] = None
    subtitle: Optional[TextPreset] = None
    body: Optional[TextPreset] = None


class Book(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = "Untitled Book"
    author: Optional[str] = ""
    page_size: str = "a4"  # a4 | letter | square | book6x9
    pages: List[Page] = []
    page_number_start: int = 1  # 1-based; pages before this show no number; back cover always hidden
    is_chapter_book: bool = False  # enables the Add Chapter action and auto-numbered chapter headings
    text_presets: Optional[TextPresets] = None  # per-book overrides for Title/Subtitle/Page-text defaults
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class BookCreate(BaseModel):
    title: str = "Untitled Book"
    author: Optional[str] = ""
    page_size: str = "a4"


class BookUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: Optional[str] = None
    author: Optional[str] = None
    page_size: Optional[str] = None
    page_number_start: Optional[int] = None
    is_chapter_book: Optional[bool] = None
    text_presets: Optional[TextPresets] = None
    pages: Optional[List[Page]] = None


class BookSummary(BaseModel):
    id: str
    title: str
    author: Optional[str] = ""
    page_size: str
    page_count: int
    created_at: str
    updated_at: str
    cover_image_url: Optional[str] = None


class TemplateStyle(BaseModel):
    model_config = ConfigDict(extra="ignore")
    background_color: Optional[str] = "#FFF8DC"
    full_bleed: bool = False
    show_page_number: bool = True
    page_number_align: Optional[str] = "right"
    page_number_size: Optional[int] = 14


class Template(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    page_size: str = "a4"
    cover: TemplateStyle = Field(default_factory=TemplateStyle)
    interior: TemplateStyle = Field(default_factory=TemplateStyle)
    back_cover: TemplateStyle = Field(default_factory=TemplateStyle)
    text_presets: Optional[TextPresets] = None
    created_at: str = Field(default_factory=_now_iso)


class TemplateCreate(BaseModel):
    name: str
    page_size: str = "a4"
    cover: TemplateStyle
    interior: TemplateStyle
    back_cover: TemplateStyle
    text_presets: Optional[TextPresets] = None



class BookRevision(BaseModel):
    """Lightweight audit-log entry. Stores a full snapshot of the book's
    user-facing state at a moment in time so we can list "saves" and roll
    back when an author wants to recover a previous version."""
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    book_id: str
    created_at: str = Field(default_factory=_now_iso)
    title: str = "Untitled Book"
    author: Optional[str] = ""
    page_size: str = "a4"
    page_count: int = 0
    pages: List[Page] = []
    page_number_start: int = 1
    is_chapter_book: bool = False
    text_presets: Optional[TextPresets] = None
    # Human-readable summary derived from the diff against the previous revision.
    summary: str = ""


class BookRevisionSummary(BaseModel):
    id: str
    book_id: str
    created_at: str
    title: str
    page_count: int
    summary: str


# Auto-save fires every ~1.2s during editing. Collapsing those into one
# revision per minute keeps the audit log readable without losing meaningful
# checkpoints.
REVISION_DEBOUNCE_SECONDS = 60
# Per-book cap. Older revisions are trimmed.
REVISION_MAX_PER_BOOK = 20


def _summarize_revision(prev: Optional[dict], curr: dict) -> str:
    """One-line summary of what changed compared to the previous revision."""
    if not prev:
        return "Initial save"
    parts: List[str] = []
    if prev.get("title") != curr.get("title"):
        parts.append("title")
    if prev.get("author") != curr.get("author"):
        parts.append("author")
    if prev.get("page_size") != curr.get("page_size"):
        parts.append("page size")
    if bool(prev.get("is_chapter_book")) != bool(curr.get("is_chapter_book")):
        parts.append("chapter mode")
    if prev.get("text_presets") != curr.get("text_presets"):
        parts.append("text presets")
    prev_pages = prev.get("pages") or []
    curr_pages = curr.get("pages") or []
    if len(prev_pages) != len(curr_pages):
        delta = len(curr_pages) - len(prev_pages)
        parts.append(f"{abs(delta)} page{'s' if abs(delta) != 1 else ''} {'added' if delta > 0 else 'removed'}")
    else:
        # Per-page block deltas
        block_changes = 0
        for p_old, p_new in zip(prev_pages, curr_pages):
            old_blocks = p_old.get("blocks") or []
            new_blocks = p_new.get("blocks") or []
            if len(old_blocks) != len(new_blocks) or any(
                ob.get("id") != nb.get("id") or ob.get("html") != nb.get("html") or
                ob.get("x") != nb.get("x") or ob.get("y") != nb.get("y") or
                ob.get("width") != nb.get("width") or ob.get("height") != nb.get("height")
                for ob, nb in zip(old_blocks, new_blocks)
            ):
                block_changes += 1
        if block_changes:
            parts.append(f"{block_changes} page{'s' if block_changes != 1 else ''} edited")
    return ", ".join(parts) if parts else "Minor change"


async def _maybe_snapshot(book_doc: dict):
    """Snapshot the book to book_revisions if the last revision is older than
    REVISION_DEBOUNCE_SECONDS (or none exists). Trim to REVISION_MAX_PER_BOOK."""
    book_id = book_doc["id"]
    latest = await db.book_revisions.find(
        {"book_id": book_id}, {"_id": 0}
    ).sort("created_at", -1).limit(1).to_list(1)
    if latest:
        try:
            last_ts = datetime.fromisoformat(latest[0]["created_at"])
            now_ts = datetime.now(timezone.utc)
            if (now_ts - last_ts).total_seconds() < REVISION_DEBOUNCE_SECONDS:
                return  # within debounce window; skip
            prev_doc = latest[0]
        except Exception:
            prev_doc = latest[0]
    else:
        prev_doc = None
    summary = _summarize_revision(prev_doc, book_doc)
    revision = BookRevision(
        book_id=book_id,
        title=book_doc.get("title", "Untitled Book"),
        author=book_doc.get("author", ""),
        page_size=book_doc.get("page_size", "a4"),
        page_count=len(book_doc.get("pages") or []),
        pages=[Page(**p) for p in (book_doc.get("pages") or [])],
        page_number_start=book_doc.get("page_number_start", 1),
        is_chapter_book=bool(book_doc.get("is_chapter_book")),
        text_presets=book_doc.get("text_presets"),
        summary=summary,
    )
    await db.book_revisions.insert_one(revision.model_dump())
    # Trim
    all_revs = await db.book_revisions.find(
        {"book_id": book_id}, {"_id": 0, "id": 1, "created_at": 1}
    ).sort("created_at", -1).to_list(1000)
    if len(all_revs) > REVISION_MAX_PER_BOOK:
        to_delete = [r["id"] for r in all_revs[REVISION_MAX_PER_BOOK:]]
        await db.book_revisions.delete_many({"id": {"$in": to_delete}})


# ===== App =====
app = FastAPI()
api_router = APIRouter(prefix="/api")


@app.on_event("startup")
async def startup():
    try:
        init_storage()
        logging.info("Storage initialized")
    except Exception as e:
        logging.error(f"Storage init failed: {e}")
    # Ensure the pdf_jobs collection has its TTL + lookup indexes.
    try:
        await _ensure_pdf_jobs_indexes()
    except Exception as e:
        logging.warning(f"pdf_jobs index setup deferred: {e}")
    # PDF export requires Chromium, but downloading it (~200 MB, ~20 s) must
    # NEVER block FastAPI's startup — uvicorn's lifespan has a hard timeout
    # and a slow install would leave the entire app unresponsive (book CRUD,
    # asset list, everything). Kick the install off as a background task so
    # the API answers requests immediately; the PDF endpoint waits on the
    # same task lazily.
    import asyncio as _asyncio
    from pdf_builder import ensure_chromium_installed

    async def _bg_install():
        try:
            await ensure_chromium_installed()
        except Exception as e:
            logging.warning(f"Chromium background install failed (will retry on first PDF request): {e}")

    _asyncio.create_task(_bg_install())


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()


# ===== Routes =====
@api_router.get("/")
async def root():
    return {"message": "Book Template API"}


@api_router.post("/books", response_model=Book)
async def create_book(payload: BookCreate):
    # Start with one blank page
    book = Book(
        title=payload.title or "Untitled Book",
        author=payload.author or "",
        page_size=payload.page_size or "a4",
        pages=[Page()],
    )
    doc = book.model_dump()
    await db.books.insert_one(doc)
    return book


@api_router.post("/books/import", response_model=Book)
async def import_book(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    author: Optional[str] = Form(None),
    page_size: str = Form("a4"),
):
    """Create a new book from an uploaded manuscript (.docx, .md, .txt).
    Smart-splits the document into pages and pre-populates each page with
    a centered text block leaving the bottom half empty for an illustration."""
    from book_importer import (
        parse_manuscript,
        chunks_to_pages,
        derive_title_and_author,
    )

    if page_size not in {"a4", "letter", "square", "book6x9"}:
        page_size = "a4"

    data = await file.read()
    # Cap manuscript size — same ceiling as the image uploader.
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 10MB)")
    try:
        chunks = parse_manuscript(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logging.exception("Manuscript parsing failed")
        raise HTTPException(400, f"Could not parse manuscript: {e}")

    derived_title, derived_author = derive_title_and_author(chunks)
    pages_data = chunks_to_pages(chunks, page_size_key=page_size)

    book = Book(
        title=(title or derived_title or "Untitled Book").strip()[:200],
        author=(author or derived_author or "").strip()[:200],
        page_size=page_size,
        pages=[Page(**p) for p in pages_data],
    )
    await db.books.insert_one(book.model_dump())
    return book


@api_router.get("/books", response_model=List[BookSummary])
async def list_books():
    books = await db.books.find({}, {"_id": 0}).sort("updated_at", -1).to_list(1000)
    summaries: List[BookSummary] = []
    for b in books:
        cover_url = None
        pages = b.get("pages") or []
        if pages:
            for blk in pages[0].get("blocks", []) or []:
                if blk.get("type") == "image" and blk.get("image_url"):
                    cover_url = blk["image_url"]
                    break
        summaries.append(
            BookSummary(
                id=b["id"],
                title=b.get("title", "Untitled Book"),
                author=b.get("author", ""),
                page_size=b.get("page_size", "a4"),
                page_count=len(pages),
                created_at=b.get("created_at", _now_iso()),
                updated_at=b.get("updated_at", _now_iso()),
                cover_image_url=cover_url,
            )
        )
    return summaries


@api_router.get("/books/{book_id}", response_model=Book)
async def get_book(book_id: str):
    doc = await db.books.find_one({"id": book_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Book not found")
    return Book(**doc)


@api_router.put("/books/{book_id}", response_model=Book)
async def update_book(book_id: str, payload: BookUpdate):
    existing = await db.books.find_one({"id": book_id}, {"_id": 0})
    if not existing:
        raise HTTPException(404, "Book not found")
    update_data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if "pages" in update_data:
        # Validate via Page model
        update_data["pages"] = [Page(**p).model_dump() if not isinstance(p, dict) or "id" not in p else Page(**p).model_dump() for p in update_data["pages"]]
    update_data["updated_at"] = _now_iso()
    await db.books.update_one({"id": book_id}, {"$set": update_data})
    doc = await db.books.find_one({"id": book_id}, {"_id": 0})
    # Audit-log: snapshot if last revision is older than the debounce window.
    try:
        await _maybe_snapshot(doc)
    except Exception as e:
        logging.warning(f"Revision snapshot failed for {book_id}: {e}")
    return Book(**doc)


@api_router.get("/books/{book_id}/revisions", response_model=List[BookRevisionSummary])
async def list_revisions(book_id: str):
    book = await db.books.find_one({"id": book_id}, {"_id": 0})
    if not book:
        raise HTTPException(404, "Book not found")
    revs = await db.book_revisions.find(
        {"book_id": book_id}, {"_id": 0}
    ).sort("created_at", -1).to_list(REVISION_MAX_PER_BOOK)
    return [
        BookRevisionSummary(
            id=r["id"],
            book_id=r["book_id"],
            created_at=r["created_at"],
            title=r.get("title", "Untitled Book"),
            page_count=r.get("page_count", 0),
            summary=r.get("summary", ""),
        )
        for r in revs
    ]


@api_router.post("/books/{book_id}/revisions/{revision_id}/restore", response_model=Book)
async def restore_revision(book_id: str, revision_id: str):
    rev = await db.book_revisions.find_one({"id": revision_id, "book_id": book_id}, {"_id": 0})
    if not rev:
        raise HTTPException(404, "Revision not found")
    update_data = {
        "title": rev.get("title", "Untitled Book"),
        "author": rev.get("author", ""),
        "page_size": rev.get("page_size", "a4"),
        "page_number_start": rev.get("page_number_start", 1),
        "is_chapter_book": bool(rev.get("is_chapter_book")),
        "text_presets": rev.get("text_presets"),
        "pages": [Page(**p).model_dump() for p in (rev.get("pages") or [])],
        "updated_at": _now_iso(),
    }
    res = await db.books.update_one({"id": book_id}, {"$set": update_data})
    if res.matched_count == 0:
        raise HTTPException(404, "Book not found")
    doc = await db.books.find_one({"id": book_id}, {"_id": 0})
    # The restore itself is a meaningful event — snapshot immediately, bypassing
    # the debounce window so the user always has a "before restore" checkpoint.
    try:
        snap = BookRevision(
            book_id=book_id,
            title=doc.get("title", "Untitled Book"),
            author=doc.get("author", ""),
            page_size=doc.get("page_size", "a4"),
            page_count=len(doc.get("pages") or []),
            pages=[Page(**p) for p in (doc.get("pages") or [])],
            page_number_start=doc.get("page_number_start", 1),
            is_chapter_book=bool(doc.get("is_chapter_book")),
            text_presets=doc.get("text_presets"),
            summary=f"Restored from {rev.get('created_at', '')[:19].replace('T', ' ')}",
        )
        await db.book_revisions.insert_one(snap.model_dump())
    except Exception as e:
        logging.warning(f"Post-restore snapshot failed: {e}")
    return Book(**doc)


@api_router.get("/books/{book_id}/export.pdf")
async def export_book_pdf(book_id: str, request: Request):
    """Synchronous PDF export — kept for direct/script use. Browsers should
    prefer the job-based flow (`/export/start` → `/export/status` →
    `/export/download`) which is resilient to proxy/CDN response timeouts on
    custom domains."""
    from pdf_builder import build_book_pdf  # local import keeps startup snappy

    book = await db.books.find_one({"id": book_id}, {"_id": 0})
    if not book:
        raise HTTPException(404, "Book not found")
    # Compute the public base URL from the incoming request so Chromium can
    # fall back to fetching images directly when the internal object-storage
    # path fails (the editor proves the public route works from the browser).
    base_url = str(request.base_url).rstrip("/")
    try:
        pdf_bytes = await build_book_pdf(book, get_object, public_base_url=base_url)
    except Exception as e:
        logging.exception("PDF build failed")
        raise HTTPException(500, f"PDF build failed: {e}")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", book.get("title") or "book").strip("_") or "book"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe}.pdf"'},
    )


# ===== PDF export — job-based flow =====
# Job state must be SHARED across backend pods (production runs multiple
# replicas behind a load balancer — a job created on pod A must be
# pollable from pod B). We store status in MongoDB and the PDF bytes in
# object storage, the same way we handle book images.
_PDF_JOB_TTL_SECONDS = 30 * 60  # auto-expire stale jobs after 30 min
_PDF_OBJECT_PREFIX = "booktemplate/pdfjobs"


async def _ensure_pdf_jobs_indexes() -> None:
    """Create the TTL index once so finished jobs auto-purge from Mongo."""
    try:
        await db.pdf_jobs.create_index(
            "expires_at", expireAfterSeconds=0, name="pdf_jobs_ttl"
        )
        await db.pdf_jobs.create_index("job_id", unique=True, name="pdf_jobs_job_id")
    except Exception as e:
        logging.warning(f"pdf_jobs index creation failed (probably already exists): {e}")


async def _run_pdf_job(job_id: str, book_id: str, base_url: str) -> None:
    """Background worker — builds the PDF, uploads bytes to object storage,
    flips the Mongo record to `ready` (or `failed`)."""
    from pdf_builder import build_book_pdf

    try:
        book = await db.books.find_one({"id": book_id}, {"_id": 0})
        if not book:
            await db.pdf_jobs.update_one(
                {"job_id": job_id},
                {"$set": {"status": "failed", "error": "Book not found",
                          "finished_at": _now_iso()}},
            )
            return
        pdf_bytes = await build_book_pdf(book, get_object, public_base_url=base_url)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", book.get("title") or "book").strip("_") or "book"
        # Upload PDF bytes to shared object storage so any pod can serve them.
        object_path = f"{_PDF_OBJECT_PREFIX}/{job_id}.pdf"
        await asyncio.to_thread(put_object, object_path, pdf_bytes, "application/pdf")
        await db.pdf_jobs.update_one(
            {"job_id": job_id},
            {"$set": {
                "status": "ready",
                "size": len(pdf_bytes),
                "filename": f"{safe}.pdf",
                "object_path": object_path,
                "finished_at": _now_iso(),
            }},
        )
    except Exception as e:
        logging.exception("PDF job %s failed", job_id)
        await db.pdf_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "failed", "error": str(e)[:300],
                      "finished_at": _now_iso()}},
        )


@api_router.post("/books/{book_id}/pdf-jobs")
async def export_pdf_start(book_id: str, request: Request):
    """Kick off a background PDF build. Returns a job_id the client polls.
    Each call returns in ~milliseconds — no risk of proxy/CDN timeouts.

    Note: route path deliberately avoids `.pdf` in it because some CDNs/edge
    proxies treat URLs containing `.pdf` as static-file requests and may
    short-circuit them with 404 before they reach the backend.
    """
    # Sanity-check the book exists before we spawn a worker (so the client
    # gets a 404 immediately rather than a "failed" status 20s later).
    exists = await db.books.find_one({"id": book_id}, {"_id": 1})
    if not exists:
        raise HTTPException(404, "Book not found")
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    await db.pdf_jobs.insert_one({
        "job_id": job_id,
        "book_id": book_id,
        "status": "pending",
        "created_at": now.isoformat(),
        # Mongo TTL index uses a real Date — not an ISO string.
        "expires_at": now + timedelta(seconds=_PDF_JOB_TTL_SECONDS),
    })
    asyncio.create_task(_run_pdf_job(job_id, book_id, str(request.base_url).rstrip("/")))
    return {"job_id": job_id, "status": "pending"}


@api_router.get("/books/{book_id}/pdf-jobs/{job_id}")
async def export_pdf_status(book_id: str, job_id: str):
    job = await db.pdf_jobs.find_one(
        {"job_id": job_id, "book_id": book_id}, {"_id": 0}
    )
    if not job:
        raise HTTPException(404, "Job not found")
    resp = {"status": job["status"], "created_at": job["created_at"]}
    if job["status"] == "ready":
        resp["size"] = job.get("size", 0)
        resp["filename"] = job.get("filename", "book.pdf")
    elif job["status"] == "failed":
        resp["error"] = job.get("error") or "PDF build failed"
    return resp


@api_router.get("/books/{book_id}/pdf-jobs/{job_id}/download")
async def export_pdf_download(book_id: str, job_id: str):
    job = await db.pdf_jobs.find_one(
        {"job_id": job_id, "book_id": book_id}, {"_id": 0}
    )
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "ready":
        raise HTTPException(409, f"Job is {job['status']}, not ready for download")
    object_path = job.get("object_path")
    filename = job.get("filename", "book.pdf")
    if not object_path:
        raise HTTPException(500, "Job is ready but has no storage path")
    try:
        pdf_bytes, _ = await asyncio.to_thread(get_object, object_path)
    except Exception as e:
        logging.exception("PDF download fetch failed")
        raise HTTPException(500, f"PDF retrieval failed: {e}")
    # Consume the job: drop the Mongo record so the user can't re-download by
    # replaying the URL. (Object storage entry expires via the TTL anyway.)
    await db.pdf_jobs.delete_one({"job_id": job_id})
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )




@api_router.post("/books/{book_id}/duplicate", response_model=Book)
async def duplicate_book(book_id: str):
    src = await db.books.find_one({"id": book_id}, {"_id": 0})
    if not src:
        raise HTTPException(404, "Book not found")
    new_pages: List[Page] = []
    for p in src.get("pages", []):
        page_obj = Page(**p)
        page_obj.id = str(uuid.uuid4())
        new_blocks: List[Block] = []
        for b in page_obj.blocks:
            block_obj = Block(**b.model_dump())
            block_obj.id = str(uuid.uuid4())
            new_blocks.append(block_obj)
        page_obj.blocks = new_blocks
        new_pages.append(page_obj)
    copy = Book(
        title=f"{src.get('title', 'Untitled Book')} (copy)",
        author=src.get("author", ""),
        page_size=src.get("page_size", "a4"),
        text_presets=src.get("text_presets"),
        pages=new_pages,
    )
    await db.books.insert_one(copy.model_dump())
    # Clone asset records (point to same storage paths) so the duplicate's
    # Assets panel mirrors the original book's library.
    src_assets = await db.files.find({"book_id": book_id, "is_deleted": False}).to_list(2000)
    for a in src_assets:
        await db.files.insert_one({
            "id": str(uuid.uuid4()),
            "storage_path": a["storage_path"],
            "original_filename": a.get("original_filename"),
            "content_type": a.get("content_type"),
            "size": a.get("size"),
            "is_deleted": False,
            "created_at": _now_iso(),
            "book_id": copy.id,
        })
    return copy


@api_router.delete("/books/{book_id}")
async def delete_book(book_id: str):
    res = await db.books.delete_one({"id": book_id})
    if res.deleted_count == 0:
        raise HTTPException(404, "Book not found")
    # Soft-delete asset records scoped to this book so they no longer surface
    # in any asset panel (other books are unaffected — assets are now book-scoped).
    await db.files.update_many({"book_id": book_id}, {"$set": {"is_deleted": True}})
    return {"deleted": True}


@api_router.get("/templates", response_model=List[Template])
async def list_templates():
    docs = await db.templates.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return [Template(**d) for d in docs]


@api_router.post("/templates", response_model=Template)
async def create_template(payload: TemplateCreate):
    template = Template(
        name=payload.name,
        page_size=payload.page_size,
        cover=payload.cover,
        interior=payload.interior,
        back_cover=payload.back_cover,
        text_presets=payload.text_presets,
    )
    await db.templates.insert_one(template.model_dump())
    return template


@api_router.delete("/templates/{template_id}")
async def delete_template(template_id: str):
    res = await db.templates.delete_one({"id": template_id})
    if res.deleted_count == 0:
        raise HTTPException(404, "Template not found")
    return {"deleted": True}




@api_router.post("/upload")
async def upload_image(file: UploadFile = File(...), book_id: Optional[str] = Form(None)):
    ext = (file.filename or "bin").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "bin"
    content_type = file.content_type or MIME_TYPES.get(ext, "application/octet-stream")
    if not content_type.startswith("image/"):
        raise HTTPException(400, "Only image uploads are supported")
    path = f"{APP_NAME}/uploads/{uuid.uuid4()}.{ext}"
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 10MB)")
    result = put_object(path, data, content_type)
    canonical_path = result["path"]
    await db.files.insert_one({
        "id": str(uuid.uuid4()),
        "storage_path": canonical_path,
        "original_filename": file.filename,
        "content_type": content_type,
        "size": result.get("size", len(data)),
        "is_deleted": False,
        "created_at": _now_iso(),
        "book_id": book_id,
    })
    return {
        "path": canonical_path,
        "url": f"/api/files/{canonical_path}",
        "content_type": content_type,
        "size": result.get("size", len(data)),
    }


@api_router.get("/assets")
async def list_assets(book_id: Optional[str] = None):
    query: dict = {"is_deleted": False}
    if book_id:
        query["book_id"] = book_id
    docs = await db.files.find(
        query,
        {"_id": 0, "id": 1, "storage_path": 1, "original_filename": 1, "content_type": 1, "size": 1, "created_at": 1, "book_id": 1},
    ).sort("created_at", -1).to_list(2000)
    return [
        {
            "id": d["id"],
            "path": d["storage_path"],
            "url": f"/api/files/{d['storage_path']}",
            "original_filename": d.get("original_filename"),
            "content_type": d.get("content_type"),
            "size": d.get("size"),
            "created_at": d.get("created_at"),
            "book_id": d.get("book_id"),
        }
        for d in docs
    ]


@api_router.delete("/assets/{asset_id}")
async def delete_asset(asset_id: str):
    res = await db.files.update_one(
        {"id": asset_id, "is_deleted": False},
        {"$set": {"is_deleted": True}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Asset not found")
    return {"deleted": True}


@api_router.get("/files/{path:path}")
async def serve_file(path: str):
    record = await db.files.find_one({"storage_path": path, "is_deleted": False})
    if not record:
        raise HTTPException(404, "File not found")
    data, content_type = get_object(path)
    return Response(
        content=data,
        media_type=record.get("content_type", content_type),
        headers={"Cache-Control": "public, max-age=31536000"},
    )


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
