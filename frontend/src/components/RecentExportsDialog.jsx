/**
 * Admin Recent Exports modal — cluster-wide DB query.
 *
 * Calls GET /api/admin/recent-exports (admin-gated). Returns up to the
 * last 20 PDF jobs from the shared `db.pdf_jobs` Mongo collection —
 * cross-pod safe (any pod can see any export's status + summary).
 *
 * Response shape:
 *   {
 *     scope: "cluster-wide",
 *     max: 20,
 *     entries: [
 *       { job_id, book_id, status, error, stage,
 *         filename, size, summary, created_at, finished_at }
 *     ]
 *   }
 *
 *   `summary` is the WeasyPrint "JOB SUMMARY: total=…" line emitted at
 *   the end of each render — the same forensic data we ship to
 *   supervisor logs, now also persisted onto the job doc so cross-pod
 *   visibility works without a centralised log aggregator.
 */
import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Loader2, RefreshCw, CheckCircle2, AlertCircle } from 'lucide-react';

function fmtSize(bytes) {
  if (bytes == null) return '—';
  const mb = bytes / (1024 * 1024);
  if (mb >= 1) return `${mb.toFixed(2)} MB`;
  const kb = bytes / 1024;
  return `${kb.toFixed(0)} KB`;
}

function fmtDuration(startIso, endIso) {
  if (!startIso || !endIso) return '—';
  try {
    const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
    if (!Number.isFinite(ms) || ms < 0) return '—';
    return `${(ms / 1000).toFixed(1)}s`;
  } catch {
    return '—';
  }
}

export default function RecentExportsDialog({ open, onOpenChange }) {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const BASE = process.env.REACT_APP_BACKEND_URL;
      const token = (() => {
        try { return localStorage.getItem('bindery_token'); } catch { return null; }
      })();
      const r = await fetch(`${BASE}/api/admin/recent-exports`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!r.ok) {
        if (r.status === 403) setError('Admin access required.');
        else setError(`Request failed (HTTP ${r.status}).`);
        return;
      }
      setData(await r.json());
    } catch (e) {
      setError(`Network error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  // Auto-load when the dialog opens.
  if (open && data === null && !loading && !error) {
    load();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="bg-paper border-rule rounded-sm max-w-4xl max-h-[80vh] overflow-hidden flex flex-col"
        data-testid="recent-exports-dialog"
      >
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl text-ink">Recent exports</DialogTitle>
          <DialogDescription className="text-sm text-ink-soft">
            Last 20 PDF jobs from the shared <code className="font-mono text-xs">pdf_jobs</code> collection.
            Cluster-wide visibility — any backend pod can see any export&apos;s status, size, and JOB SUMMARY line.
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-3 py-2">
          <Button
            onClick={load}
            disabled={loading}
            data-testid="recent-exports-refresh"
            variant="outline"
            className="rounded-sm h-8 border-rule"
          >
            {loading ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <RefreshCw className="w-4 h-4 mr-2" />}
            Refresh
          </Button>
          {data && (
            <span className="text-xs text-ink-mute italic" data-testid="recent-exports-scope">
              scope: {data.scope || 'cluster-wide'} · showing {(data.entries || []).length} of max {data.max || 20}
            </span>
          )}
        </div>

        {error && (
          <div
            className="bg-paper border border-terracotta text-terracotta rounded-sm px-3 py-2 text-sm"
            data-testid="recent-exports-error"
          >
            {error}
          </div>
        )}

        {data && (
          <div
            className="overflow-y-auto flex-1 space-y-2 pr-1"
            data-testid="recent-exports-list"
          >
            {(data.entries || []).length === 0 ? (
              <p className="text-sm text-ink-mute italic" data-testid="recent-exports-empty">
                No exports recorded yet. Render a PDF to populate this list.
              </p>
            ) : (
              (data.entries || []).map((e) => (
                <ExportRow key={e.job_id} entry={e} />
              ))
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function ExportRow({ entry }) {
  const ok = entry.status === 'ready';
  return (
    <div
      className="border border-rule/60 rounded-sm px-3 py-2 bg-paper-soft"
      data-testid={`recent-exports-row-${entry.job_id}`}
    >
      <div className="flex items-center gap-2 text-xs">
        {ok ? (
          <CheckCircle2 className="w-4 h-4 text-green-700" />
        ) : (
          <AlertCircle className="w-4 h-4 text-red-700" />
        )}
        <span className="font-semibold text-ink">{entry.status}</span>
        <span className="text-ink-mute">·</span>
        <span className="font-mono text-ink-soft">{entry.filename || '(no file)'}</span>
        <span className="text-ink-mute">·</span>
        <span className="text-ink-soft">{fmtSize(entry.size)}</span>
        <span className="text-ink-mute">·</span>
        <span className="text-ink-soft">{fmtDuration(entry.created_at, entry.finished_at)}</span>
        <span className="ml-auto text-[10px] text-ink-mute">
          {entry.created_at ? new Date(entry.created_at).toLocaleString() : '—'}
        </span>
      </div>
      {entry.error && (
        <div className="mt-1 font-mono text-xs text-red-700 whitespace-pre-wrap break-all">
          error: {entry.error}
        </div>
      )}
      {entry.summary && (
        <pre className="mt-1 font-mono text-xs text-ink whitespace-pre-wrap break-all">
          {entry.summary}
        </pre>
      )}
      {!entry.summary && !entry.error && entry.stage && (
        <div className="mt-1 font-mono text-xs text-ink-soft">stage: {entry.stage}</div>
      )}
    </div>
  );
}
