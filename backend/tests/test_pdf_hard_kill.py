"""Unit tests for the multiprocessing hard-kill logic in
`pdf_builder_weasy.build_book_pdf`.

Verifies (per user brief 2026-02):
  1. Happy-path render still works after the multiprocessing swap.
  2. 60s hard-kill fires when a page hangs — simulated with a
     `time.sleep(90)` injected into WeasyPrint's `write_pdf` call.
     Timeout constant is monkey-patched to 3s so the test is fast.
  3. WARNING log line fires when a page is killed and includes the
     page number and block content dump.
  4. No zombie processes remain after the kill — verified via
     `multiprocessing.active_children()` and the parent's child-pid
     record.
  5. When a hang triggers the kill, the caller substitutes a blank
     placeholder page rather than aborting the export.

These are in-process unit tests — no HTTP, no MongoDB. They import
`pdf_builder_weasy` directly and exercise `build_book_pdf` with a
synthetic book dict + a stub `get_image`.
"""
from __future__ import annotations

import asyncio
import logging
import multiprocessing
import time

import pytest


# --------------------------------------------------------------------------
# Minimal book fixture — 1 page, 1 text block, no images. Keeps the render
# fast in the happy path and small enough that the WARNING log dump is
# assertable.
# --------------------------------------------------------------------------

def _make_book(marker_text: str = "HANG_ME_PLEASE") -> dict:
    return {
        "id": "test-hard-kill",
        "title": "TEST_hard_kill",
        "page_size": "a4",
        "text_presets": {},
        "pages": [
            {
                "id": "p1",
                "index": 0,
                "background_color": "#ffffff",
                "full_bleed": False,
                "show_page_number": False,
                "blocks": [
                    {
                        "id": "b1",
                        "type": "text",
                        "x": 100,
                        "y": 100,
                        "width": 400,
                        "height": 100,
                        "font_family": "Cormorant Garamond",
                        "font_size": 18,
                        "html": f"<p>{marker_text}</p>",
                        "color": "#000000",
                    }
                ],
            }
        ],
    }


def _stub_get_image(_key: str) -> tuple[bytes, str]:
    return (b"", "image/png")


# --------------------------------------------------------------------------
# 1. Happy path — the multiprocessing swap must not break normal renders.
# --------------------------------------------------------------------------

def test_happy_path_render_completes():
    """1-page book renders end-to-end via the subprocess fork and returns
    a valid PDF (starts with %PDF magic)."""
    import pdf_builder_weasy as pw
    book = _make_book("plain content")

    async def _run():
        return await pw.build_book_pdf(book, _stub_get_image)

    pdf_bytes = asyncio.run(_run())
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF"), f"Not a PDF (starts with {pdf_bytes[:10]!r})"
    assert len(pdf_bytes) > 500, f"PDF suspiciously small: {len(pdf_bytes)} bytes"


# --------------------------------------------------------------------------
# 2 + 3 + 5. Hard-kill fires on hang, WARNING logs with page + content,
# blank placeholder substituted so caller still gets a PDF.
# --------------------------------------------------------------------------

# Module-level conditional sleeper — hangs ONLY if the rendered HTML
# contains the hang marker string. The blank-placeholder fallback path
# uses the same weasyprint.HTML class but renders "[page could not be
# rendered]", so it must NOT hang or the test can't verify the
# placeholder fallback actually completes.
from weasyprint import HTML as _RealHTML  # noqa: E402


class _ConditionalSleepingHTML:
    def __init__(self, *args, **kwargs):
        html_str = kwargs.get("string") or (args[0] if args else "")
        self._html_str = html_str or ""
        # Delegate to the real WeasyPrint HTML for the blank placeholder
        # and any other non-hanging call so those still produce a valid
        # PDF.
        self._real = _RealHTML(*args, **kwargs)

    def write_pdf(self, *args, **kwargs):
        if "HANG_MARKER_XYZ" in self._html_str or "ZOMBIE_SCAN_MARKER" in self._html_str:
            time.sleep(90)  # well past the monkey-patched process timeout
        return self._real.write_pdf(*args, **kwargs)


def test_hard_kill_fires_on_hang_and_logs_warning(monkeypatch, caplog):
    """Inject a 90s hang inside the fork; assert:
      - the subprocess is terminated within the (shortened) 3s budget
      - a WARNING log with page number + block content dump is emitted
      - the caller substitutes a blank placeholder page
      - the whole call still returns a valid PDF
    """
    import pdf_builder_weasy as pw

    # Shorten the hard-kill deadline so the test finishes in seconds.
    monkeypatch.setattr(pw, "_PAGE_PROCESS_TIMEOUT_S", 3.0)
    monkeypatch.setattr(pw, "HTML", _ConditionalSleepingHTML)

    book = _make_book("HANG_MARKER_XYZ")

    # Snapshot the process table so we can assert no zombies remain.
    children_before = set(p.pid for p in multiprocessing.active_children())

    caplog.set_level(logging.WARNING, logger="bindery.pdf_weasy")

    async def _run():
        return await pw.build_book_pdf(book, _stub_get_image)

    t0 = time.monotonic()
    pdf_bytes = asyncio.run(_run())
    elapsed = time.monotonic() - t0

    # (2) Kill must fire well before the 90s sleep would complete. Allow
    # some slack for fork overhead + SIGTERM→SIGKILL escalation (~7s max
    # per branch: 3s wait + 5s SIGTERM grace + 2s SIGKILL join).
    assert elapsed < 30, (
        f"Kill did not fire in time — elapsed {elapsed:.1f}s "
        f"(expected < 30s with 3s process timeout)"
    )

    # (5) Caller still returned a PDF (blank placeholder in place of the
    # hung page).
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF"), (
        f"Expected placeholder PDF, got {pdf_bytes[:20]!r}"
    )

    # (3) WARNING log must mention page 1 AND include the block content
    # dump so operators can identify the pathological page from logs.
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    kill_warnings = [
        r for r in warnings
        if "killed after" in r.getMessage() and "page 1" in r.getMessage()
    ]
    assert kill_warnings, (
        f"No 'killed after' WARNING for page 1 found. "
        f"Got warnings: {[r.getMessage() for r in warnings]}"
    )
    # Block content dump must appear in the SAME warning line.
    kill_msg = kill_warnings[0].getMessage()
    assert "HANG_MARKER_XYZ" in kill_msg, (
        f"Block content dump missing from kill WARNING. Got: {kill_msg}"
    )
    # Should also identify the block type so operators know what was
    # being rendered when it hung.
    assert "text" in kill_msg.lower(), (
        f"Block type not surfaced in kill WARNING. Got: {kill_msg}"
    )

    # (4) No zombie processes remain — every multiprocessing.Process the
    # test spawned should be joined and closed by build_book_pdf's
    # `finally` block.
    # Give the OS a beat to reap.
    time.sleep(0.5)
    children_after = set(p.pid for p in multiprocessing.active_children())
    new_children = children_after - children_before
    assert not new_children, (
        f"Zombie processes leaked after hard-kill: {new_children}. "
        f"multiprocessing.active_children() = {multiprocessing.active_children()}"
    )


# --------------------------------------------------------------------------
# 4b. Also verify at the OS level (via psutil-style scan) that no
# defunct/zombie child procs are left behind. Uses /proc since psutil
# isn't a runtime dep — cheap and Linux-native.
# --------------------------------------------------------------------------

def _scan_zombie_children_of(pid: int) -> list[int]:
    """Return PIDs of any Z-state (zombie) child processes of `pid`."""
    import os
    zombies: list[int] = []
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            child_pid = int(entry)
            try:
                with open(f"/proc/{child_pid}/status") as f:
                    lines = f.read().splitlines()
                info = {}
                for line in lines:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        info[k.strip()] = v.strip()
                if info.get("PPid", "").split()[0:1] == [str(pid)]:
                    state = info.get("State", "").split()[0:1]
                    if state and state[0] == "Z":
                        zombies.append(child_pid)
            except (FileNotFoundError, PermissionError, IndexError):
                pass
    except FileNotFoundError:
        pytest.skip("/proc not available — non-Linux host")
    return zombies


def test_no_zombie_procs_in_proc_table_after_kill(monkeypatch):
    """After a hard-kill, `/proc/*` must show no Z-state child processes
    parented to us. Guards against `proc.close()` being missed."""
    import os
    import pdf_builder_weasy as pw

    monkeypatch.setattr(pw, "_PAGE_PROCESS_TIMEOUT_S", 3.0)
    monkeypatch.setattr(pw, "HTML", _ConditionalSleepingHTML)
    book = _make_book("ZOMBIE_SCAN_MARKER")

    async def _run():
        return await pw.build_book_pdf(book, _stub_get_image)

    asyncio.run(_run())

    # Give the reaper a beat.
    time.sleep(0.5)
    zombies = _scan_zombie_children_of(os.getpid())
    assert not zombies, f"Zombie child processes remain: {zombies}"
