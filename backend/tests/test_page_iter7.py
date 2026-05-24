"""Backend tests for iter-7 full_bleed Page field."""
import os
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://page-builder-540.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def s():
    return requests.Session()


@pytest.fixture(scope="module")
def book(s):
    r = s.post(f"{API}/books", json={"title": "TEST_Iter7_FullBleed", "page_size": "a4"})
    assert r.status_code == 200, r.text
    b = r.json()
    yield b
    # teardown
    try:
        s.delete(f"{API}/books/{b['id']}")
    except Exception:
        pass


class TestFullBleed:
    def test_default_full_bleed_false_on_create(self, book):
        # POST /api/books → page should have full_bleed=false (or absent treated as false)
        pages = book.get("pages") or []
        assert len(pages) >= 1
        # Pydantic dumps the default, so field should be present and False
        assert pages[0].get("full_bleed") is False, (
            f"Expected default full_bleed=False, got {pages[0].get('full_bleed')}"
        )

    def test_get_book_returns_full_bleed_false_default(self, s, book):
        r = s.get(f"{API}/books/{book['id']}")
        assert r.status_code == 200
        d = r.json()
        assert d["pages"][0].get("full_bleed") is False

    def test_put_accepts_full_bleed_true_and_persists(self, s, book):
        # PUT to set full_bleed=True on page 0
        page0 = book["pages"][0]
        new_pages = [{
            "id": page0["id"],
            "blocks": [],
            "background_color": "#FFF8DC",
            "show_page_number": True,
            "full_bleed": True,
        }]
        r = s.put(f"{API}/books/{book['id']}", json={"pages": new_pages})
        assert r.status_code == 200, r.text
        upd = r.json()
        assert upd["pages"][0]["full_bleed"] is True

        # Persistence: GET back the book
        r2 = s.get(f"{API}/books/{book['id']}")
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["pages"][0]["full_bleed"] is True

    def test_put_can_set_back_to_false(self, s, book):
        page0_id = book["pages"][0]["id"]
        new_pages = [{
            "id": page0_id,
            "blocks": [],
            "background_color": "#FFF8DC",
            "show_page_number": True,
            "full_bleed": False,
        }]
        r = s.put(f"{API}/books/{book['id']}", json={"pages": new_pages})
        assert r.status_code == 200
        assert r.json()["pages"][0]["full_bleed"] is False

    def test_full_bleed_independent_per_page(self, s, book):
        # Create two pages: page A bleed=true, page B bleed=false
        page0_id = book["pages"][0]["id"]
        new_pages = [
            {"id": page0_id, "blocks": [], "background_color": "#FFF8DC", "full_bleed": True},
            {"id": "p2", "blocks": [], "background_color": "#FFFFFF", "full_bleed": False},
        ]
        r = s.put(f"{API}/books/{book['id']}", json={"pages": new_pages})
        assert r.status_code == 200, r.text
        d = r.json()
        assert len(d["pages"]) == 2
        assert d["pages"][0]["full_bleed"] is True
        assert d["pages"][1]["full_bleed"] is False

    def test_full_bleed_missing_field_in_put_keeps_default_false(self, s, book):
        # When client doesn't send full_bleed at all, Page() default should make it False
        page0_id = book["pages"][0]["id"]
        new_pages = [{
            "id": page0_id,
            "blocks": [],
            "background_color": "#FFF8DC",
            "show_page_number": True,
        }]
        r = s.put(f"{API}/books/{book['id']}", json={"pages": new_pages})
        assert r.status_code == 200
        assert r.json()["pages"][0]["full_bleed"] is False
