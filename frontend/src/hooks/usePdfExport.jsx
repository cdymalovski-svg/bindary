/**
 * usePdfExport — encapsulates the entire "Export PDF" flow.
 *
 * The function `onExportPdf(range, options)` performs:
 *   1. A pre-flight `/api/books/{id}/file-health` call. If any image
 *      references are missing/dimensionless, it stashes the problems +
 *      pending args and returns without exporting — the caller renders
 *      `FileHealthWarningDialog` with `fileHealthProblems`. The user's
 *      "Export anyway" click calls `onFileHealthProceed` which re-invokes
 *      the export with `__skipHealthCheck: true` so no dialog loop.
 *   2. `saveBook(false)` to flush any pending edits before render.
 *   3. `POST /api/books/{id}/pdf-jobs` to start the render job.
 *   4. Polls `GET /api/books/{id}/pdf-jobs/{job_id}` every 1.2s with a
 *      6-minute per-stage idle deadline (resets whenever the stage
 *      string changes so a slow-but-progressing job never times out).
 *      Renders a custom Sonner toast with stage + progress bar.
 *   5. Downloads bytes and either (a) opens the browser print dialog via
 *      a hidden iframe (`options.printAfter`), (b) opens the blob in a
 *      new tab (`options.previewInTab`, popup-blocker fallback = download),
 *      or (c) triggers a direct download.
 *   6. Success toast wording adapts to the chosen output mode.
 *
 * Exposed state:
 *   - `exporting` — used by the toolbar to disable the Export button.
 *   - `exportsBump` — bumped after a successful export; the export
 *     popover watches this to refetch the export-history list.
 *   - `fileHealthProblems` — non-null when the warning dialog should
 *     show; wired to `FileHealthWarningDialog`.
 *   - `onFileHealthCancel` / `onFileHealthProceed` — dialog callbacks.
 */
import { useState } from 'react';
import { toast } from 'sonner';
import PdfExportToast from '@/components/PdfExportToast';

export default function usePdfExport({ book, saveBook }) {
  const [exporting, setExporting] = useState(false);
  const [exportsBump, setExportsBump] = useState(0);
  // File-health pre-flight: holds the `problems` list returned by
  // GET /api/books/{id}/file-health when the user clicks Export. When
  // non-null AND non-empty, the FileHealthWarningDialog shows and the
  // user must confirm before the actual PDF job is started. `pending`
  // captures the (range, options) args of the original Export call so
  // we can resume after confirmation.
  const [fileHealthProblems, setFileHealthProblems] = useState(null);
  const [pendingExport, setPendingExport] = useState(null);

  const onExportPdf = async (range = null, options = {}) => {
    const printAfter = options?.printAfter === true;
    // When printing, suppress the new-tab open path — we route the
    // bytes into a hidden iframe and call .print() instead so the user
    // lands on the OS print dialog directly.
    const previewInTab = !printAfter && options?.previewInTab === true;
    const pdfx = options?.pdfx === true;
    const coverSpread = options?.coverSpread === true;
    const spineWidthIn = options?.spineWidthIn;
    const binding = options?.binding === 'casebound' ? 'casebound' : 'perfect';
    const dpiRaw = parseInt(options?.dpi, 10);
    const dpi = dpiRaw === 450 || dpiRaw === 600 ? dpiRaw : 300;
    if (!book) return;

    // Pre-flight: ask the backend whether any image references in this
    // book are missing or have no dimensions. Skip the check ONLY when
    // the user has just acknowledged the warning ("Export anyway") —
    // signalled by options.__skipHealthCheck. Cover-spread exports use
    // only the cover page so the same per-book check is still valid.
    if (!options?.__skipHealthCheck) {
      try {
        const BASE = process.env.REACT_APP_BACKEND_URL;
        const token = (() => {
          try { return localStorage.getItem('bindery_token'); } catch { return null; }
        })();
        const r = await fetch(`${BASE}/api/books/${book.id}/file-health`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (r.ok) {
          const body = await r.json();
          if (body && Array.isArray(body.problems) && body.problems.length > 0) {
            // Surface the warning dialog; the user decides whether to
            // proceed. The dialog's "Export anyway" handler re-invokes
            // onExportPdf with __skipHealthCheck so we don't loop.
            setFileHealthProblems(body.problems);
            setPendingExport({ range, options });
            return;
          }
        }
        // r.ok === false: silent fall-through — pre-flight is advisory.
        // We don't block the export on a failed health-check call.
      } catch {
        // Network error talking to /file-health — also advisory, fall
        // through to the regular export path.
      }
    }
    setExporting(true);
    const toastId = 'pdf-export';
    let kindLabel;
    if (coverSpread) {
      kindLabel = binding === 'casebound' ? ' casebound cover' : ' cover spread';
    } else if (range) {
      kindLabel = ` (pages ${range.start}–${range.end})`;
    } else {
      kindLabel = '';
    }
    const modeLabel = pdfx ? ' · Print-ready' : '';
    // Local cancel state — shared between the cancel button (rendered
    // inside the toast) and the poll loop below. We use a plain object
    // rather than a ref so the function-scoped closure can flip the
    // flag without re-rendering Editor.
    const cancelState = { cancelled: false, jobId: null };
    const t0 = performance.now();
    const BASE = process.env.REACT_APP_BACKEND_URL;
    // All `/api/*` calls require the JWT — pull it from the same store
    // axios uses so we don't bypass auth when using raw fetch.
    const token = (() => {
      try { return localStorage.getItem('bindery_token'); } catch { return null; }
    })();
    const authHeaders = token ? { Authorization: `Bearer ${token}` } : {};

    const onCancel = async () => {
      if (cancelState.cancelled) return;
      cancelState.cancelled = true;
      toast.dismiss(toastId);
      toast.message('Cancelling export…', { id: toastId, duration: 4000 });
      // Best-effort — if the backend hasn't created the job doc yet,
      // the poll loop will see the local flag and stop anyway.
      if (cancelState.jobId) {
        try {
          await fetch(
            `${BASE}/api/books/${book.id}/pdf-jobs/${cancelState.jobId}/cancel`,
            { method: 'POST', headers: authHeaders },
          );
        } catch { /* swallow — frontend has already stopped polling */ }
      }
    };

    const renderToast = ({ stage, done, total, elapsedSec }) => (
      <PdfExportToast
        title={`Building PDF${kindLabel}${modeLabel}`}
        stage={stage}
        done={done}
        total={total}
        elapsedSec={elapsedSec}
        onCancel={onCancel}
      />
    );
    toast.custom(() => renderToast({ stage: 'Starting…', done: 0, total: 0, elapsedSec: 0 }), {
      id: toastId, duration: Infinity,
    });
    try {
      // Persist any in-flight edits before the server renders.
      await saveBook(false);
      // Kick off the background job. This call returns in milliseconds —
      // proxies and CDNs never see a long-lived request.
      // Note: URL avoids `.pdf` in the path because some CDNs treat dot-pdf
      // URLs as static-file fetches and short-circuit with 404.
      const bodyObj = {};
      if (range) { bodyObj.start_page = range.start; bodyObj.end_page = range.end; }
      if (pdfx) bodyObj.pdfx = true;
      if (coverSpread) bodyObj.cover_spread = true;
      if (spineWidthIn != null) bodyObj.spine_width_in = spineWidthIn;
      if (coverSpread && binding === 'casebound') bodyObj.binding = 'casebound';
      if (dpi !== 300) bodyObj.dpi = dpi;
      const body = Object.keys(bodyObj).length ? JSON.stringify(bodyObj) : undefined;
      const startResp = await fetch(`${BASE}/api/books/${book.id}/pdf-jobs`, {
        method: 'POST',
        headers: {
          ...(body ? { 'Content-Type': 'application/json' } : {}),
          ...authHeaders,
        },
        body,
      });
      if (!startResp.ok) {
        let detail = `HTTP ${startResp.status}`;
        try { const b = await startResp.json(); if (b?.detail) detail = b.detail; } catch { /* response had no JSON body — keep generic HTTP detail. */ }
        throw new Error(detail);
      }
      const { job_id } = await startResp.json();
      cancelState.jobId = job_id;
      // If the user clicked Cancel in the brief window between starting
      // the job and getting its id back, fire the cancel request now.
      if (cancelState.cancelled) {
        try {
          await fetch(`${BASE}/api/books/${book.id}/pdf-jobs/${job_id}/cancel`, {
            method: 'POST', headers: authHeaders,
          });
        } catch { /* best-effort — frontend has already stopped polling. */ }
        throw new Error('Cancelled by user');
      }
      // Poll status. Cap at ~10 min so a cold start (Chromium install) or a
      // very large book still has time to finish. Each individual request
      // is sub-second; only the wall-clock can grow.
      const STATUS_URL = `${BASE}/api/books/${book.id}/pdf-jobs/${job_id}`;
      const start = Date.now();
      let lastStatus = 'pending';
      let lastStage = '';
      let serverFilename = null;
      // Parse the streaming Ghostscript / chunk-render fraction out of the
      // stage string so the toast renders a real progress bar.
      // Matches "converting to PDF/X-1a (12/100)" and "rendering chunk 2/5".
      const STAGE_FRACTION = /\((\d+)\s*\/\s*(\d+)\)|(\d+)\s*\/\s*(\d+)\s*$/;
      // Persist the last numeric fraction we saw. Between rendering-chunks
      // and the next ticking phase (merging / uploading / Ghostscript warm-
      // up) the stage string has no fraction — without this, the bar would
      // collapse to an easy-to-miss indeterminate sweep and the user would
      // think it disappeared. We keep the bar pinned full (`done = total`)
      // during these short transitions so progress always looks continuous.
      // Reset on phase change (different denominator) so the next phase
      // restarts cleanly.
      let lastFraction = { done: 0, total: 0 };
      // Per-stage idle deadline. Resets every time we see a NEW stage
      // string from the server — so an actively progressing job (e.g.
      // a 60-page book that chunks through 12 "rendering chunk N/M"
      // updates) is never killed for taking too long overall. Only a
      // genuine stall (no stage change for N minutes) triggers the
      // timeout error. This replaces the old wall-clock-from-start
      // budget which falsely killed big art-heavy books at 10 min.
      // 6 min ceiling: pre-fetch of a single oversized PNG (e.g.
      // 12000×12000) at 600 DPI can take ~1 min to downscale; we want
      // headroom above any legitimate single-step duration so a
      // genuinely slow operation still completes.
      const STAGE_IDLE_DEADLINE_MS = 360_000; // 6 min without ANY stage change
      let stageDeadline = Date.now() + STAGE_IDLE_DEADLINE_MS;
      let prevStage = '';
      while (Date.now() < stageDeadline) {
        if (cancelState.cancelled) {
          // Local cancel already fired the backend cancel request. Bail.
          throw new Error('Cancelled by user');
        }
        await new Promise((r) => setTimeout(r, 1200));
        const s = await fetch(STATUS_URL, { headers: authHeaders });
        if (!s.ok) {
          if (s.status === 404) throw new Error('PDF job expired — please try again');
          continue; // transient — keep polling
        }
        const sb = await s.json();
        lastStatus = sb.status;
        if (sb.stage) {
          // Reset the idle deadline ONLY when the stage string actually
          // changes. Heartbeat emissions like "rendering chunk 3/12"
          // count as progress; a frozen stage for 4+ minutes is a real
          // stall and we surface the timeout error.
          if (sb.stage !== prevStage) {
            prevStage = sb.stage;
            stageDeadline = Date.now() + STAGE_IDLE_DEADLINE_MS;
          }
          lastStage = sb.stage;
        }
        if (sb.status === 'ready') { serverFilename = sb.filename || null; break; }
        if (sb.status === 'failed') {
          const detail = sb.error || 'PDF build failed';
          // "Cancelled by user" is not an error — surface a calm message.
          if (detail.toLowerCase().includes('cancelled')) {
            throw new Error('Cancelled by user');
          }
          // Prefer the rich `error` (which now includes the failing stage
          // + timings dict from pdf_builder._render_chunk_safe) over the
          // raw traceback. Only fall through to the trace when `error`
          // looks bare ("PDF build failed", empty, or shorter than 40
          // chars) — the trace is line noise in every other case.
          const useTrace = sb.trace && detail.trim().length < 40;
          const tail = useTrace ? ` (${String(sb.trace).slice(0, 240)})` : '';
          throw new Error(`${detail}${tail}`);
        }
        // Otherwise (pending) — update the custom toast with stage + fraction.
        // Skip the redraw if a cancel landed between the fetch and now,
        // otherwise we'd briefly overwrite the "Cancelling…" message.
        if (cancelState.cancelled) {
          throw new Error('Cancelled by user');
        }
        const elapsedSec = Math.round((Date.now() - start) / 1000);
        let done = 0;
        let total = 0;
        const match = STAGE_FRACTION.exec(lastStage || '');
        if (match) {
          done = parseInt(match[1] || match[3], 10);
          total = parseInt(match[2] || match[4], 10);
          // Phase change? (new denominator) Reset the sticky carry-over so
          // the bar restarts from the new phase's first tick.
          lastFraction = { done, total };
        } else if (lastFraction.total > 0) {
          // Transitional stage with no numeric fraction (e.g. "merging
          // chunks", "uploading", or Ghostscript warming up before its
          // first page tick). Pin the bar at 100% of the last known phase
          // so the user sees continuous progress instead of a vanishing
          // sweep.
          done = lastFraction.total;
          total = lastFraction.total;
        }
        toast.custom(
          () => renderToast({ stage: lastStage || 'Working…', done, total, elapsedSec }),
          { id: toastId, duration: Infinity },
        );
      }
      if (lastStatus !== 'ready') {
        const stageHint = lastStage ? ` (stuck at: ${lastStage})` : '';
        throw new Error(`PDF timed out${stageHint} — try again or check /api/pdf-health`);
      }

      // Stream the bytes — this is a fast, fully-buffered response, so no
      // proxy timeout risk.
      const dl = await fetch(`${BASE}/api/books/${book.id}/pdf-jobs/${job_id}/download`, {
        headers: authHeaders,
      });
      if (!dl.ok) throw new Error(`Download failed (HTTP ${dl.status})`);
      const blob = await dl.blob();
      // Prefer the server's filename (carries the page-range suffix for
      // partial exports). Fall back to a sanitised title if missing.
      const fallback = `${(book.title || 'book').replace(/[^a-z0-9-_]+/gi, '_')}.pdf`;
      const downloadName = serverFilename || fallback;
      const url = URL.createObjectURL(blob);
      // Tracks whether we successfully invoked iframe.contentWindow.print().
      // If false (Safari blocked us, or hard timeout opened a new tab),
      // the success toast tells the user to press Cmd/Ctrl+P themselves.
      let printIframeWorked = false;
      if (printAfter) {
        // Hidden iframe → browser PDF viewer renders the blob → we call
        // .print() once it's loaded so the user lands directly on the
        // OS print dialog. Same-origin blob URL so contentWindow access
        // is allowed in Chrome/Edge. Safari sometimes blocks the
        // programmatic print() call against a PDF iframe — in that
        // case we fall back to opening the blob in a new tab so the
        // user can press Cmd/Ctrl+P themselves.
        let printed = false;
        const iframe = document.createElement('iframe');
        iframe.setAttribute('aria-hidden', 'true');
        iframe.style.position = 'fixed';
        iframe.style.left = '-9999px';
        iframe.style.width = '1px';
        iframe.style.height = '1px';
        iframe.style.border = '0';
        iframe.src = url;
        document.body.appendChild(iframe);
        const triggerPrint = () => {
          // 300ms settle so the PDF plugin has actually rendered. Some
          // browsers fire load before the plugin is interactive.
          setTimeout(() => {
            try {
              iframe.contentWindow.focus();
              iframe.contentWindow.print();
              printed = true;
              printIframeWorked = true;
            } catch {
              window.open(url, '_blank', 'noopener,noreferrer');
            }
          }, 300);
        };
        iframe.onload = triggerPrint;
        // Hard fallback: if iframe load never fires within 8s, open in
        // a new tab so the user isn't stuck with no print dialog.
        setTimeout(() => { if (!printed) window.open(url, '_blank', 'noopener,noreferrer'); }, 8000);
        // Reclaim the iframe + blob URL after a generous delay; the
        // browser holds a reference for the print preview until the
        // user dismisses it, then GC is fine.
        setTimeout(() => {
          if (iframe.parentNode) iframe.parentNode.removeChild(iframe);
          URL.revokeObjectURL(url);
        }, 120_000);
      } else if (previewInTab) {
        // Open in a new tab so the browser's built-in PDF viewer renders
        // it. We do NOT immediately revoke the object URL — the new tab
        // still needs it. Schedule revoke after a generous delay; the
        // browser caches the blob, so navigating away from the tab is
        // fine, and worst case the OS reclaims the memory on tab close.
        const win = window.open(url, '_blank', 'noopener,noreferrer');
        if (!win) {
          // Popup blocked — fall back to download so the user still gets
          // their PDF rather than a silent no-op.
          const a = document.createElement('a');
          a.href = url;
          a.download = downloadName;
          document.body.appendChild(a);
          a.click();
          a.remove();
          toast.dismiss(toastId);
          toast.warning(
            'Popup blocked — downloaded instead. Allow popups for this site to use Preview-in-tab.',
            { duration: 8000 },
          );
          // Old toast was replaced; create a new "exported" one below.
        }
        // Revoke after 60s; the new tab has well-cached the bytes by then.
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      } else {
        const a = document.createElement('a');
        a.href = url;
        a.download = downloadName;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      }
      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      // Explicitly dismiss the custom progress toast before showing the
      // success message. Sonner's `toast.success({ id })` does not
      // reliably replace a `toast.custom` render — the progress bar
      // lingers visually. Dismiss + fresh toast gives a clean swap.
      toast.dismiss(toastId);
      toast.success(
        printAfter
          ? (printIframeWorked
              ? `PDF ready in ${secs}s — opening printer…`
              : `PDF ready in ${secs}s — opened in new tab, press Cmd/Ctrl+P to print`)
          : previewInTab
            ? `PDF export complete in ${secs}s — opened in new tab`
            : `PDF export complete in ${secs}s`,
        // Persist until the user dismisses it so a completed job is
        // never missed (they might have switched tabs while it ran).
        { duration: Infinity, closeButton: true },
      );
      // Refresh the export-history list so the popover updates immediately.
      setExportsBump((n) => n + 1);
    } catch (e) {
      console.error(e);
      const raw = e?.message || 'Export failed';
      // Dismiss the custom progress toast so the new message replaces it
      // cleanly — `toast.error({ id })` against a `toast.custom` does not
      // reliably swap the render in Sonner.
      toast.dismiss(toastId);
      // A cancellation isn't an error — show a calm neutral toast.
      if (cancelState.cancelled || raw.toLowerCase().includes('cancelled by user')) {
        toast.message('PDF export cancelled', {
          duration: Infinity, closeButton: true,
        });
        return;
      }
      const isNetwork =
        e?.name === 'AbortError' ||
        /failed to fetch|networkerror|load failed/i.test(raw);
      const friendly = isNetwork
        ? 'PDF export failed: couldn\'t reach the PDF service. Check your connection and try again.'
        : `PDF export failed: ${raw.slice(0, 200)}`;
      // Errors and cancellations stay until dismissed so the user sees
      // them even after switching tabs / scrolling away.
      toast.error(friendly, { duration: Infinity, closeButton: true });
    } finally {
      setExporting(false);
    }
  };

  const onFileHealthCancel = () => {
    setFileHealthProblems(null);
    setPendingExport(null);
  };
  const onFileHealthProceed = () => {
    const p = pendingExport;
    setFileHealthProblems(null);
    setPendingExport(null);
    if (p) {
      onExportPdf(p.range, { ...(p.options || {}), __skipHealthCheck: true });
    }
  };

  return {
    onExportPdf,
    exporting,
    exportsBump,
    fileHealthProblems,
    onFileHealthCancel,
    onFileHealthProceed,
  };
}
