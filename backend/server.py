from fastapi import FastAPI, APIRouter, UploadFile, File, HTTPException, Response
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import uuid
import requests
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Any
from datetime import datetime, timezone


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


class Page(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    blocks: List[Block] = []
    background_color: Optional[str] = "#FFF8DC"
    show_page_number: bool = True
    page_number_align: Optional[str] = "right"  # 'left' | 'center' | 'right'
    page_number_size: Optional[int] = 14


class Book(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = "Untitled Book"
    author: Optional[str] = ""
    page_size: str = "a4"  # a4 | letter | square | book6x9
    pages: List[Page] = []
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
    pages: Optional[List[Page]] = None


class BookSummary(BaseModel):
    id: str
    title: str
    author: Optional[str] = ""
    page_size: str
    page_count: int
    updated_at: str
    cover_image_url: Optional[str] = None


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


@api_router.get("/books", response_model=List[BookSummary])
async def list_books():
    books = await db.books.find({}, {"_id": 0}).sort("updated_at", -1).to_list(1000)
    summaries: List[BookSummary] = []
    for b in books:
        cover_url = None
        pages = b.get("pages") or []
        if pages:
            for blk in pages[0].get("blocks", []):
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
    return Book(**doc)


@api_router.delete("/books/{book_id}")
async def delete_book(book_id: str):
    res = await db.books.delete_one({"id": book_id})
    if res.deleted_count == 0:
        raise HTTPException(404, "Book not found")
    return {"deleted": True}


@api_router.post("/upload")
async def upload_image(file: UploadFile = File(...)):
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
    })
    return {
        "path": canonical_path,
        "url": f"/api/files/{canonical_path}",
        "content_type": content_type,
        "size": result.get("size", len(data)),
    }


@api_router.get("/assets")
async def list_assets():
    docs = await db.files.find(
        {"is_deleted": False},
        {"_id": 0, "id": 1, "storage_path": 1, "original_filename": 1, "content_type": 1, "size": 1, "created_at": 1},
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
