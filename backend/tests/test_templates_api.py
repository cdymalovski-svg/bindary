# Tests for the templates API (iteration 8)
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fall back to frontend env file
    try:
        with open("/app/frontend/.env") as fh:
            for line in fh:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                    break
    except Exception:
        pass

API = f"{BASE_URL}/api"


@pytest.fixture
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture
def cleanup(client):
    created = []
    yield created
    for tid in created:
        try:
            client.delete(f"{API}/templates/{tid}", timeout=10)
        except Exception:
            pass


def _sample_payload(name="TEST_Iter8_Tpl"):
    return {
        "name": name,
        "page_size": "a4",
        "cover": {
            "background_color": "#CFE3DC",
            "full_bleed": False,
            "show_page_number": True,
            "page_number_align": "center",
            "page_number_size": 16,
        },
        "interior": {
            "background_color": "#F5EFE0",
            "full_bleed": False,
            "show_page_number": True,
            "page_number_align": "right",
            "page_number_size": 14,
        },
        "back_cover": {
            "background_color": "#3A3833",
            "full_bleed": True,
            "show_page_number": False,
            "page_number_align": "right",
            "page_number_size": 14,
        },
    }


class TestTemplatesAPI:
    def test_list_returns_array(self, client):
        r = client.get(f"{API}/templates", timeout=15)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_create_and_get(self, client, cleanup):
        payload = _sample_payload()
        r = client.post(f"{API}/templates", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "id" in body
        assert "created_at" in body
        assert body["name"] == payload["name"]
        assert body["page_size"] == "a4"
        assert body["cover"]["background_color"] == "#CFE3DC"
        assert body["interior"]["background_color"] == "#F5EFE0"
        assert body["back_cover"]["background_color"] == "#3A3833"
        assert body["back_cover"]["full_bleed"] is True
        assert body["back_cover"]["show_page_number"] is False
        cleanup.append(body["id"])

        # GET list should contain it
        listing = client.get(f"{API}/templates", timeout=15).json()
        ids = [t["id"] for t in listing]
        assert body["id"] in ids

    def test_list_sorted_desc(self, client, cleanup):
        a = client.post(f"{API}/templates", json=_sample_payload("TEST_Iter8_Older"), timeout=15).json()
        cleanup.append(a["id"])
        b = client.post(f"{API}/templates", json=_sample_payload("TEST_Iter8_Newer"), timeout=15).json()
        cleanup.append(b["id"])
        listing = client.get(f"{API}/templates", timeout=15).json()
        # find indices
        ids = [t["id"] for t in listing]
        assert ids.index(b["id"]) < ids.index(a["id"]), "Newer template should be first"

    def test_delete_removes(self, client):
        created = client.post(f"{API}/templates", json=_sample_payload("TEST_Iter8_Del"), timeout=15).json()
        tid = created["id"]
        d = client.delete(f"{API}/templates/{tid}", timeout=15)
        assert d.status_code == 200
        listing = client.get(f"{API}/templates", timeout=15).json()
        assert tid not in [t["id"] for t in listing]

    def test_delete_nonexistent_404(self, client):
        d = client.delete(f"{API}/templates/nonexistent-id-zzz", timeout=15)
        assert d.status_code == 404

    def test_create_book_then_apply_template_pages(self, client, cleanup):
        """Simulate frontend flow: create book then PUT 3 pages from template."""
        tpl = client.post(f"{API}/templates", json=_sample_payload("TEST_Iter8_FlowTpl"), timeout=15).json()
        cleanup.append(tpl["id"])
        book = client.post(f"{API}/books", json={"title": "TEST_Iter8_BookFromTpl", "page_size": "a4"}, timeout=15).json()
        # Build 3 pages mirroring Dashboard.onCreate
        def make(s):
            return {
                "id": "p-" + s["background_color"],
                "blocks": [],
                "background_color": s["background_color"],
                "full_bleed": bool(s.get("full_bleed", False)),
                "show_page_number": bool(s.get("show_page_number", True)),
                "page_number_align": s.get("page_number_align", "right"),
                "page_number_size": s.get("page_number_size", 14),
            }
        pages = [make(tpl["cover"]), make(tpl["interior"]), make(tpl["back_cover"])]
        put = client.put(
            f"{API}/books/{book['id']}",
            json={"pages": pages, "page_size": tpl["page_size"]},
            timeout=15,
        )
        assert put.status_code == 200
        # GET should have 3 pages with correct colors
        got = client.get(f"{API}/books/{book['id']}", timeout=15).json()
        assert len(got["pages"]) == 3
        assert got["pages"][0]["background_color"] == "#CFE3DC"
        assert got["pages"][1]["background_color"] == "#F5EFE0"
        assert got["pages"][2]["background_color"] == "#3A3833"
        assert got["pages"][2]["full_bleed"] is True
        # Cleanup book
        client.delete(f"{API}/books/{book['id']}", timeout=15)
