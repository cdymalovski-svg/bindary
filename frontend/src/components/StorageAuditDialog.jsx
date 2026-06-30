/**
 * Admin Storage Audit modal.
 *
 * Calls POST /api/admin/storage-audit (admin-gated). Walks every
 * db.files row, re-probes the bytes from object storage, and reports
 * three lists: `fixed` (rows that were missing width_px/height_px and
 * have now been backfilled), `bad` (Pillow can't open OR zero dims),
 * `orphaned` (db.files row references a path that no longer exists in
 * storage). Each bad/orphaned entry carries the book title that still
 * references the path, so the admin can find broken images quickly.
 *
 * The endpoint never deletes anything — it only updates dimensions on
 * `fixed` rows. The admin decides whether to clean up bad/orphaned
 * rows through a separate UI (not built here).
 *
 * Download report: serialises the latest run as plain text (one line
 * per entry, grouped by section) so the admin can paste it into a
 * tracking issue or email it.
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
import { Loader2, Download, AlertCircle, CheckCircle2, FileQuestion } from 'lucide-react';

export default function StorageAuditDialog({ open, onOpenChange }) {
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);

  const runAudit = async () => {
    setRunning(true);
    setError(null);
    setReport(null);
    try {
      const BASE = process.env.REACT_APP_BACKEND_URL;
      const token = (() => {
        try { return localStorage.getItem('bindery_token'); } catch { return null; }
      })();
      const r = await fetch(`${BASE}/api/admin/storage-audit`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!r.ok) {
        if (r.status === 403) {
          setError('You must be signed in as an admin to run the storage audit.');
        } else {
          setError(`Audit failed (HTTP ${r.status}). Check backend logs.`);
        }
        return;
      }
      setReport(await r.json());
    } catch (e) {
      setError(`Network error: ${e.message}`);
    } finally {
      setRunning(false);
    }
  };

  const downloadReport = () => {
    if (!report) return;
    const lines = [];
    lines.push(`Bindery storage audit — ${report.ran_at || 'unknown time'}`);
    lines.push(`Ran by: ${report.ran_by || 'unknown'}`);
    lines.push(`Total files scanned: ${report.total_scanned}`);
    lines.push('');
    lines.push(`FIXED (${(report.fixed || []).length}) — dimensions backfilled`);
    lines.push('-'.repeat(60));
    for (const f of (report.fixed || [])) {
      lines.push(`  ${f.original_filename || f.storage_path}  →  ${f.width_px}×${f.height_px}px`);
    }
    lines.push('');
    lines.push(`BAD FILES (${(report.bad || []).length}) — cannot be opened or zero dimensions`);
    lines.push('-'.repeat(60));
    for (const f of (report.bad || [])) {
      lines.push(`  ${f.original_filename || f.storage_path}`);
      lines.push(`    error : ${f.decode_error}`);
      lines.push(`    book  : ${f.book_title || '(not referenced by any book)'}`);
    }
    lines.push('');
    lines.push(`ORPHANED RECORDS (${(report.orphaned || []).length}) — file missing from storage`);
    lines.push('-'.repeat(60));
    for (const f of (report.orphaned || [])) {
      lines.push(`  ${f.original_filename || f.storage_path}`);
      lines.push(`    error : ${f.fetch_error}`);
      lines.push(`    book  : ${f.book_title || '(not referenced by any book)'}`);
    }
    const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `bindery-storage-audit-${new Date().toISOString().slice(0, 10)}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="bg-paper border-rule rounded-sm max-w-3xl max-h-[80vh] overflow-hidden flex flex-col"
        data-testid="storage-audit-dialog"
      >
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl text-ink">Storage audit</DialogTitle>
          <DialogDescription className="text-sm text-ink-soft">
            Scans every uploaded image, backfills missing dimensions, and
            reports bad or orphaned files. Read-only — the audit never
            deletes anything. Safe to run as often as needed.
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-3 py-2">
          <Button
            onClick={runAudit}
            disabled={running}
            data-testid="storage-audit-run"
            className="rounded-sm h-9 px-4 bg-terracotta hover:bg-terracotta/90 text-paper"
          >
            {running ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                Scanning…
              </>
            ) : report ? 'Re-run audit' : 'Run audit'}
          </Button>
          {report && (
            <Button
              variant="outline"
              onClick={downloadReport}
              data-testid="storage-audit-download"
              className="rounded-sm h-9 border-rule"
            >
              <Download className="w-4 h-4 mr-2" />
              Download report
            </Button>
          )}
        </div>

        {error && (
          <div
            className="bg-paper border border-terracotta text-terracotta rounded-sm px-3 py-2 text-sm"
            data-testid="storage-audit-error"
          >
            {error}
          </div>
        )}

        {report && (
          <div
            className="overflow-y-auto flex-1 space-y-4 pr-1 text-sm"
            data-testid="storage-audit-report"
          >
            <div className="text-xs text-ink-mute italic">
              Scanned {report.total_scanned} file{report.total_scanned === 1 ? '' : 's'}
              {report.ran_at ? ` at ${new Date(report.ran_at).toLocaleString()}` : ''}.
            </div>

            <Section
              title="Fixed"
              count={(report.fixed || []).length}
              icon={<CheckCircle2 className="w-4 h-4 text-green-700" />}
              empty="Nothing to backfill — every file already has dimensions."
              testid="storage-audit-section-fixed"
            >
              {(report.fixed || []).map((f) => (
                <li key={f.id} className="py-1 border-b border-rule/40 last:border-0">
                  <div className="font-mono text-xs text-ink">{f.original_filename || f.storage_path}</div>
                  <div className="text-xs text-ink-mute">
                    dimensions recovered: {f.width_px}×{f.height_px}px
                  </div>
                </li>
              ))}
            </Section>

            <Section
              title="Bad files"
              count={(report.bad || []).length}
              icon={<AlertCircle className="w-4 h-4 text-red-700" />}
              empty="No corrupt or zero-dimension files found."
              testid="storage-audit-section-bad"
            >
              {(report.bad || []).map((f) => (
                <li key={f.id} className="py-1 border-b border-rule/40 last:border-0">
                  <div className="font-mono text-xs text-ink">{f.original_filename || f.storage_path}</div>
                  <div className="text-xs text-red-700">{f.decode_error}</div>
                  <div className="text-xs text-ink-soft italic">
                    book: {f.book_title || '(not referenced by any book)'}
                  </div>
                </li>
              ))}
            </Section>

            <Section
              title="Orphaned records"
              count={(report.orphaned || []).length}
              icon={<FileQuestion className="w-4 h-4 text-amber-700" />}
              empty="No orphaned database rows — every record points to a real file."
              testid="storage-audit-section-orphaned"
            >
              {(report.orphaned || []).map((f) => (
                <li key={f.id} className="py-1 border-b border-rule/40 last:border-0">
                  <div className="font-mono text-xs text-ink">{f.original_filename || f.storage_path}</div>
                  <div className="text-xs text-amber-700">{f.fetch_error}</div>
                  <div className="text-xs text-ink-soft italic">
                    book: {f.book_title || '(not referenced by any book)'}
                  </div>
                </li>
              ))}
            </Section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Section({ title, count, icon, empty, testid, children }) {
  return (
    <div data-testid={testid}>
      <h3 className="flex items-center gap-2 font-serif text-lg text-ink mb-1">
        {icon}
        <span>{title}</span>
        <span className="text-ink-mute text-sm font-sans">({count})</span>
      </h3>
      {count === 0 ? (
        <p className="text-xs text-ink-mute italic pl-6">{empty}</p>
      ) : (
        <ul className="pl-6">{children}</ul>
      )}
    </div>
  );
}
