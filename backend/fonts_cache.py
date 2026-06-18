"""Local cache for Google Fonts.

The PDF renderer's HTML imports a large Google Fonts stylesheet (~27
families). WeasyPrint and Chromium both fetch it (and every WOFF2 it
references) for EVERY render — on a slow pod-to-Google network, this
hangs the export for minutes. Symptoms in production:

    - 1-page export works (one fetch round trip).
    - Full-book export fails (chunked rendering = multiple fetches
      against a slow origin, exceeds chunk timeouts).

This module:
    1. Downloads the Google Fonts CSS once at first use.
    2. Parses it for WOFF2 URLs.
    3. Downloads every WOFF2 once and caches it in memory.
    4. Exposes:
       - `LOCAL_FONTS_CSS`: a self-contained `@font-face` block with
         data: URIs for every WOFF2. Used inline in PDF HTML so no
         network fetch is needed during render.
       - `font_url_cache_get(url)`: helper for the WeasyPrint
         `url_fetcher` so any leftover Google Fonts URL in the HTML
         is served from local memory.

All work happens lazily on the first call. If the network is down at
that moment, we fall back to a no-op CSS string and the PDF renders
with system fonts (DejaVu) — visually inferior but never hangs.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Optional

import requests

log = logging.getLogger("bindery.fonts_cache")

# The exact Google Fonts URL the HTML used to fetch every render.
# Kept in one place so updates to the font list happen here only.
GOOGLE_FONTS_URL = (
    "https://fonts.googleapis.com/css2?"
    "family=Abril+Fatface"
    "&family=Bebas+Neue"
    "&family=Bitter:ital,wght@0,400;0,600;0,700;1,400"
    "&family=Caveat:wght@400;700"
    "&family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500"
    "&family=Crimson+Text:ital,wght@0,400;0,600;1,400"
    "&family=Dancing+Script:wght@400;700"
    "&family=DM+Sans:wght@400;500;700"
    "&family=EB+Garamond:ital,wght@0,400;0,500;0,600;0,700;1,400"
    "&family=Fira+Code:wght@400;500;700"
    "&family=Inconsolata:wght@400;700"
    "&family=Indie+Flower"
    "&family=JetBrains+Mono:wght@400;500;700"
    "&family=Kalam:wght@400;700"
    "&family=Libre+Baskerville:ital,wght@0,400;0,700;1,400"
    "&family=Lobster"
    "&family=Lora:ital,wght@0,400;0,500;0,600;0,700;1,400"
    "&family=Merriweather:ital,wght@0,400;0,700;0,900;1,400"
    "&family=Montserrat:wght@300;400;500;600;700"
    "&family=Nunito:wght@400;600;700"
    "&family=Outfit:wght@300;400;500;600;700"
    "&family=Pacifico"
    "&family=Playfair+Display:ital,wght@0,400;0,500;0,600;0,700;1,400"
    "&family=Poppins:wght@300;400;500;600;700"
    "&family=Raleway:wght@300;400;500;600;700"
    "&family=Sacramento"
    "&family=Work+Sans:wght@300;400;500;600;700"
    "&display=swap"
)

# Google serves different WOFF2 files depending on the User-Agent.
# Modern browsers get WOFF2; legacy UAs get TTF/EOT. We *want* WOFF2 —
# smallest payload + best WeasyPrint support.
_UA_MODERN = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_FETCH_LOCK = threading.Lock()
_inlined_css: Optional[str] = None
_inline_attempt_failed: bool = False
# Map original Google Fonts WOFF2 URL → raw bytes. Used by the
# WeasyPrint url_fetcher to serve any URL the inlined CSS may have
# missed (defence-in-depth).
_woff2_cache: dict[str, bytes] = {}


def _download_once(url: str, timeout: int = 15) -> Optional[bytes]:
    """One-shot HTTP GET with explicit timeout. Returns None on any
    network/HTTP failure — callers fall back to a no-op."""
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": _UA_MODERN})
        if r.status_code != 200:
            log.warning("fonts_cache: %s returned HTTP %d", url, r.status_code)
            return None
        return r.content
    except Exception as e:
        log.warning("fonts_cache: failed to fetch %s: %s", url, e)
        return None


def _build_inlined_css() -> str:
    """Fetch the Google Fonts CSS once and pre-cache every WOFF2 it
    references. We return the ORIGINAL CSS text (with gstatic URLs)
    rather than base64-inlining the binaries — Google's response is
    ~50 KB while base64-encoding every WOFF2 would bloat every render
    by ~13 MB. The url_fetcher intercepts the gstatic URLs at render
    time and serves them from `_woff2_cache` in microseconds.

    Net effect: each render's HTML carries a small @font-face block
    that references "https://fonts.gstatic.com/..." URLs, but those
    URLs are resolved from local memory — no network during PDF build.

    Returns "" on any failure; callers fall back to system fonts."""
    css_bytes = _download_once(GOOGLE_FONTS_URL, timeout=10)
    if not css_bytes:
        return ""
    try:
        css_text = css_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return ""

    woff2_pattern = re.compile(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)")
    urls = set(woff2_pattern.findall(css_text))
    log.info("fonts_cache: downloading %d WOFF2 files", len(urls))

    fetched = 0
    for url in urls:
        data = _download_once(url, timeout=10)
        if data is None:
            continue
        _woff2_cache[url] = data
        fetched += 1
    if fetched == 0:
        log.warning("fonts_cache: 0/%d WOFF2 files downloaded — falling back to system fonts", len(urls))
        return ""
    log.info("fonts_cache: %d/%d WOFF2 files cached (%d KB total)",
             fetched, len(urls), sum(len(b) for b in _woff2_cache.values()) // 1024)

    # Return the original CSS text unchanged. The gstatic URLs inside
    # it are intercepted by the WeasyPrint url_fetcher and resolved
    # from `_woff2_cache` — equivalent to inlining but ~250× smaller
    # per render.
    return css_text


def get_local_fonts_css() -> str:
    """Return the inlined @font-face CSS for the editor's font list.

    Threaded-safe — the underlying fetch happens once globally and is
    cached for the lifetime of the process. Subsequent calls return
    the cached string in microseconds.

    On fetch failure, returns "" (empty CSS). Caller HTML still
    includes the empty block — WeasyPrint just falls back to system
    fonts, which is visually different but always renders."""
    global _inlined_css, _inline_attempt_failed
    if _inlined_css is not None:
        return _inlined_css
    with _FETCH_LOCK:
        # Double-check inside the lock so we don't race two threads
        # both fetching.
        if _inlined_css is not None:
            return _inlined_css
        if _inline_attempt_failed:
            # We already tried and the network was down. Don't keep
            # retrying on every render — log loudly and return empty.
            return ""
        result = _build_inlined_css()
        if not result:
            _inline_attempt_failed = True
            return ""
        _inlined_css = result
        return result


def font_url_cache_get(url: str) -> Optional[bytes]:
    """Return cached WOFF2 bytes for a Google Fonts URL, or None if
    not cached. Used by the WeasyPrint url_fetcher as a last-resort
    defence: even if some WOFF2 URL escaped the inlining step, the
    fetcher serves it from memory."""
    return _woff2_cache.get(url)


def warm_cache_async() -> None:
    """Fire-and-forget background warm-up. Call this from server
    startup so the first export request doesn't pay the download
    cost. The function itself is non-blocking — it spawns a thread."""
    def _go():
        try:
            get_local_fonts_css()
        except Exception as e:
            log.warning("fonts_cache: warm-up failed: %s", e)
    threading.Thread(target=_go, daemon=True, name="fonts_cache_warm").start()
