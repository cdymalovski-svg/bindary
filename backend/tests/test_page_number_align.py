"""Backend tests verifying page_number_align field persistence and Block default color."""
import os
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL').rstrip('/')
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def s():
    return requests.Session()


@pytest.fixture(scope="module")
def created_book(s):
    r = s.post(f"{API}/books", json={"title": "TEST_PNAlign", "author": "TEST", "page_size": "a4"})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    yield bid
    s.delete(f"{API}/books/{bid}")


def test_default_page_number_align_is_right(s, created_book):
    r = s.get(f"{API}/books/{created_book}")
    assert r.status_code == 200
    data = r.json()
    assert data["pages"][0].get("page_number_align") == "right"


def test_update_page_number_align_persists(s, created_book):
    book = s.get(f"{API}/books/{created_book}").json()
    pid = book["pages"][0]["id"]
    new_pages = [
        {
            "id": pid,
            "blocks": [],
            "background_color": "#F9F6F0",
            "show_page_number": True,
            "page_number_align": "center",
        }
    ]
    r = s.put(f"{API}/books/{created_book}", json={"pages": new_pages})
    assert r.status_code == 200, r.text
    upd = r.json()
    assert upd["pages"][0]["page_number_align"] == "center"

    # GET to confirm persistence
    r2 = s.get(f"{API}/books/{created_book}")
    assert r2.json()["pages"][0]["page_number_align"] == "center"


def test_update_page_number_align_left(s, created_book):
    book = s.get(f"{API}/books/{created_book}").json()
    pid = book["pages"][0]["id"]
    new_pages = [
        {
            "id": pid,
            "blocks": [],
            "background_color": "#F9F6F0",
            "show_page_number": True,
            "page_number_align": "left",
        }
    ]
    r = s.put(f"{API}/books/{created_book}", json={"pages": new_pages})
    assert r.status_code == 200
    assert r.json()["pages"][0]["page_number_align"] == "left"


def test_default_block_color_is_black(s, created_book):
    """When a text block is saved without explicit color, the Block model default should be '#000000'."""
    book = s.get(f"{API}/books/{created_book}").json()
    pid = book["pages"][0]["id"]
    new_pages = [
        {
            "id": pid,
            "blocks": [
                {"id": "blk-default-color", "type": "text", "x": 10, "y": 10, "width": 200, "height": 100,
                 "html": "<p>hi</p>"}
            ],
            "background_color": "#F9F6F0",
            "show_page_number": True,
        }
    ]
    r = s.put(f"{API}/books/{created_book}", json={"pages": new_pages})
    assert r.status_code == 200, r.text
    upd = r.json()
    assert upd["pages"][0]["blocks"][0]["color"] == "#000000"
