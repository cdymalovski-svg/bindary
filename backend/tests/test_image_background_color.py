"""Image-block background-fill rendering.

Validates that:
  • The `background_color` field is preserved through the Block model.
  • `_render_block` emits a `background-color:` CSS declaration on the
    wrapper div when the field is set.
  • A block with `background_color` but no `image_url` still renders
    (colour-tile mode) instead of being silently dropped.
"""
from __future__ import annotations

import os
import sys
import uuid

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdf_builder import _render_block  # noqa: E402

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL", "https://manuscript-app-2.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api"


def _auth() -> requests.Session:
    s = requests.Session()
    s.post(
        f"{API}/auth/login",
        json={"email": "chris@dcsbuilt.com.au", "password": "Redcar01"},
        timeout=15,
    )
    return s


class TestRenderBlockBackground:
    def test_image_block_with_bg_includes_css(self):
        block = {
            "type": "image",
            "id": "b1",
            "x": 10, "y": 10, "width": 200, "height": 200,
            "image_url": "https://example.com/cat.png",
            "background_color": "#FFE9C8",
        }
        html = _render_block(block, {})
        assert "background-color:#FFE9C8" in html, html
        # And the image still renders.
        assert "<img" in html

    def test_image_block_without_bg_unchanged(self):
        block = {
            "type": "image", "id": "b2",
            "x": 0, "y": 0, "width": 100, "height": 100,
            "image_url": "https://example.com/dog.png",
        }
        html = _render_block(block, {})
        assert "background-color" not in html, html
        assert "<img" in html

    def test_empty_image_block_with_bg_renders_as_color_tile(self):
        # No image_url and no image_path — but a background_color is set.
        # The block must still emit a non-empty wrapper so the colour
        # actually shows on the page.
        block = {
            "type": "image", "id": "b3",
            "x": 0, "y": 0, "width": 100, "height": 100,
            "background_color": "#9E4532",
        }
        html = _render_block(block, {})
        assert "background-color:#9E4532" in html, html
        # No <img> tag (no source) — but the div must be there.
        assert "<img" not in html
        assert "<div" in html

    def test_text_blocks_ignore_background_color(self):
        # The field only applies to image blocks. Text blocks are
        # unaffected so the cascade doesn't accidentally tint them.
        block = {
            "type": "text", "id": "t1",
            "x": 0, "y": 0, "width": 100, "height": 100,
            "html": "Hello",
            "color": "#000000",
            "background_color": "#FF0000",
        }
        html = _render_block(block, {})
        # The outer wrapper for text blocks does not get a background
        # painted (text styling is purely via `color`).
        assert "background-color:#FF0000" not in html, html


class TestBackgroundColorPersistence:
    """The Block model must round-trip the background_color field."""

    def test_field_persists_on_save_and_load(self):
        s = _auth()
        r = s.post(f"{API}/books", json={"title": f"TEST_bg_{uuid.uuid4().hex[:6]}", "page_size": "square"})
        r.raise_for_status()
        bid = r.json()["id"]
        try:
            book = s.get(f"{API}/books/{bid}").json()
            book["pages"][0]["blocks"] = [{
                "id": str(uuid.uuid4()),
                "type": "image",
                "x": 10, "y": 10, "width": 100, "height": 100,
                "image_url": "",
                "background_color": "#CFE3DC",
            }]
            r = s.put(f"{API}/books/{bid}", json=book)
            assert r.status_code == 200, r.text
            fetched = s.get(f"{API}/books/{bid}").json()
            blk = fetched["pages"][0]["blocks"][0]
            assert blk.get("background_color") == "#CFE3DC", blk
        finally:
            s.delete(f"{API}/books/{bid}")
