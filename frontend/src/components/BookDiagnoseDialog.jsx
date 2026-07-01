/**
 * BookDiagnoseDialog — admin-only per-page inspector.
 *
 * Calls GET /api/admin/book-diagnose/{book_id}?page_no=N&probe_bytes=true
 * via the shared axios `api` client. Read-only endpoint — no side
 * effects — so this dialog is safe to run against a production book
 * from the browser to identify why a specific page hangs WeasyPrint.
 *
 * Fields pre-fill with the current book id and page number. The result
 * is translated into ONE plain-English diagnosis line covering the
 * failure modes the endpoint surfaces:
 *
 *   - probe.decode_ok === false                  → corrupt image bytes
 *   - file_record === null                       → orphaned reference
 *   - width_px === 0 / null / missing            → legacy pre-validation
 *   - anything else, all clean                   → page looks healthy
 *
 * When multiple image blocks are on the page, we report the FIRST
 * problematic one (any single failure is enough to hang WeasyPrint —
 * the operator will re-run the diagnostic after fixing it).
 */
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Loader2, AlertCircle, AlertTriangle, CheckCircle2 } from 'lucide-react';

/**
 * Translate the endpoint's page report into a UI-ready diagnosis.
 * Returns { severity: 'ok' | 'warn' | 'error', headline, details, block }.
 * Deliberately terse — the operator wants ONE line of advice.
 */
function diagnose(pageReport) {
  if (!pageReport) {
    return {
      severity: 'error',
      headline: 'Page not found',
      details: 'The endpoint returned no data for that page number.',
      block: null,
    };
  }
  const imageBlocks = (pageReport.blocks || []).filter((b) => b.type === 'image');
  if (imageBlocks.length === 0 && !pageReport.background_image_path) {
    return {
      severity: 'ok',
      headline: 'Page has no image blocks',
      details: 'This page is text-only — WeasyPrint hangs on this page are unlikely to be image-related.',
      block: null,
    };
  }

  // Include a synthetic background-block if there is one, so the same
  // triage logic covers both block-level and page-level images.
  const targets = [...imageBlocks];
  if (pageReport.background_image_path) {
    targets.unshift({
      type: 'background',
      storage_path: pageReport.background_image_path,
      file_record: pageReport.background_file_record,
      probe: pageReport.background_probe,
    });
  }

  for (const b of targets) {
    // Priority 1: byte-probe decode failure — the WeasyPrint smoking gun.
    if (b.probe && b.probe.decode_ok === false) {
      return {
        severity: 'error',
        headline: 'This image appears corrupted.',
        details: 'Delete the image block on this page and re-upload the file.',
        block: b,
      };
    }
    // Priority 2: no db.files record at all — orphan reference.
    if (b.file_record === null || b.file_record === undefined) {
      return {
        severity: 'error',
        headline: 'No file record found.',
        details: 'This image is orphaned — delete the block and re-upload.',
        block: b,
      };
    }
    // Priority 3: missing/zero dimensions — legacy pre-validation upload.
    const w = b.file_record?.width_px;
    const h = b.file_record?.height_px;
    if (!w || !h) {
      return {
        severity: 'error',
        headline: 'This image has no stored dimensions.',
        details: 'Delete the block and re-upload.',
        block: b,
      };
    }
    // Priority 4: zero-byte file that somehow slipped past validation.
    if (b.file_record?.size === 0) {
      return {
        severity: 'error',
        headline: 'This image is 0 bytes.',
        details: 'Delete the block and re-upload.',
        block: b,
      };
    }
    // Priority 5: probe reachable=false — file is missing from object
    // storage even though a db.files record exists.
    if (b.probe && b.probe.reachable === false) {
      return {
        severity: 'error',
        headline: 'Image bytes are not reachable in object storage.',
        details: `Fetch error: ${b.probe.decode_error || 'unknown'}. Re-upload the image.`,
        block: b,
      };
    }
  }

  return {
    severity: 'ok',
    headline: 'Page looks healthy.',
    details: 'The hang may be environmental — try exporting just this page range.',
    block: null,
  };
}

export default function BookDiagnoseDialog({
  open,
  onOpenChange,
  defaultBookId = '',
  defaultPageNo = 1,
}) {
  const [bookId, setBookId] = useState(defaultBookId);
  const [pageNo, setPageNo] = useState(String(defaultPageNo || 1));
  const [includeTextContent, setIncludeTextContent] = useState(true);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  // Text-length signal for the requested page and its two neighbours
  // (page-1 and page+1) — lets the operator see instantly whether a
  // hanging page has a runaway text block relative to its neighbours.
  // Fetched with include_text_content=false (metadata only) so this
  // stays cheap even on huge books.
  const [neighbours, setNeighbours] = useState(null); // {prev:{no,chars}, next:{no,chars}}
  const [error, setError] = useState(null);

  useEffect(() => {
    if (open) {
      setBookId(defaultBookId);
      setPageNo(String(defaultPageNo || 1));
      setResult(null);
      setNeighbours(null);
      setError(null);
    }
  }, [open, defaultBookId, defaultPageNo]);

  const run = async () => {
    setLoading(true);
    setResult(null);
    setNeighbours(null);
    setError(null);
    try {
      const n = Math.max(1, parseInt(pageNo, 10) || 1);
      const params = {
        page_no: n,
        probe_bytes: true,
        ...(includeTextContent ? { include_text_content: true } : {}),
      };
      const r = await api.get(
        `/admin/book-diagnose/${encodeURIComponent(bookId)}`,
        { params },
      );
      setResult(r.data);

      // Fire off two lightweight metadata-only requests for the
      // neighbour pages so the operator sees a text-length comparison
      // without extra typing. Silent on failure — this is a nice-to-
      // have, not the diagnosis.
      const total = r.data?.total_pages || 0;
      const jobs = [];
      if (n > 1) jobs.push([n - 1, 'prev']);
      if (n < total) jobs.push([n + 1, 'next']);
      const results = {};
      await Promise.all(jobs.map(async ([pn, key]) => {
        try {
          const nr = await api.get(
            `/admin/book-diagnose/${encodeURIComponent(bookId)}`,
            { params: { page_no: pn, probe_bytes: false, include_text_content: false } },
          );
          const pg = nr.data?.pages?.[0];
          if (pg) {
            const chars = (pg.blocks || [])
              .filter((b) => b.type === 'text')
              .reduce((sum, b) => sum + (b.text_length_chars || 0), 0);
            results[key] = { no: pn, chars, fonts_missing: pg.fonts_missing_from_cache || [] };
          }
        } catch { /* silent */ }
      }));
      setNeighbours(results);
    } catch (e) {
      const status = e?.response?.status;
      if (status === 401) setError('Not authenticated — sign in again.');
      else if (status === 403) setError('Admin access required.');
      else if (status === 404) setError('Book not found — check the ID.');
      else if (status) setError(`Request failed (HTTP ${status}).`);
      else setError(`Network error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const pageReport = result?.pages?.[0];
  const diagnosis = result ? diagnose(pageReport) : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="bg-paper border-rule rounded-sm max-w-2xl"
        data-testid="book-diagnose-dialog"
      >
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl text-ink">Diagnose page</DialogTitle>
          <DialogDescription className="text-sm text-ink-soft">
            Runs a read-only per-page check against the book — no render is
            triggered, no data is modified. Use this to identify which
            image on which page causes a stuck export.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="diag-book-id" className="text-xs text-ink-soft">Book ID</Label>
            <Input
              id="diag-book-id"
              value={bookId}
              onChange={(e) => setBookId(e.target.value)}
              placeholder="dc988a6f-5595-…"
              data-testid="book-diagnose-book-id"
              className="font-mono text-sm rounded-sm border-rule"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="diag-page-no" className="text-xs text-ink-soft">Page number (1-based)</Label>
            <Input
              id="diag-page-no"
              type="number"
              min="1"
              value={pageNo}
              onChange={(e) => setPageNo(e.target.value)}
              data-testid="book-diagnose-page-no"
              className="w-32 font-mono text-sm rounded-sm border-rule"
            />
          </div>
          {/* Include-text-content toggle. Default ON — the operator
              usually wants the full evidence when triaging a hang.
              Turning it OFF is a lightweight metadata scan (no HTML
              payload, no font capture), useful for scanning a book
              quickly without transferring page-worth of prose. */}
          <label
            className="flex items-center gap-2 text-sm text-ink-soft cursor-pointer select-none"
            data-testid="book-diagnose-include-text-label"
          >
            <Checkbox
              checked={includeTextContent}
              onCheckedChange={(v) => setIncludeTextContent(v === true)}
              data-testid="book-diagnose-include-text"
            />
            <span>Include text content (fonts, HTML, character count)</span>
          </label>
          <Button
            onClick={run}
            disabled={loading || !bookId || !pageNo}
            data-testid="book-diagnose-run"
            className="rounded-sm h-9 bg-terracotta hover:bg-terracotta/90 text-paper"
          >
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                Probing…
              </>
            ) : 'Run diagnostic'}
          </Button>
        </div>

        {error && (
          <div
            className="mt-2 bg-paper border border-terracotta text-terracotta rounded-sm px-3 py-2 text-sm"
            data-testid="book-diagnose-error"
          >
            {error}
          </div>
        )}

        {result && (
          <>
            {/* Missing-fonts warning — amber banner rendered ABOVE the
                main diagnosis so it's the first thing the operator sees.
                A font referenced by a block but not in the local cache
                forces WeasyPrint through fontconfig's system chain,
                which is where several observed hangs have originated. */}
            {(pageReport?.fonts_missing_from_cache || []).length > 0 && (
              <div
                className="mt-2 rounded-sm border border-amber-500/40 bg-amber-50 px-3 py-2"
                data-testid="book-diagnose-missing-fonts"
              >
                <div className="flex items-start gap-2">
                  <AlertTriangle className="w-5 h-5 text-amber-700 shrink-0 mt-0.5" />
                  <div className="text-sm text-amber-900">
                    <div className="font-serif text-base">
                      Warning: these fonts are not in the cache and may cause a render hang:
                    </div>
                    <div className="mt-1 font-mono text-xs">
                      {(pageReport.fonts_missing_from_cache || []).join(', ')}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Text-length comparison across page-1 / page / page+1 —
                lets the operator see instantly whether the target page
                has a runaway text block relative to its neighbours. */}
            {(neighbours && (neighbours.prev || neighbours.next)) && (
              <div
                className="mt-2 rounded-sm border border-rule/60 bg-paper-soft px-3 py-2"
                data-testid="book-diagnose-neighbours"
              >
                <div className="text-xs text-ink-mute mb-1">Text length comparison (chars):</div>
                <div className="flex gap-4 text-sm font-mono">
                  {neighbours.prev && (
                    <div>
                      <span className="text-ink-mute">Page {neighbours.prev.no}:</span>{' '}
                      <span className="text-ink" data-testid="book-diagnose-neighbour-prev">
                        {neighbours.prev.chars.toLocaleString()}
                      </span>
                    </div>
                  )}
                  <div className="font-semibold">
                    <span className="text-ink-mute">Page {pageReport?.page_no}:</span>{' '}
                    <span className="text-ink" data-testid="book-diagnose-neighbour-this">
                      {((pageReport?.blocks || [])
                        .filter((b) => b.type === 'text')
                        .reduce((s, b) => s + (b.text_length_chars || 0), 0))
                        .toLocaleString()}
                    </span>
                  </div>
                  {neighbours.next && (
                    <div>
                      <span className="text-ink-mute">Page {neighbours.next.no}:</span>{' '}
                      <span className="text-ink" data-testid="book-diagnose-neighbour-next">
                        {neighbours.next.chars.toLocaleString()}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            )}
          </>
        )}

        {diagnosis && (
          <div
            className={
              'mt-2 rounded-sm px-3 py-3 border ' +
              (diagnosis.severity === 'ok'
                ? 'border-green-700/40 bg-green-50'
                : 'border-red-700/40 bg-red-50')
            }
            data-testid="book-diagnose-result"
          >
            <div className="flex items-start gap-2">
              {diagnosis.severity === 'ok' ? (
                <CheckCircle2 className="w-5 h-5 text-green-700 shrink-0 mt-0.5" />
              ) : (
                <AlertCircle className="w-5 h-5 text-red-700 shrink-0 mt-0.5" />
              )}
              <div className="flex-1">
                <div
                  className="font-serif text-lg text-ink"
                  data-testid="book-diagnose-headline"
                >
                  {diagnosis.headline}
                </div>
                <div className="text-sm text-ink-soft mt-1">
                  {diagnosis.details}
                </div>
                {diagnosis.block && (
                  <div className="mt-2 text-xs text-ink-mute font-mono break-all">
                    Affected block:{' '}
                    {diagnosis.block.storage_path || '(no storage path)'}
                  </div>
                )}
              </div>
            </div>
            {/* Extra context — page-level facts the operator may want to see. */}
            {result && (
              <div className="mt-3 text-xs text-ink-mute grid grid-cols-2 gap-x-4 gap-y-0.5">
                <div>Book: <span className="text-ink-soft">{result.title || '(untitled)'}</span></div>
                <div>Total pages: {result.total_pages}</div>
                <div>Blocks on this page: {pageReport?.block_count ?? 0}</div>
                <div>Bytes probed: {String(result.probed_bytes)}</div>
              </div>
            )}
          </div>
        )}

        {/* Raw JSON — collapsed by default so power users can dig without
            the dialog getting noisy. */}
        {result && (
          <details className="mt-2 text-xs">
            <summary className="cursor-pointer text-ink-mute hover:text-ink-soft select-none">
              Raw response JSON
            </summary>
            <pre
              className="mt-1 p-2 bg-paper-soft border border-rule/40 rounded-sm overflow-auto max-h-64 font-mono text-[11px] leading-snug"
              data-testid="book-diagnose-raw-json"
            >
              {JSON.stringify(result, null, 2)}
            </pre>
          </details>
        )}
      </DialogContent>
    </Dialog>
  );
}

// Re-export the pure translator so tests can exercise it without the
// component/network roundtrip.
export { diagnose };
