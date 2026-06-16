import { useEffect, useState, useCallback } from 'react';
import { ShieldCheck, ShieldAlert, ShieldX, RefreshCw } from 'lucide-react';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';

/**
 * IngramSpark v5.11.26 compliance badge + popover panel.
 *
 * Renders as a small pill in the editor toolbar. Colour reflects the
 * worst-case status from `GET /api/books/{id}/preflight`:
 *   - green shield + "Print-ready"  → no errors, no warnings
 *   - amber shield + "N warnings"   → warnings only
 *   - red shield + "N errors"       → errors block submission
 *
 * Clicking opens a popover listing each row with actionable detail
 * (page number, block id, what's wrong, how to fix). Refresh button
 * re-fetches on demand; otherwise the parent passes a `revision` prop
 * that bumps on book saves to trigger an auto-refresh.
 */
export default function CompliancePanel({ apiBase, bookId, token, revision }) {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);

  // Inline the fetch logic so we don't trip the React Compiler's
  // setState-in-effect heuristic (a refresh function defined outside the
  // effect that calls setState is flagged even though it's the canonical
  // data-fetching pattern). The refresh handler binds to the same closure.
  const fetchReport = async (signal) => {
    if (!bookId) return;
    setLoading(true);
    try {
      const r = await fetch(`${apiBase}/api/books/${bookId}/preflight`, {
        headers: { Authorization: `Bearer ${token}` },
        signal,
      });
      if (r.ok) {
        const data = await r.json();
        if (!signal?.aborted) setReport(data);
      }
    } catch (e) {
      if (e?.name !== 'AbortError') {
        // Surface failures only in dev — preflight is a nice-to-have, not
        // critical, so a transient failure should not block the export UX.
        console.warn('preflight fetch failed', e);
      }
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  };

  useEffect(() => {
    if (!bookId) return undefined;
    const ac = new AbortController();
    (async () => {
      setLoading(true);
      try {
        const r = await fetch(`${apiBase}/api/books/${bookId}/preflight`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: ac.signal,
        });
        if (r.ok && !ac.signal.aborted) {
          setReport(await r.json());
        }
      } catch (e) {
        if (e?.name !== 'AbortError') {
          console.warn('preflight fetch failed', e);
        }
      } finally {
        if (!ac.signal.aborted) setLoading(false);
      }
    })();
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookId, revision]);

  const refresh = useCallback(() => {
    fetchReport();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookId, apiBase, token]);

  const status = report?.status || 'ok';
  const errorCount = report?.errors?.length || 0;
  const warningCount = report?.warnings?.length || 0;

  // Tone the badge to the worst severity present.
  const tone =
    status === 'errors' ? 'error'
    : status === 'warnings' ? 'warn'
    : 'ok';

  const Icon = tone === 'error' ? ShieldX : tone === 'warn' ? ShieldAlert : ShieldCheck;
  const colourClass =
    tone === 'error' ? 'text-red-700 bg-red-50 border-red-200 hover:bg-red-100'
    : tone === 'warn' ? 'text-amber-700 bg-amber-50 border-amber-200 hover:bg-amber-100'
    : 'text-emerald-700 bg-emerald-50 border-emerald-200 hover:bg-emerald-100';

  const label =
    tone === 'error' ? `${errorCount} error${errorCount === 1 ? '' : 's'}`
    : tone === 'warn' ? `${warningCount} warning${warningCount === 1 ? '' : 's'}`
    : 'Print-ready';

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid="compliance-badge"
          className={`hidden lg:flex items-center gap-1.5 px-2.5 h-8 rounded-sm text-xs font-medium border transition-colors ${colourClass}`}
          title="IngramSpark v5.11.26 compliance"
        >
          <Icon className="w-3.5 h-3.5" />
          {label}
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="bg-paper border-rule rounded-sm w-96 p-0"
        data-testid="compliance-panel"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-rule">
          <div>
            <div className="text-[10px] tracking-[0.18em] uppercase text-ink-mute">IngramSpark</div>
            <div className="text-sm font-medium">v5.11.26 preflight</div>
          </div>
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            className="p-1.5 rounded-sm hover:bg-desk text-ink-mute"
            title="Re-check now"
            data-testid="compliance-refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        <div className="max-h-[60vh] overflow-y-auto">
          {!report && (
            <div className="px-4 py-8 text-center text-xs text-ink-mute">
              Loading compliance check…
            </div>
          )}

          {report?.errors?.map((e) => (
            <ComplianceRow key={`e-${e.check}`} kind="error" row={e} />
          ))}
          {report?.warnings?.map((w) => (
            <ComplianceRow key={`w-${w.check}`} kind="warning" row={w} />
          ))}

          {report?.status === 'ok' && (
            <div className="px-4 py-6 text-center">
              <ShieldCheck className="w-8 h-8 mx-auto text-emerald-600 mb-2" />
              <div className="text-sm font-medium text-ink">All checks passed</div>
              <div className="text-xs text-ink-mute mt-1">
                {report.page_count} page{report.page_count === 1 ? '' : 's'} · ready for IngramSpark submission
              </div>
            </div>
          )}

          {report?.passed?.length > 0 && (
            <div className="px-4 py-3 border-t border-rule">
              <div className="text-[10px] tracking-[0.18em] uppercase text-ink-mute mb-1.5">Passed</div>
              <div className="flex flex-wrap gap-1">
                {report.passed.map((p) => (
                  <span key={p} className="text-[10px] px-1.5 py-0.5 rounded-sm bg-emerald-50 text-emerald-700">
                    {p.replace(/_/g, ' ')}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function ComplianceRow({ kind, row }) {
  const Icon = kind === 'error' ? ShieldX : ShieldAlert;
  const tone = kind === 'error' ? 'text-red-700' : 'text-amber-700';
  return (
    <div className="px-4 py-3 border-b border-rule last:border-b-0">
      <div className="flex items-start gap-2">
        <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${tone}`} />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-medium text-ink leading-snug">
            {row.msg}
          </div>
          {row.items && row.items.length > 0 && (
            <ul className="mt-1.5 space-y-0.5">
              {row.items.slice(0, 8).map((it, i) => (
                <li key={i} className="text-[11px] text-ink-mute tabular-nums">
                  {it.page != null && <>page {it.page}</>}
                  {it.effective_dpi != null && (
                    <> · {it.effective_dpi} DPI (need ≥{it.min_required_dpi})</>
                  )}
                  {it.total_ink_pct != null && (
                    <> · {it.total_ink_pct}% ink ({it.kind === 'page_bg' ? 'background' : 'text colour'})</>
                  )}
                </li>
              ))}
              {row.items.length > 8 && (
                <li className="text-[11px] text-ink-mute italic">
                  …and {row.items.length - 8} more
                </li>
              )}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
