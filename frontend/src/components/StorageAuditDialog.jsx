/**
 * Admin Storage Audit modal — DB-only, fast.
 *
 * Calls POST /api/admin/storage-audit (admin-gated). The backend
 * issues two MongoDB queries (db.files + db.books) — no object-storage
 * I/O, no Pillow decode — so the call returns in milliseconds even on
 * large libraries. This replaces the earlier audit that timed out in
 * production behind CDN edge proxies (HTTP 524).
 *
 * Response shape:
 *   {
 *     needs_reupload: [{ id, storage_path, filename, book_title, page_no }],
 *     orphaned:       [{ storage_path, filename, book_title, page_no }],
 *     healthy_count: int,
 *     total_referenced_paths: int,
 *     total_file_records: int,
 *     ran_at, ran_by,
 *   }
 *
 *   - needs_reupload: db.files rows whose width_px or height_px is
 *     missing/zero. These were uploaded before iteration 54's mandatory
 *     validation; the preflight DPI check silently skips them.
 *     Fix: book owner re-uploads through the validating endpoint.
 *   - orphaned: storage_path referenced by a book's image block that
 *     has NO matching row in db.files (or row is soft-deleted). At
 *     export time the image falls back to a blank PNG.
 *   - healthy_count: db.files rows with valid dimensions.
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
    lines.push(`Total file records:      ${report.total_file_records}`);
    lines.push(`Total referenced paths:  ${report.total_referenced_paths}`);
    lines.push(`Healthy file records:    ${report.healthy_count}`);
    lines.push('');
    lines.push(`NEEDS RE-UPLOAD (${(report.needs_reupload || []).length}) — missing image dimensions`);
    lines.push('-'.repeat(60));
    for (const f of (report.needs_reupload || [])) {
      lines.push(`  ${f.filename || f.storage_path}`);
      lines.push(`    book : ${f.book_title || '(not referenced by any book)'}`);
      lines.push(`    page : ${f.page_no != null ? f.page_no : '—'}`);
    }
    lines.push('');
    lines.push(`ORPHANED PATHS (${(report.orphaned || []).length}) — referenced by a book but no db.files row`);
    lines.push('-'.repeat(60));
    for (const f of (report.orphaned || [])) {
      lines.push(`  ${f.filename || f.storage_path}`);
      lines.push(`    book : ${f.book_title || '(unknown)'}`);
      lines.push(`    page : ${f.page_no != null ? f.page_no : '—'}`);
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
            Cross-references every image record against every book in the
            database. Pure MongoDB — no object-storage reads — so the audit
            completes in milliseconds and is safe to run as often as needed.
            Read-only; the audit never deletes or modifies anything.
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
            <div
              className="text-xs text-ink-mute italic"
              data-testid="storage-audit-summary"
            >
              {report.total_file_records} file record
              {report.total_file_records === 1 ? '' : 's'} ·{' '}
              {report.total_referenced_paths} path
              {report.total_referenced_paths === 1 ? '' : 's'} referenced in books
              {report.ran_at ? ` · ${new Date(report.ran_at).toLocaleString()}` : ''}
            </div>

            <Section
              title="Needs re-upload"
              count={(report.needs_reupload || []).length}
              icon={<AlertCircle className="w-4 h-4 text-amber-700" />}
              empty="Every file record has valid width/height dimensions."
              testid="storage-audit-section-needs-reupload"
            >
              {(report.needs_reupload || []).map((f) => (
                <li key={f.id || f.storage_path} className="py-1 border-b border-rule/40 last:border-0">
                  <div className="font-mono text-xs text-ink">{f.filename || f.storage_path}</div>
                  <div className="text-xs text-ink-soft italic">
                    book: {f.book_title || '(not referenced)'} · page {f.page_no != null ? f.page_no : '—'}
                  </div>
                </li>
              ))}
            </Section>

            <Section
              title="Orphaned"
              count={(report.orphaned || []).length}
              icon={<FileQuestion className="w-4 h-4 text-red-700" />}
              empty="No orphaned image references — every block points to a real file record."
              testid="storage-audit-section-orphaned"
            >
              {(report.orphaned || []).map((f) => (
                <li key={f.storage_path} className="py-1 border-b border-rule/40 last:border-0">
                  <div className="font-mono text-xs text-ink">{f.filename || f.storage_path}</div>
                  <div className="text-xs text-ink-soft italic">
                    book: {f.book_title || '(unknown)'} · page {f.page_no != null ? f.page_no : '—'}
                  </div>
                </li>
              ))}
            </Section>

            <Section
              title="Healthy"
              count={report.healthy_count || 0}
              icon={<CheckCircle2 className="w-4 h-4 text-green-700" />}
              empty="No healthy files yet — upload an image to see this populate."
              testid="storage-audit-section-healthy"
            >
              <li className="py-1 text-xs text-ink-mute italic">
                {report.healthy_count} file record{report.healthy_count === 1 ? '' : 's'} with valid dimensions
                — not listed individually.
              </li>
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
