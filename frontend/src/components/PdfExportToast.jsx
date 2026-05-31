import { Loader2, X } from 'lucide-react';

/**
 * Custom Sonner toast for long-running exports. Shows a thin terracotta
 * progress bar filling left-to-right when a fraction is known, an
 * indeterminate sweep otherwise. The cancel button calls `onCancel`
 * which is wired by the export flow to abort both the local polling
 * loop AND the backend worker.
 */
export default function PdfExportToast({
  title = 'Building PDF',
  stage = '',
  done = 0,
  total = 0,
  elapsedSec = 0,
  onCancel,
}) {
  const hasFraction = total > 0 && done > 0;
  const pct = hasFraction ? Math.min(100, Math.round((done / total) * 100)) : 0;

  return (
    <div
      data-testid="pdf-export-toast"
      className="w-[340px] sm:w-[380px] bg-paper text-ink border border-rule rounded-sm shadow-lg overflow-hidden"
    >
      <div className="flex items-center justify-between px-4 pt-3 pb-1">
        <div className="flex items-center gap-2 min-w-0">
          <Loader2 className="w-3.5 h-3.5 animate-spin text-terracotta shrink-0" />
          <p className="text-sm font-medium truncate">{title}</p>
        </div>
        {onCancel ? (
          <button
            type="button"
            onClick={onCancel}
            aria-label="Cancel export"
            title="Cancel export"
            data-testid="pdf-export-cancel"
            className="p-1 rounded-sm text-ink-mute hover:text-ink hover:bg-rule/40 transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        ) : null}
      </div>

      <div className="px-4 pb-1 flex items-baseline gap-2 justify-between">
        <p className="text-[11px] text-ink-mute truncate">{stage || 'Starting…'}</p>
        <p className="text-[11px] text-ink-mute tabular-nums shrink-0">
          {hasFraction ? `${done}/${total}` : `${elapsedSec}s`}
        </p>
      </div>

      {/* Track */}
      <div className="mx-4 mb-3 mt-1 h-[3px] rounded-full bg-rule/40 overflow-hidden">
        {hasFraction ? (
          <div
            className="h-full bg-terracotta transition-[width] duration-300 ease-out"
            style={{ width: `${pct}%` }}
            data-testid="pdf-export-progress-bar"
          />
        ) : (
          // Indeterminate sweep — a 30% segment slides across the track.
          // Uses a one-off keyframe in index.css.
          <div
            className="h-full w-1/3 bg-terracotta pdf-export-indeterminate"
            data-testid="pdf-export-progress-indeterminate"
          />
        )}
      </div>
    </div>
  );
}
