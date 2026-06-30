/**
 * FileHealthWarningDialog — pre-export confirmation.
 *
 * Shown ONLY when GET /api/books/{book_id}/file-health returns a
 * non-empty `problems` list. Tells the user exactly which pages have
 * problematic image references so they can either:
 *   - Re-upload the missing file via the Assets panel (cancel here), or
 *   - Proceed anyway — the export will substitute blank PNGs for
 *     missing images and skip DPI checks for files with no dimensions.
 *
 * Mirrors the categorisation of the backend response:
 *   - issue: "missing"        → no row in db.files (will render blank)
 *   - issue: "no_dimensions"  → row exists but width_px/height_px is
 *                                missing/zero (legacy upload pre-iter 54)
 */
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { AlertCircle, FileQuestion } from 'lucide-react';

export default function FileHealthWarningDialog({ open, problems, onCancel, onProceed }) {
  if (!problems) return null;
  const missing = problems.filter((p) => p.issue === 'missing');
  const noDims = problems.filter((p) => p.issue === 'no_dimensions');

  return (
    <AlertDialog open={open} onOpenChange={(o) => { if (!o) onCancel(); }}>
      <AlertDialogContent
        className="bg-paper border-rule rounded-sm max-w-xl"
        data-testid="file-health-dialog"
      >
        <AlertDialogHeader>
          <AlertDialogTitle className="font-serif text-2xl text-ink">
            Image problems detected
          </AlertDialogTitle>
          <AlertDialogDescription className="text-sm text-ink-soft">
            {problems.length} image reference{problems.length === 1 ? ' has' : 's have'} an issue.
            Exporting now will substitute blank placeholders for missing
            files. We recommend re-uploading them through the Assets panel
            first.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-3 max-h-[40vh] overflow-y-auto pr-1">
          {missing.length > 0 && (
            <div data-testid="file-health-missing">
              <div className="flex items-center gap-2 text-sm font-serif text-red-700">
                <FileQuestion className="w-4 h-4" />
                Missing file ({missing.length})
              </div>
              <ul className="pl-6 text-xs text-ink-soft mt-1">
                {missing.map((p) => (
                  <li key={`${p.storage_path}-${p.page_no}`} className="py-0.5">
                    <span className="font-mono">{p.filename}</span> · page {p.page_no}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {noDims.length > 0 && (
            <div data-testid="file-health-no-dims">
              <div className="flex items-center gap-2 text-sm font-serif text-amber-700">
                <AlertCircle className="w-4 h-4" />
                Missing dimensions ({noDims.length}) — DPI cannot be verified
              </div>
              <ul className="pl-6 text-xs text-ink-soft mt-1">
                {noDims.map((p) => (
                  <li key={`${p.storage_path}-${p.page_no}`} className="py-0.5">
                    <span className="font-mono">{p.filename}</span> · page {p.page_no}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel
            onClick={onCancel}
            data-testid="file-health-cancel"
            className="rounded-sm border-rule"
          >
            Cancel — I&apos;ll re-upload first
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={onProceed}
            data-testid="file-health-proceed"
            className="rounded-sm bg-terracotta hover:bg-terracotta/90 text-paper"
          >
            Export anyway
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
