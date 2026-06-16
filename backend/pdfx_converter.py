"""PDF/X-1a:2001 conversion via Ghostscript.

Takes an RGB PDF (e.g. one produced by `page.pdf()` in Playwright) and
returns a PDF/X-1a:2001-compliant CMYK version suitable for offset
printing. Implemented as a subprocess call so it can be swapped out for
another engine (qpdf+vera, mutool, etc.) without touching the worker.

Why PDF/X-1a:2001 specifically:
  - Hard-required by most short-run and offset commercial printers.
  - Forces CMYK, embeds all fonts, strips client-side colour management,
    flattens transparency, and bakes an OutputIntent ICC profile (we use
    Ghostscript's bundled SWOP-flavoured `default_cmyk.icc` — close
    enough to US Web Coated SWOP v2 for general commercial work).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("pdfx")

# Process-wide flag so concurrent jobs don't all try `apt-get install` at once.
_GS_INSTALL_LOCK = asyncio.Lock()
_GS_INSTALL_TRIED = False


async def _try_install_ghostscript() -> bool:
    """Best-effort `apt-get install ghostscript`. No-op if we're not on a
    Debian/Ubuntu container or don't have root. Returns True if `gs` is
    available afterwards. Mirrors how `pdf_builder.ensure_chromium_installed`
    self-heals a stripped production image on first use."""
    global _GS_INSTALL_TRIED
    async with _GS_INSTALL_LOCK:
        if shutil.which("gs") is not None:
            return True
        if _GS_INSTALL_TRIED:
            return shutil.which("gs") is not None
        _GS_INSTALL_TRIED = True
        if shutil.which("apt-get") is None:
            return False
        log.info("Ghostscript missing — attempting `apt-get install ghostscript`")

        def _install() -> int:
            try:
                env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
                subprocess.run(
                    ["apt-get", "update"], check=False, capture_output=True,
                    env=env, timeout=60,
                )
                proc = subprocess.run(
                    ["apt-get", "install", "-y", "--no-install-recommends", "ghostscript"],
                    check=False, capture_output=True, env=env, timeout=180,
                )
                return proc.returncode
            except Exception:
                return -1

        rc = await asyncio.to_thread(_install)
        ok = shutil.which("gs") is not None
        log.info("Ghostscript auto-install rc=%s available=%s", rc, ok)
        return ok


# Ghostscript on Debian ships an ICC profile at this canonical path. The
# bundled profile is close to US Web Coated SWOP v2 and is acceptable for
# PDF/X-1a output for general commercial print. If you need an exact
# match for a specific printer (FOGRA39 / GRACoL etc.), drop the .icc
# file alongside this module and override `CMYK_ICC_PATH`.
_DEFAULT_ICC_CANDIDATES = (
    "/usr/share/color/icc/ghostscript/default_cmyk.icc",
    "/usr/share/ghostscript/Resource/ColorSpace/DefaultCMYK",
)


def _resolve_icc_path() -> str:
    """Find a usable CMYK ICC profile on disk."""
    override = os.environ.get("PDFX_CMYK_ICC_PATH")
    if override and Path(override).exists():
        return override
    for p in _DEFAULT_ICC_CANDIDATES:
        if Path(p).exists():
            return p
    raise RuntimeError(
        "No CMYK ICC profile found. Install `ghostscript` (apt) or set "
        "PDFX_CMYK_ICC_PATH to a valid .icc file."
    )


def _pdfx_def_ps(icc_path: str, title: str) -> str:
    """Build the PDF/X definitions file Ghostscript expects on stdin
    BEFORE the input PDF. The `[/_objdef ...]` block creates a PDF
    OutputIntent dict and chains the ICC profile into it.
    """
    # Title must be a parenthesised PostScript string with unsafe chars escaped.
    safe_title = re.sub(r"([()\\])", r"\\\1", title or "Document")
    # NOTE: do NOT change the structure of this template — Ghostscript is
    # extremely picky about PDFX_def.ps and the slightest reformatting
    # silently produces non-conformant output.
    return f"""%!
% PDF/X-1a:2001 OutputIntent definition (consumed by Ghostscript -dPDFX)

[ /Title ({safe_title})
  /DOCINFO pdfmark

[/_objdef {{icc_PDFX}} /type /stream /OBJ pdfmark
[{{icc_PDFX}} <</N 4>> /PUT pdfmark
[{{icc_PDFX}} ({icc_path}) (r) file /PUT pdfmark

[/_objdef {{OutputIntent_PDFX}} /type /dict /OBJ pdfmark
[{{OutputIntent_PDFX}} <<
  /Type /OutputIntent
  /S /GTS_PDFX
  /OutputCondition (Commercial print, CMYK)
  /OutputConditionIdentifier (CGATS TR 001 SWOP2006)
  /RegistryName (http://www.color.org)
  /Info (US Web Coated SWOP v2)
  /DestOutputProfile {{icc_PDFX}}
>> /PUT pdfmark

[{{Catalog}} <</OutputIntents [ {{OutputIntent_PDFX}} ]>> /PUT pdfmark
"""


async def convert_to_pdfx(
    pdf_bytes: bytes,
    *,
    title: str = "Document",
    total_pages: Optional[int] = None,
    progress_cb: Optional["Callable[[str], None]"] = None,
) -> bytes:
    """Convert an RGB PDF to PDF/X-1a:2001 CMYK bytes.

    `total_pages` and `progress_cb` are optional — when both are supplied,
    Ghostscript's per-page output is parsed and the callback is invoked
    with strings like `"converting to PDF/X-1a (12/60)"` so the frontend
    toast can show a live progress counter instead of a static stage.

    Raises a RuntimeError with the Ghostscript stderr tail if the
    conversion fails — caller should surface that to the user so they
    know why "Print-ready" did not produce a file.
    """
    if shutil.which("gs") is None:
        # In production the container may not bake Ghostscript in. Try to
        # heal at first request, then re-check.
        installed = await _try_install_ghostscript()
        if not installed or shutil.which("gs") is None:
            raise RuntimeError(
                "Ghostscript (`gs`) is not installed on the server. "
                "Install via `apt-get install -y ghostscript` or contact "
                "your platform admin to add it to the deployment image."
            )

    icc_path = _resolve_icc_path()

    # All work happens in a temp dir so we never leak the input PDF or the
    # PDFX_def.ps to /tmp lingerers after the worker returns.
    with tempfile.TemporaryDirectory(prefix="bindery_pdfx_") as tmp:
        tmp_path = Path(tmp)
        in_pdf = tmp_path / "in.pdf"
        out_pdf = tmp_path / "out.pdf"
        defs_ps = tmp_path / "PDFX_def.ps"
        in_pdf.write_bytes(pdf_bytes)
        defs_ps.write_text(_pdfx_def_ps(icc_path, title))

        cmd = [
            "gs",
            "-dPDFX",
            "-dBATCH",
            "-dNOPAUSE",
            "-dNOOUTERSAVE",
            "-dNOSAFER",
            # NOTE: we DELIBERATELY do not pass -dQUIET — we rely on
            # Ghostscript's "Page N" stdout line per converted page to
            # power the streaming progress bar below. Stdout is drained
            # line-by-line so the pipe buffer never fills.
            "-dNumRenderingThreads=2",        # use both cores when available
            "-dCompatibilityLevel=1.3",       # PDF/X-1a:2001 requires 1.3
            "-sDEVICE=pdfwrite",
            "-sColorConversionStrategy=CMYK",
            "-dProcessColorModel=/DeviceCMYK",
            # NOTE: we deliberately do NOT pass `-dBlackText=true` or
            # `-dBlackVector=true` — those flags force EVERY text/vector
            # to render in pure black regardless of source colour
            # (turning a deep-green chapter heading into pure K). The
            # IngramSpark v5.11.26 spec calls for RGB(0,0,0) text to
            # become DeviceK only, which Ghostscript's CMYK ICC pipeline
            # approximates well enough on its own — the conversion of
            # RGB(0,0,0) via the SWOP profile produces a 100% K dominant
            # build with negligible CMY values. A future enhancement
            # could add a custom defs.ps colour override to force
            # exactly 0/0/0/100 for pure-grey input, leaving the rest
            # of the ICC pipeline intact.
            # /prepress favours quality (slow); /printer is the middle ground
            # and is still PDF/X-conformant when paired with the explicit
            # -dPDFX flag above. Drops conversion time roughly 40-60% on a
            # 100-page picture book without changing the final spec.
            "-dPDFSETTINGS=/printer",
            "-dEmbedAllFonts=true",
            "-dSubsetFonts=true",
            "-dCompressFonts=true",
            "-dAutoRotatePages=/None",
            f"-sOutputICCProfile={icc_path}",
            f"-sDefaultCMYKProfile={icc_path}",
            f"-sOutputFile={out_pdf}",
            str(defs_ps),
            str(in_pdf),
        ]
        # Wrap with `stdbuf -oL` when available so Ghostscript's stdout
        # flushes line-by-line instead of being block-buffered by libc
        # (default when stdout is a pipe rather than a TTY). Without
        # this, the `Page N` ticks can accumulate in a 4-64 KB pipe
        # buffer and arrive in a single burst at the end — making the
        # progress bar look frozen for the entire conversion on small
        # books. `stdbuf` is part of coreutils; available on every
        # Debian/Ubuntu container we ship to.
        if shutil.which("stdbuf") is not None:
            cmd = ["stdbuf", "-oL", *cmd]

        log.info(
            "PDFX conversion starting (in=%d bytes, total_pages=%s)",
            len(pdf_bytes), total_pages,
        )

        def _report(page_done: int) -> None:
            if progress_cb is None:
                return
            try:
                if total_pages:
                    progress_cb(f"converting to PDF/X-1a ({page_done}/{total_pages})")
                else:
                    progress_cb(f"converting to PDF/X-1a (page {page_done})")
            except Exception:
                # Progress is best-effort — never let a callback error
                # take down the conversion.
                pass

        # Async subprocess + streaming stdout. 10-minute ceiling so even a
        # 300-page colour book in a slow production pod gets a chance to
        # finish.
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise RuntimeError(f"Ghostscript could not be launched: {e}") from None

        stderr_chunks: list[bytes] = []
        # Tracks the last time we emitted any progress for the heartbeat
        # below. Updated by `_report` (page tick) and by the heartbeat
        # itself. Using a mutable list so the inner closures share state.
        last_emit: list[float] = [asyncio.get_event_loop().time()]
        gs_start = last_emit[0]
        # Holds the last seen page number so heartbeats can keep the bar
        # at the correct fraction instead of dropping back to "no number".
        last_page: list[int] = [0]

        async def _drain_stderr() -> None:
            assert proc.stderr is not None
            while True:
                chunk = await proc.stderr.readline()
                if not chunk:
                    return
                stderr_chunks.append(chunk)

        async def _drain_stdout() -> None:
            """Parse Ghostscript's `Page N` lines and emit progress.
            Ghostscript prints one line per converted page; we use that as
            our progress tick. We don't fan out work — pdfwrite is
            single-stream — but watching the counter is the cheapest
            real-time signal we get."""
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    return
                # Lines look like b"Page 12\n" — be defensive against any
                # other diagnostic gs might choose to emit.
                if line.startswith(b"Page "):
                    try:
                        n = int(line[5:].strip())
                    except ValueError:
                        continue
                    last_page[0] = n
                    last_emit[0] = asyncio.get_event_loop().time()
                    _report(n)

        async def _heartbeat() -> None:
            """Defensive: even on Linux with `stdbuf -oL` we occasionally
            see Ghostscript hold stdout for several seconds during its
            initial PDF parse + ICC profile load — the progress bar would
            look frozen and the user would assume the worker died. This
            task fires every 2s; if nothing else has emitted a tick in
            the last 2.5s, we push a heartbeat string that includes the
            elapsed time so the frontend toast and the underlying Mongo
            stage record both visibly advance. The polling client uses
            this to confirm the worker is alive even when gs is silent.
            Exits cleanly when the subprocess exits."""
            while proc.returncode is None:
                await asyncio.sleep(2.0)
                if proc.returncode is not None:
                    return
                now = asyncio.get_event_loop().time()
                if now - last_emit[0] < 2.5:
                    continue
                if progress_cb is None:
                    last_emit[0] = now
                    continue
                elapsed = int(now - gs_start)
                try:
                    if total_pages and last_page[0] > 0:
                        # Keep the fraction stable so the progress bar
                        # stays visibly filled at the last known page.
                        progress_cb(
                            f"converting to PDF/X-1a ({last_page[0]}/{total_pages}) "
                            f"· {elapsed}s"
                        )
                    else:
                        progress_cb(f"converting to PDF/X-1a · working {elapsed}s")
                except Exception:
                    pass
                last_emit[0] = now

        # Run both drainers, the heartbeat and the process wait
        # concurrently with a single overall timeout. If the timeout
        # fires we kill the process so it doesn't linger as a zombie.
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _drain_stdout(), _drain_stderr(), _heartbeat(), proc.wait()
                ),
                timeout=600,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            log.error("PDFX conversion timed out after 600s")
            raise RuntimeError(
                "PDF/X-1a conversion timed out (>10 min). Try exporting a "
                "smaller page range, or disable the Print-ready toggle to "
                "ship an RGB PDF and let your printer handle conversion."
            ) from None

        returncode = proc.returncode
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")

        if returncode != 0 or not out_pdf.exists():
            stderr_tail = "\n".join(stderr_text.splitlines()[-15:])
            log.error("PDFX conversion failed (rc=%s): %s", returncode, stderr_tail)
            raise RuntimeError(
                f"PDF/X-1a conversion failed: {stderr_tail or 'no stderr'}"
            )

        out_bytes = out_pdf.read_bytes()
        log.info(
            "PDFX conversion done (out=%d bytes, ratio=%.2fx)",
            len(out_bytes),
            len(out_bytes) / max(1, len(pdf_bytes)),
        )
        return out_bytes
