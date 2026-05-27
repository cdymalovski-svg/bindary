"""Backend API tests for Assets (list + soft delete)."""
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


def _upload(s, name="TEST_asset.png"):
    files = {"file": (name, io.BytesIO(PNG_BYTES), "image/png")}
    r = s.post(f"{API}/upload", files=files)
    assert r.status_code == 200, r.text
    return r.json()


class TestAssetsAPI:
    def test_list_assets_shape(self, s):
        # Ensure at least one asset present
        _upload(s, "TEST_asset_shape.png")
        r = s.get(f"{API}/assets")
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        assert len(items) >= 1
        sample = items[0]
        for k in ("id", "url", "path", "original_filename"):
            assert k in sample, f"Missing key {k}"
        assert sample["url"].startswith("/api/files/")

    def test_upload_then_list_contains_new_asset(self, s):
        meta = _upload(s, "TEST_asset_listed.png")
        r = s.get(f"{API}/assets")
        assert r.status_code == 200
        items = r.json()
        match = [a for a in items if a["path"] == meta["path"]]
        assert len(match) == 1
        assert match[0]["original_filename"] == "TEST_asset_listed.png"
        assert match[0]["url"] == f"/api/files/{meta['path']}"

    def test_delete_asset_soft_deletes(self, s):
        meta = _upload(s, "TEST_asset_delete.png")
        # find id from list
        items = s.get(f"{API}/assets").json()
        a = next(x for x in items if x["path"] == meta["path"])
        asset_id = a["id"]

        # delete
        r = s.delete(f"{API}/assets/{asset_id}")
        assert r.status_code == 200
        assert r.json().get("deleted")

        # subsequent list excludes it
        items2 = s.get(f"{API}/assets").json()
        assert not any(x["id"] == asset_id for x in items2), "Deleted asset still listed"

        # serving the file should 404 (soft-deleted)
        r3 = s.get(f"{BASE_URL}{meta['url']}")
        assert r3.status_code == 404

    def test_delete_nonexistent_asset(self, s):
        r = s.delete(f"{API}/assets/nonexistent-xyz-123")
        assert r.status_code == 404

    def test_delete_already_deleted(self, s):
        meta = _upload(s, "TEST_asset_double_delete.png")
        items = s.get(f"{API}/assets").json()
        asset_id = next(x for x in items if x["path"] == meta["path"])["id"]
        r1 = s.delete(f"{API}/assets/{asset_id}")
        assert r1.status_code == 200
        r2 = s.delete(f"{API}/assets/{asset_id}")
        assert r2.status_code == 404


# A second, distinguishable PNG (2x1 red pixel) so we can verify that
# replace() actually overwrote the bytes at the storage path.
PNG_BYTES_ALT = bytes.fromhex(
    "89504E470D0A1A0A0000000D4948445200000002000000010806000000996DAB"
    "2D000000164944415478DA63F8CFC0F09F8101300C0203000300010000000005"
    "00010D0A2DB40000000049454E44AE426082"
)


class TestAssetReplace:
    """The replace endpoint is the recovery path for orphaned assets
    (DB record exists but the storage bytes are gone). It MUST keep the
    same id + storage_path so that every block in every book that
    references the asset's URL keeps working without an edit."""

    def test_replace_preserves_id_and_path(self, s):
        meta = _upload(s, "TEST_replace_same_path.png")
        items = s.get(f"{API}/assets").json()
        asset = next(x for x in items if x["path"] == meta["path"])
        asset_id, original_path = asset["id"], asset["path"]

        files = {"file": ("new.png", io.BytesIO(PNG_BYTES_ALT), "image/png")}
        r = s.post(f"{API}/assets/{asset_id}/replace", files=files)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == asset_id
        assert body["path"] == original_path  # critical: same path
        # Cache-buster ensures browsers re-fetch.
        assert "?v=" in body["url"]

        # The DB record's filename should have been updated.
        items2 = s.get(f"{API}/assets").json()
        updated = next(x for x in items2 if x["id"] == asset_id)
        assert updated["original_filename"] == "new.png"

        # The bytes served at the original /api/files/<path> URL must now
        # be the replacement bytes — the proof that we overwrote in place.
        r2 = s.get(f"{BASE_URL}{meta['url']}")
        assert r2.status_code == 200
        assert r2.content == PNG_BYTES_ALT

    def test_replace_nonexistent_asset(self, s):
        files = {"file": ("x.png", io.BytesIO(PNG_BYTES), "image/png")}
        r = s.post(f"{API}/assets/does-not-exist/replace", files=files)
        assert r.status_code == 404

    def test_replace_rejects_non_image(self, s):
        meta = _upload(s, "TEST_replace_reject_type.png")
        items = s.get(f"{API}/assets").json()
        asset_id = next(x for x in items if x["path"] == meta["path"])["id"]
        files = {"file": ("bad.txt", io.BytesIO(b"hello"), "text/plain")}
        r = s.post(f"{API}/assets/{asset_id}/replace", files=files)
        assert r.status_code == 400
