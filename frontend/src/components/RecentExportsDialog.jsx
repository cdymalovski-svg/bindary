/**
 * Admin Recent Exports modal.
 *
 * Calls GET /api/admin/recent-exports (admin-gated). Returns up to the
 * last 20 JOB SUMMARY log lines emitted by the PDF render pipeline on
 * this pod. Each line carries phase-split timings + fallback counts +
 * missing-font list — the same forensic data we ship to supervisor.
 *
 * Multi-pod caveat: each backend process has its own ring buffer, so
 * a hit may land on a pod with a sparser history than another. The
 * backend tags the response with `scope: "this-pod-only"` and we
 * surface that in the UI so the admin isn't surprised when refreshing
 * shows different rows.
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
import { Loader2, RefreshCw } from 'lucide-react';

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
            Last 20 PDF render summaries from this backend process. Each
            line shows total time, prefetch vs render breakdown, fallback
            counts, and any missing fonts. Per-pod scope.
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
            <span className="text-xs text-ink-mute italic">
              scope: {data.scope || 'this-pod-only'}, showing {(data.entries || []).length} of max {data.max || 20}
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
            className="overflow-y-auto flex-1 space-y-1 pr-1"
            data-testid="recent-exports-list"
          >
            {(data.entries || []).length === 0 ? (
              <p className="text-sm text-ink-mute italic">
                No exports captured on this pod yet. Render a PDF to populate this list.
              </p>
            ) : (
              (data.entries || []).map((e, i) => (
                <div key={i} className="border-b border-rule/40 last:border-0 py-2">
                  <div className="text-[10px] text-ink-mute uppercase tracking-wider">
                    {new Date(e.ts).toLocaleString()} · {e.level}
                  </div>
                  <pre className="font-mono text-xs text-ink whitespace-pre-wrap break-all">
                    {e.message}
                  </pre>
                </div>
              ))
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
