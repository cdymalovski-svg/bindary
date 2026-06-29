"""Unit tests for the iteration-51 font-subsetting logic.

These tests run against `pdf_builder` functions directly (no HTTP),
so they execute in <100 ms total and don't depend on the hosted
preview environment.

Coverage:
  - _extract_used_font_families scans all three font-introduction
    surfaces (block.font_family, text_presets, inline rich-text HTML).
  - 'Cormorant Garamond' is always present in the result, even when
    the book references nothing — because _render_block injects it
    as a hardcoded fallback string in the CSS.
  - missing_families correctly flags typos that won't resolve at
    render time, distinguishing them from known system fonts.
  - _google_fonts_css(used_families=...) returns a strict subset of
    the full catalog; passing None still returns the full catalog
    (backwards-compat).
"""
from __future__ import annotations

import re
import sys
import pytest

sys.path.insert(0, "/app/backend")

from pdf_builder import (
    _extract_used_font_families,
    _google_fonts_css,
    _KNOWN_SYSTEM_FONTS,
)


# ── _extract_used_font_families ─────────────────────────────────────────

class TestUsedFontFamiliesScan:
    def test_empty_book_always_includes_cormorant_default(self):
        """`_render_block` hard-codes 'Cormorant Garamond' as the per-block
        font-family fallback string in the rendered CSS. The subset MUST
        include it even when no block references it, otherwise legacy
        books with no font_family field would silently fall back to
        system serif."""
        used, missing = _extract_used_font_families(
            {"pages": [], "text_presets": None}
        )
        assert "Cormorant Garamond" in used
        assert missing == set()

    def test_per_block_font_family_is_scanned(self):
        book = {"pages": [
            {"blocks": [
                {"type": "text", "font_family": "Lora", "html": "<p>x</p>"},
                {"type": "text", "font_family": "Pacifico", "html": "<p>y</p>"},
            ]},
        ]}
        used, _ = _extract_used_font_families(book)
        assert "Lora" in used
        assert "Pacifico" in used

    def test_text_presets_scanned(self):
        book = {"pages": [], "text_presets": {
            "title": {"font_family": "Abril Fatface"},
            "subtitle": {"font_family": "Lora"},
            "page_text": {"font_family": "EB Garamond"},
        }}
        used, _ = _extract_used_font_families(book)
        assert {"Abril Fatface", "Lora", "EB Garamond"} <= used

    def test_inline_rich_text_font_family_is_scanned(self):
        """The rich-text toolbar emits inline `style="font-family: …"`.
        `_safe_block_html` only strips <script>/handlers, preserving
        inline styles verbatim. The scan MUST catch them or those
        characters silently fall back to serif at render."""
        book = {"pages": [
            {"blocks": [
                {"type": "text", "font_family": "Lora",
                 "html": "<p>plain <span style='font-family: Caveat;'>cursive</span> tail</p>"},
                {"type": "text", "font_family": "Lora",
                 "html": '<p style="font-family:Bebas Neue;">heading</p>'},
            ]},
        ]}
        used, _ = _extract_used_font_families(book)
        assert "Caveat" in used
        assert "Bebas Neue" in used

    def test_missing_family_flagged_when_not_in_cache_and_not_system(self):
        book = {"pages": [
            {"blocks": [
                {"type": "text", "font_family": "TotallyMadeUpFont",
                 "html": "<p>x</p>"},
            ]},
        ]}
        used, missing = _extract_used_font_families(book)
        assert "TotallyMadeUpFont" in missing

    def test_system_fonts_are_not_flagged_as_missing(self):
        """Helvetica/Georgia/Times New Roman etc. resolve via the host
        font catalog at PDF-viewer time. They're not in the cached
        Google Fonts CSS and shouldn't trigger a 'silent fallback' warning."""
        book = {"pages": [
            {"blocks": [
                {"type": "text", "font_family": "Helvetica", "html": "<p>x</p>"},
                {"type": "text", "font_family": "Georgia", "html": "<p>x</p>"},
                {"type": "text", "font_family": "Times New Roman", "html": "<p>x</p>"},
                {"type": "text", "font_family": "Courier New", "html": "<p>x</p>"},
            ]},
        ]}
        used, missing = _extract_used_font_families(book)
        assert missing == set(), f"system fonts wrongly flagged: {missing}"

    def test_quoted_inline_family_is_normalised(self):
        book = {"pages": [
            {"blocks": [
                {"type": "text", "font_family": "Lora",
                 "html": "<p style='font-family: \"Playfair Display\", serif;'>X</p>"},
            ]},
        ]}
        used, _ = _extract_used_font_families(book)
        # Note: the regex captures the head of the font-stack, no quotes.
        assert "Playfair Display" in used

    def test_cache_membership_is_case_insensitive(self):
        """Author types 'cormorant garamond' (lower case) in block.font_family.
        Cache CSS spells it 'Cormorant Garamond'. Should NOT be flagged
        as missing — that would be a false positive forcing authors to
        match exact capitalisation."""
        book = {"pages": [
            {"blocks": [
                {"type": "text", "font_family": "cormorant garamond",
                 "html": "<p>x</p>"},
            ]},
        ]}
        used, missing = _extract_used_font_families(book)
        assert missing == set()


# ── _google_fonts_css ───────────────────────────────────────────────────

class TestGoogleFontsCssSubsetting:
    def test_no_subset_returns_full_catalog(self):
        """Passing None preserves backwards-compat: existing code paths
        that haven't been updated still get the full 369-rule block."""
        full = _google_fonts_css(used_families=None)
        # The cached CSS has ~369 @font-face blocks in production. Even
        # in degraded modes (cache miss → @import fallback) it's >0.
        assert "@font-face" in full or "@import" in full
        assert len(full) > 1000  # sanity floor: full catalog is ~150 KB

    def test_subset_is_proper_subset_of_full(self):
        full = _google_fonts_css(used_families=None)
        if "@font-face" not in full:
            pytest.skip("font cache empty (degraded mode); subset test n/a")
        full_blocks = full.count("@font-face")
        subset = _google_fonts_css(used_families={"Cormorant Garamond"})
        subset_blocks = subset.count("@font-face")
        assert 0 < subset_blocks < full_blocks
        # All subset blocks must declare Cormorant Garamond.
        fam_re = re.compile(r"font-family\s*:\s*([^;]+)\s*;", re.IGNORECASE)
        for block in subset.split("@font-face")[1:]:
            m = fam_re.search(block)
            assert m, f"malformed block in subset: {block[:120]!r}"
            fam = m.group(1).strip().strip("'\"").lower()
            assert fam == "cormorant garamond", (
                f"non-Cormorant family leaked into subset: {fam!r}"
            )

    def test_subset_with_two_families_keeps_both(self):
        if "@font-face" not in _google_fonts_css(None):
            pytest.skip("font cache empty (degraded mode); subset test n/a")
        subset = _google_fonts_css({"Cormorant Garamond", "Lora"})
        fam_re = re.compile(r"font-family\s*:\s*([^;]+)\s*;", re.IGNORECASE)
        families = set()
        for block in subset.split("@font-face")[1:]:
            m = fam_re.search(block)
            if m:
                families.add(m.group(1).strip().strip("'\"").lower())
        assert families == {"cormorant garamond", "lora"}

    def test_subset_with_missing_family_silently_drops_it(self):
        """If `used_families` includes a name not in the cached catalog,
        the subset just drops it — the CALLER (_extract_used_font_families)
        is responsible for warning the user. _google_fonts_css itself
        must not crash or include unrelated families."""
        if "@font-face" not in _google_fonts_css(None):
            pytest.skip("font cache empty (degraded mode); subset test n/a")
        subset = _google_fonts_css({"Cormorant Garamond", "TotallyFake"})
        # Cormorant should still be present; TotallyFake just absent.
        assert "Cormorant Garamond".lower() in subset.lower()
        assert "TotallyFake".lower() not in subset.lower()


# ── _KNOWN_SYSTEM_FONTS sanity ──────────────────────────────────────────

class TestSystemFontsList:
    def test_includes_editor_dropdown_system_options(self):
        """The frontend font dropdown (frontend/src/lib/fonts.js) lists
        these as system options. Keep _KNOWN_SYSTEM_FONTS in sync so a
        user picking 'Helvetica' from the dropdown doesn't trigger a
        spurious 'missing font' warning."""
        for f in ("georgia", "times new roman", "helvetica", "courier new"):
            assert f in _KNOWN_SYSTEM_FONTS, f"system font missing: {f}"

    def test_includes_css_generic_keywords(self):
        """`_render_block` always appends `, serif` to the font stack.
        That literal keyword would otherwise be flagged as missing on
        every render — must be whitelisted."""
        for kw in ("serif", "sans-serif", "monospace"):
            assert kw in _KNOWN_SYSTEM_FONTS
