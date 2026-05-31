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


async def convert_to_pdfx(pdf_bytes: bytes, *, title: str = "Document") -> bytes:
    """Convert an RGB PDF to PDF/X-1a:2001 CMYK bytes.

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

        # NOTE: -dNOSAFER (not -dSAFER) is required so Ghostscript can read
        # the ICC profile from /usr/share. The inputs to this conversion
        # are all generated server-side from our own renderer — there is
        # no untrusted PostScript to sandbox, so disabling SAFER is the
        # documented Artifex workflow for PDF/X conversion.
        cmd = [
            "gs",
            "-dPDFX",
            "-dBATCH",
            "-dNOPAUSE",
            "-dNOOUTERSAVE",
            "-dNOSAFER",
            "-dCompatibilityLevel=1.3",       # PDF/X-1a:2001 requires 1.3
            "-sDEVICE=pdfwrite",
            "-sColorConversionStrategy=CMYK",
            "-dProcessColorModel=/DeviceCMYK",
            "-dPDFSETTINGS=/prepress",
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

        log.info("PDFX conversion starting (in=%d bytes)", len(pdf_bytes))

        # Run blocking subprocess off the event loop so the worker stays
        # responsive to other coroutines (status updates, etc.).
        def _run() -> subprocess.CompletedProcess:
            return subprocess.run(
                cmd,
                capture_output=True,
                check=False,
                timeout=180,
            )

        proc = await asyncio.to_thread(_run)

        if proc.returncode != 0 or not out_pdf.exists():
            stderr_tail = (proc.stderr or b"").decode("utf-8", errors="replace")
            stderr_tail = "\n".join(stderr_tail.splitlines()[-15:])
            log.error("PDFX conversion failed: %s", stderr_tail)
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
