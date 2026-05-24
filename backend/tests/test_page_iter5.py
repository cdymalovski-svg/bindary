"""Iteration 5 backend tests:
- Default page background_color == '#FFF8DC'
- Default page_number_size == 14
- PUT can persist updated page_number_size
- Layer changes (z_index) persist via PUT
- Default text block color remains '#000000'
"""
import os
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://book-builder-69.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def s():
    return requests.Session()


@pytest.fixture(scope="module")
def book_id(s):
    r = s.post(f"{API}/books", json={"title": "TEST_Iter5_Book", "author": "T", "page_size": "a4"})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    yield bid
    # cleanup
    s.delete(f"{API}/books/{bid}")


# --- Defaults on creation ---
class TestPageDefaults:
    def test_default_background_is_light_yellow(self, s, book_id):
        r = s.get(f"{API}/books/{book_id}")
        assert r.status_code == 200
        data = r.json()
        assert len(data["pages"]) >= 1
        page = data["pages"][0]
        assert page["background_color"] == "#FFF8DC", page

    def test_default_page_number_size_is_14(self, s, book_id):
        r = s.get(f"{API}/books/{book_id}")
        assert r.status_code == 200
        page = r.json()["pages"][0]
        assert page.get("page_number_size") == 14, page

    def test_default_show_page_number_true(self, s, book_id):
        r = s.get(f"{API}/books/{book_id}")
        page = r.json()["pages"][0]
        assert page.get("show_page_number") is True

    def test_default_page_number_align_right(self, s, book_id):
        r = s.get(f"{API}/books/{book_id}")
        page = r.json()["pages"][0]
        assert page.get("page_number_align") == "right"


# --- Update + persistence ---
class TestPageNumberSizeUpdate:
    def test_put_page_number_size_24(self, s, book_id):
        # fetch current
        r = s.get(f"{API}/books/{book_id}")
        book = r.json()
        pages = book["pages"]
        pages[0]["page_number_size"] = 24
        r2 = s.put(f"{API}/books/{book_id}", json={"pages": pages})
        assert r2.status_code == 200, r2.text
        assert r2.json()["pages"][0]["page_number_size"] == 24

        # GET to verify persistence
        r3 = s.get(f"{API}/books/{book_id}")
        assert r3.json()["pages"][0]["page_number_size"] == 24

    def test_put_page_number_size_32(self, s, book_id):
        r = s.get(f"{API}/books/{book_id}")
        pages = r.json()["pages"]
        pages[0]["page_number_size"] = 32
        r2 = s.put(f"{API}/books/{book_id}", json={"pages": pages})
        assert r2.status_code == 200
        r3 = s.get(f"{API}/books/{book_id}")
        assert r3.json()["pages"][0]["page_number_size"] == 32


# --- Layer / z_index persistence ---
class TestLayerZIndex:
    def test_blocks_z_index_persist(self, s, book_id):
        r = s.get(f"{API}/books/{book_id}")
        book = r.json()
        pages = book["pages"]
        # add two text blocks with different z_index values
        pages[0]["blocks"] = [
            {
                "id": "blk-a", "type": "text", "x": 10, "y": 10, "width": 200, "height": 80,
                "z_index": 1, "html": "<p>A</p>", "color": "#000000",
            },
            {
                "id": "blk-b", "type": "text", "x": 50, "y": 50, "width": 200, "height": 80,
                "z_index": 5, "html": "<p>B</p>", "color": "#000000",
            },
        ]
        r2 = s.put(f"{API}/books/{book_id}", json={"pages": pages})
        assert r2.status_code == 200, r2.text
        out_blocks = r2.json()["pages"][0]["blocks"]
        zmap = {b["id"]: b["z_index"] for b in out_blocks}
        assert zmap["blk-a"] == 1
        assert zmap["blk-b"] == 5

        # GET re-verify
        r3 = s.get(f"{API}/books/{book_id}")
        zmap2 = {b["id"]: b["z_index"] for b in r3.json()["pages"][0]["blocks"]}
        assert zmap2["blk-a"] == 1
        assert zmap2["blk-b"] == 5

    def test_negative_z_index_supported(self, s, book_id):
        r = s.get(f"{API}/books/{book_id}")
        pages = r.json()["pages"]
        # set one block to negative z (send-to-back)
        for b in pages[0]["blocks"]:
            if b["id"] == "blk-a":
                b["z_index"] = -3
        r2 = s.put(f"{API}/books/{book_id}", json={"pages": pages})
        assert r2.status_code == 200
        zmap = {b["id"]: b["z_index"] for b in r2.json()["pages"][0]["blocks"]}
        assert zmap["blk-a"] == -3


# --- Regression: defaults on text block via Block model ---
class TestTextBlockColorDefault:
    def test_block_default_color_black(self, s, book_id):
        r = s.get(f"{API}/books/{book_id}")
        pages = r.json()["pages"]
        # Append a new block with only required fields - rely on backend default for color
        pages[0]["blocks"].append({"type": "text", "x": 100, "y": 100, "width": 100, "height": 40})
        r2 = s.put(f"{API}/books/{book_id}", json={"pages": pages})
        assert r2.status_code == 200, r2.text
        last_block = r2.json()["pages"][0]["blocks"][-1]
        assert last_block.get("color") == "#000000", last_block
