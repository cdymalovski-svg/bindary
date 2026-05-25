"""Iter-10: Book-scoped assets.

Validates:
  * POST /api/upload with book_id form field stores book_id
  * GET /api/assets?book_id=... filters
  * POST /api/books/{id}/duplicate clones asset records (same storage_path)
  * DELETE /api/books/{id} soft-deletes that book's asset records (others unaffected)
"""
import io
import os
import pytest
import requests

BASE_URL = os.environ['REACT_APP_BACKEND_URL'].rstrip('/')
API = f"{BASE_URL}/api"

PNG_BYTES = bytes.fromhex(
    "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
    "0000000A49444154789C6300010000000500010D0A2DB40000000049454E44AE426082"
)


@pytest.fixture(scope="module")
def s():
    return requests.Session()


def _create_book(s, title):
    r = s.post(f"{API}/books", json={"title": title, "page_size": "a4"})
    assert r.status_code == 200, r.text
    return r.json()


def _upload(s, name, book_id=None):
    files = {"file": (name, io.BytesIO(PNG_BYTES), "image/png")}
    data = {"book_id": book_id} if book_id else None
    r = s.post(f"{API}/upload", files=files, data=data)
    assert r.status_code == 200, r.text
    return r.json()


def _list_assets(s, book_id=None):
    params = {"book_id": book_id} if book_id else None
    r = s.get(f"{API}/assets", params=params)
    assert r.status_code == 200, r.text
    return r.json()


class TestBookScopedAssets:
    """Book A vs Book B isolation"""

    def test_upload_with_book_id_scopes_assets(self, s):
        book_a = _create_book(s, "TEST_BookA_iter10")
        book_b = _create_book(s, "TEST_BookB_iter10")

        a1 = _upload(s, "TEST_a1.png", book_id=book_a["id"])
        a2 = _upload(s, "TEST_a2.png", book_id=book_a["id"])
        b1 = _upload(s, "TEST_b1.png", book_id=book_b["id"])

        # Book A sees only its 2 assets
        a_assets = _list_assets(s, book_id=book_a["id"])
        a_paths = {x["path"] for x in a_assets}
        assert a1["path"] in a_paths
        assert a2["path"] in a_paths
        assert b1["path"] not in a_paths
        # ensure all returned have correct book_id
        for x in a_assets:
            assert x["book_id"] == book_a["id"]

        # Book B sees only its 1 asset
        b_assets = _list_assets(s, book_id=book_b["id"])
        b_paths = {x["path"] for x in b_assets}
        assert b1["path"] in b_paths
        assert a1["path"] not in b_paths
        assert a2["path"] not in b_paths

    def test_upload_without_book_id_legacy(self, s):
        meta = _upload(s, "TEST_legacy.png", book_id=None)
        # Should not appear in any scoped book panel
        random_book = _create_book(s, "TEST_RandomBook_iter10")
        scoped = _list_assets(s, book_id=random_book["id"])
        assert meta["path"] not in {x["path"] for x in scoped}
        # But appears in the unfiltered admin list
        admin = _list_assets(s)
        assert meta["path"] in {x["path"] for x in admin}

    def test_duplicate_book_clones_asset_records(self, s):
        src = _create_book(s, "TEST_DupSrc_iter10")
        u1 = _upload(s, "TEST_dup1.png", book_id=src["id"])
        u2 = _upload(s, "TEST_dup2.png", book_id=src["id"])

        r = s.post(f"{API}/books/{src['id']}/duplicate")
        assert r.status_code == 200, r.text
        dup = r.json()
        assert dup["id"] != src["id"]

        dup_assets = _list_assets(s, book_id=dup["id"])
        dup_paths = sorted(x["path"] for x in dup_assets)
        src_paths = sorted([u1["path"], u2["path"]])
        assert dup_paths == src_paths, f"expected {src_paths} got {dup_paths}"
        # source still has its own assets
        src_assets = _list_assets(s, book_id=src["id"])
        assert sorted(x["path"] for x in src_assets) == src_paths
        # different record ids though
        src_ids = {x["id"] for x in src_assets}
        dup_ids = {x["id"] for x in dup_assets}
        assert src_ids.isdisjoint(dup_ids)

    def test_delete_book_soft_deletes_its_assets(self, s):
        a = _create_book(s, "TEST_DelA_iter10")
        b = _create_book(s, "TEST_DelB_iter10")
        ua = _upload(s, "TEST_dela.png", book_id=a["id"])
        ub = _upload(s, "TEST_delb.png", book_id=b["id"])

        # sanity
        assert any(x["path"] == ua["path"] for x in _list_assets(s, book_id=a["id"]))

        r = s.delete(f"{API}/books/{a['id']}")
        assert r.status_code == 200
        assert r.json().get("deleted")

        # A's assets no longer surface
        after = _list_assets(s, book_id=a["id"])
        assert all(x["path"] != ua["path"] for x in after)
        assert len(after) == 0
        # B's assets unaffected
        b_after = _list_assets(s, book_id=b["id"])
        assert any(x["path"] == ub["path"] for x in b_after)

        # serving the deleted asset file should 404
        r2 = s.get(f"{BASE_URL}{ua['url']}")
        assert r2.status_code == 404

    def test_assets_list_legacy_regression(self, s):
        # GET /api/assets (no query) still works
        r = s.get(f"{API}/assets")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
