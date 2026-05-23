"""Backend API tests for Book Template app - books CRUD + upload."""
import io
import os
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://page-builder-540.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    return sess


# ---- Books CRUD ----
class TestBooksCRUD:
    created_id = None

    def test_create_book(self, s):
        r = s.post(f"{API}/books", json={"title": "TEST_Book_1", "author": "TEST_Author", "page_size": "letter"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert "id" in data
        assert data["title"] == "TEST_Book_1"
        assert data["author"] == "TEST_Author"
        assert data["page_size"] == "letter"
        assert isinstance(data["pages"], list) and len(data["pages"]) >= 1
        assert "id" in data["pages"][0]
        TestBooksCRUD.created_id = data["id"]

    def test_list_books(self, s):
        r = s.get(f"{API}/books")
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        ours = [b for b in items if b["id"] == TestBooksCRUD.created_id]
        assert len(ours) == 1
        b = ours[0]
        assert b["title"] == "TEST_Book_1"
        assert b["page_count"] >= 1
        assert "updated_at" in b

    def test_get_book(self, s):
        r = s.get(f"{API}/books/{TestBooksCRUD.created_id}")
        assert r.status_code == 200
        b = r.json()
        assert b["id"] == TestBooksCRUD.created_id
        assert "pages" in b
        assert isinstance(b["pages"][0].get("blocks"), list)

    def test_update_book(self, s):
        before = s.get(f"{API}/books/{TestBooksCRUD.created_id}").json()
        new_pages = [
            {
                "id": before["pages"][0]["id"],
                "blocks": [
                    {"id": "b1", "type": "text", "x": 10, "y": 10, "width": 200, "height": 100,
                     "html": "<p>Hello</p>", "font_size": 20}
                ],
                "background_color": "#F9F6F0",
                "show_page_number": True
            },
            {
                "id": "p2",
                "blocks": [],
                "background_color": "#F9F6F0",
                "show_page_number": True
            }
        ]
        r = s.put(f"{API}/books/{TestBooksCRUD.created_id}", json={
            "title": "TEST_Book_Updated",
            "page_size": "a4",
            "pages": new_pages
        })
        assert r.status_code == 200, r.text
        upd = r.json()
        assert upd["title"] == "TEST_Book_Updated"
        assert upd["page_size"] == "a4"
        assert len(upd["pages"]) == 2
        assert upd["pages"][0]["blocks"][0]["html"] == "<p>Hello</p>"
        assert upd["updated_at"] != before["updated_at"]

        # GET to confirm persistence
        r2 = s.get(f"{API}/books/{TestBooksCRUD.created_id}")
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["title"] == "TEST_Book_Updated"
        assert len(d2["pages"]) == 2
        assert d2["pages"][0]["blocks"][0]["html"] == "<p>Hello</p>"

    def test_delete_book(self, s):
        r = s.delete(f"{API}/books/{TestBooksCRUD.created_id}")
        assert r.status_code == 200
        assert r.json().get("deleted") is True

        # Verify 404
        r2 = s.get(f"{API}/books/{TestBooksCRUD.created_id}")
        assert r2.status_code == 404

    def test_get_nonexistent_book(self, s):
        r = s.get(f"{API}/books/nonexistent-id-xyz")
        assert r.status_code == 404


# ---- Upload ----
class TestUpload:
    def test_upload_and_fetch_image(self, s):
        # 1x1 PNG
        png = bytes.fromhex(
            "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
            "0000000A49444154789C6300010000000500010D0A2DB40000000049454E44AE426082"
        )
        files = {"file": ("test.png", io.BytesIO(png), "image/png")}
        r = s.post(f"{API}/upload", files=files)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "path" in data and "url" in data
        assert data["content_type"] == "image/png"
        assert data["size"] > 0
        assert data["url"].startswith("/api/files/")

        # Fetch
        fetch_url = f"{BASE_URL}{data['url']}"
        r2 = s.get(fetch_url)
        assert r2.status_code == 200
        assert r2.headers.get("Content-Type", "").startswith("image/")
        assert len(r2.content) > 0

    def test_upload_rejects_non_image(self, s):
        files = {"file": ("a.txt", io.BytesIO(b"hello"), "text/plain")}
        r = s.post(f"{API}/upload", files=files)
        assert r.status_code == 400
