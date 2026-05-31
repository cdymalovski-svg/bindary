import { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, Loader2, FileDown, Trash2, Sparkles } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { api } from '@/lib/api';

/**
 * Export-PDF popover. Two modes:
 *   1. Export the whole book (single click).
 *   2. Export a contiguous page range — the user types start/end, hits
 *      "Export range", and gets a PDF named `<Title>_pp_<a>-<b>.pdf`.
 *
 * Below the controls we render the export history for this book so users
 * stitching a 100+ page book in chunks can see which ranges have already
 * been delivered and avoid duplicating work. Each historical run can be
 * removed from the list (e.g. if the file was lost on disk and they need
 * to re-export the same range without it looking duplicated).
 */
export default function ExportPopover({ bookId, totalPages, exporting, onExport, refreshKey = 0 }) {
  const [open, setOpen] = useState(false);
  const [startPage, setStartPage] = useState('1');
  const [endPage, setEndPage] = useState(String(totalPages || 1));
  // When true, the resulting PDF opens in a new browser tab (uses the
  // built-in viewer) instead of downloading to disk. Saved per-session in
  // localStorage so users who iterate on a book stay in their preferred
  // workflow without re-toggling each export.
  const [previewInTab, setPreviewInTab] = useState(() => {
    try { return localStorage.getItem('bindery_preview_in_tab') === '1'; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem('bindery_preview_in_tab', previewInTab ? '1' : '0'); } catch {}
  }, [previewInTab]);
  // Print-ready (PDF/X-1a:2001) toggle — when on, the backend post-processes
  // the exported PDF through Ghostscript: converts every color to CMYK,
  // embeds all fonts, flattens transparency, and bakes a SWOP v2 ICC
  // OutputIntent into the file. Required by most offset / commercial
  // printers. Adds 10–30s to export time, so kept opt-in.
  const [pdfx, setPdfx] = useState(() => {
    try { return localStorage.getItem('bindery_pdfx') === '1'; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem('bindery_pdfx', pdfx ? '1' : '0'); } catch {}
  }, [pdfx]);
  // Optional explicit spine width (inches) — used when exporting the cover
  // spread. Empty string = auto-compute from interior page count + paper
  // caliper (IngramSpark white-paper default 0.002252 in/page).
  const [spineWidthIn, setSpineWidthIn] = useState(() => {
    try { return localStorage.getItem('bindery_spine_width_in') || ''; } catch { return ''; }
  });
  useEffect(() => {
    try { localStorage.setItem('bindery_spine_width_in', spineWidthIn); } catch {}
  }, [spineWidthIn]);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // Reset the range inputs whenever the book length actually changes so the
  // defaults stay sensible. We intentionally avoid resetting on every render.
  useEffect(() => {
    setEndPage(String(totalPages || 1));
  }, [totalPages]);

  const loadHistory = useCallback(async () => {
    if (!bookId) return;
    setHistoryLoading(true);
    try {
      const { data } = await api.get(`/books/${bookId}/exports`);
      setHistory(Array.isArray(data) ? data : []);
    } catch (e) {
      // History is non-critical — silent failure is fine, user can still export.
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  }, [bookId]);

  useEffect(() => {
    if (open) loadHistory();
  }, [open, refreshKey, loadHistory]);

  const handleDelete = async (id) => {
    try {
      await api.delete(`/books/${bookId}/exports/${id}`);
      setHistory((prev) => prev.filter((h) => h.id !== id));
    } catch {
      /* swallow — popover will refresh next open */
    }
  };

  // Build a "covered pages" set so we can visualise which pages have already
  // been exported (regardless of whether they came from a single full export
  // or many partial exports). Used to colour-shade the coverage bar.
  const coveredPages = useMemo(() => {
    const set = new Set();
    for (const h of history) {
      const a = Math.max(1, h.start_page || 1);
      const b = Math.min(totalPages || 1, h.end_page || totalPages || 1);
      for (let p = a; p <= b; p += 1) set.add(p);
    }
    return set;
  }, [history, totalPages]);

  const allCovered = totalPages > 0 && coveredPages.size >= totalPages;

  const submitRange = (e) => {
    e?.preventDefault?.();
    const s = parseInt(startPage, 10);
    const en = parseInt(endPage, 10);
    if (!Number.isFinite(s) || !Number.isFinite(en)) return;
    if (s < 1 || en < 1 || s > en || s > totalPages) return;
    setOpen(false);
    onExport({ start: s, end: Math.min(en, totalPages) }, { previewInTab, pdfx });
  };

  const submitFull = () => {
    setOpen(false);
    onExport(null, { previewInTab, pdfx });
  };

  // Cover spread = a single wide PDF with BACK | SPINE | FRONT layout plus
  // 0.125" bleed on every outside edge — the exact file IngramSpark and
  // similar perfect-bound POD printers expect.
  const submitCoverSpread = () => {
    setOpen(false);
    const spineParsed = parseFloat(spineWidthIn);
    const spine = Number.isFinite(spineParsed) && spineParsed > 0 ? spineParsed : null;
    onExport(null, { previewInTab, pdfx, coverSpread: true, spineWidthIn: spine });
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          disabled={exporting}
          className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm h-8 shrink-0 px-2 lg:px-3"
          data-testid="export-pdf-button"
        >
          {exporting ? <Loader2 className="w-4 h-4 lg:mr-1 animate-spin" /> : <Download className="w-4 h-4 lg:mr-1" />}
          <span className="hidden lg:inline">Export PDF</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-96 bg-paper border-rule rounded-sm p-0 shadow-xl"
        data-testid="export-popover"
      >
        {/* Header + whole-book CTA */}
        <div className="px-4 pt-4 pb-3 border-b border-rule">
          <p className="label-caps text-ink-mute mb-2">Export to PDF</p>
          {/* Preview-in-tab toggle — applies to BOTH the "Whole book" and
              "Range" actions below. Persists across sessions. */}
          <label
            className="flex items-center gap-2 mb-3 text-xs text-ink-soft cursor-pointer select-none"
            data-testid="preview-in-tab-label"
          >
            <input
              type="checkbox"
              checked={previewInTab}
              onChange={(e) => setPreviewInTab(e.target.checked)}
              data-testid="preview-in-tab-toggle"
              className="w-4 h-4 accent-terracotta cursor-pointer"
            />
            <span>Open in new tab instead of downloading</span>
          </label>
          <label
            className="flex items-start gap-2 mb-3 text-xs text-ink-soft cursor-pointer select-none"
            data-testid="pdfx-label"
          >
            <input
              type="checkbox"
              checked={pdfx}
              onChange={(e) => setPdfx(e.target.checked)}
              data-testid="pdfx-toggle"
              className="w-4 h-4 mt-px accent-terracotta cursor-pointer"
            />
            <span className="leading-snug">
              <span className="text-ink">Print-ready (PDF/X-1a:2001)</span>
              <span className="block text-[10px] text-ink-mute mt-0.5">
                CMYK, embedded fonts, SWOP v2 OutputIntent. Required by most commercial printers. Adds ~15–30s.
              </span>
            </span>
          </label>
          <Button
            onClick={submitFull}
            disabled={exporting || totalPages === 0}
            className="w-full bg-ink hover:bg-ink-soft text-paper rounded-sm justify-start h-10"
            data-testid="export-whole-book"
          >
            <FileDown className="w-4 h-4 mr-2" />
            <span>Whole book</span>
            <span className="ml-auto text-paper/60 text-xs">{totalPages} page{totalPages === 1 ? '' : 's'}</span>
          </Button>
        </div>

        {/* Range picker */}
        <form onSubmit={submitRange} className="px-4 py-3 border-b border-rule">
          <p className="text-xs text-ink-mute mb-2">
            Or export a page range — useful for very long books you want to assemble externally.
          </p>
          <div className="flex items-end gap-2">
            <div className="flex-1 space-y-1">
              <Label htmlFor="export-start" className="text-[10px] text-ink-mute uppercase tracking-wider">From page</Label>
              <Input
                id="export-start"
                type="number"
                min={1}
                max={totalPages}
                value={startPage}
                onChange={(e) => setStartPage(e.target.value)}
                className="h-8 bg-white border-rule rounded-sm text-sm"
                data-testid="export-range-start"
              />
            </div>
            <div className="flex-1 space-y-1">
              <Label htmlFor="export-end" className="text-[10px] text-ink-mute uppercase tracking-wider">To page</Label>
              <Input
                id="export-end"
                type="number"
                min={1}
                max={totalPages}
                value={endPage}
                onChange={(e) => setEndPage(e.target.value)}
                className="h-8 bg-white border-rule rounded-sm text-sm"
                data-testid="export-range-end"
              />
            </div>
            <Button
              type="submit"
              disabled={exporting}
              className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm h-8 px-3"
              data-testid="export-range-submit"
            >
              Export
            </Button>
          </div>

          {/* Coverage bar — one tick per page, terracotta if already exported. */}
          {totalPages > 0 && (
            <div className="mt-3" data-testid="export-coverage-bar">
              <div className="flex items-center justify-between mb-1">
                <p className="text-[10px] text-ink-mute uppercase tracking-wider">
                  Coverage · {coveredPages.size}/{totalPages} pages exported
                </p>
                {allCovered && (
                  <span className="inline-flex items-center gap-1 text-[10px] text-terracotta">
                    <Sparkles className="w-3 h-3" /> Complete
                  </span>
                )}
              </div>
              <div className="flex gap-px h-2 bg-rule/40 rounded-sm overflow-hidden">
                {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
                  <div
                    key={p}
                    className={`flex-1 ${coveredPages.has(p) ? 'bg-terracotta' : 'bg-rule'}`}
                    title={`Page ${p}${coveredPages.has(p) ? ' · already exported' : ''}`}
                  />
                ))}
              </div>
            </div>
          )}
        </form>

        {/* Cover spread export — IngramSpark / commercial print layout:
            BACK | SPINE | FRONT on one wide PDF page with 0.125" bleed. */}
        <div className="px-4 py-3 border-b border-rule" data-testid="cover-spread-section">
          <p className="label-caps text-ink-mute mb-1">Print-ready cover</p>
          <p className="text-xs text-ink-mute mb-2 leading-snug">
            Single wide PDF: back · spine · front, with 0.125&quot; bleed on every outside edge —
            matches IngramSpark / KDP cover-file specs.
          </p>
          <div className="flex items-end gap-2 mb-2">
            <div className="flex-1 space-y-1">
              <Label htmlFor="spine-width" className="text-[10px] text-ink-mute uppercase tracking-wider">
                Spine width (in)
              </Label>
              <Input
                id="spine-width"
                type="number"
                step="0.001"
                min={0}
                placeholder="auto"
                value={spineWidthIn}
                onChange={(e) => setSpineWidthIn(e.target.value)}
                className="h-8 bg-white border-rule rounded-sm text-sm"
                data-testid="cover-spread-spine-width"
              />
            </div>
            <Button
              type="button"
              onClick={submitCoverSpread}
              disabled={exporting || totalPages < 1}
              className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm h-8 px-3"
              data-testid="export-cover-spread"
            >
              Export cover
            </Button>
          </div>
          <p className="text-[10px] text-ink-mute leading-snug">
            Leave spine blank to auto-compute from page count (white paper, 0.002252&quot; per page).
            For a 100-page book that&apos;s ~0.22&quot;.
          </p>
        </div>

        {/* History list */}
        <div className="px-4 py-3">
          <p className="label-caps text-ink-mute mb-2">Previously exported</p>
          {historyLoading ? (
            <p className="text-xs text-ink-mute italic">Loading history…</p>
          ) : history.length === 0 ? (
            <p className="text-xs text-ink-mute italic">
              Nothing exported yet. Once you do, you'll see what page ranges have been delivered here.
            </p>
          ) : (
            <ul className="space-y-1.5 max-h-48 overflow-y-auto" data-testid="export-history-list">
              {history.map((h) => (
                <li
                  key={h.id}
                  className="flex items-center justify-between gap-2 text-xs bg-white border border-rule rounded-sm px-2.5 py-1.5"
                  data-testid={`export-history-${h.id}`}
                >
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-ink truncate">
                      {h.start_page === 1 && h.end_page === totalPages
                        ? 'Whole book'
                        : `Pages ${h.start_page}–${h.end_page}`}
                    </p>
                    <p className="text-[10px] text-ink-mute truncate">
                      {fmtDate(h.exported_at)} · {fmtSize(h.size)}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleDelete(h.id)}
                    title="Remove from history"
                    data-testid={`export-history-delete-${h.id}`}
                    className="p-1 text-ink-mute hover:text-terracotta rounded-sm shrink-0"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function fmtSize(bytes) {
  if (!Number.isFinite(bytes)) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // Relative-ish formatting that's nicer than a raw ISO timestamp.
  const diff = Date.now() - d.getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr ago`;
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}
