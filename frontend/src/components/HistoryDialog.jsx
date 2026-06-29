import { useEffect, useState } from 'react';
import { History, RotateCcw, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { listRevisions, restoreRevision } from '@/lib/api';

// Short, human-friendly date label (e.g. "May 25, 02:14").
function formatTime(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

export default function HistoryDialog({ bookId, onRestored, open: controlledOpen, onOpenChange: controlledOnOpenChange, hideTrigger = false }) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  // Controlled mode: parent owns `open` state and provides the trigger.
  // Uncontrolled: this component renders its own trigger button (the
  // long-standing toolbar default).  Used by the editor's "More" menu
  // which wants to fire the dialog from a DropdownMenuItem.
  const isControlled = controlledOpen !== undefined;
  const open = isControlled ? controlledOpen : uncontrolledOpen;
  const setOpen = isControlled ? (controlledOnOpenChange || (() => {})) : setUncontrolledOpen;
  const [loading, setLoading] = useState(false);
  const [restoringId, setRestoringId] = useState(null);
  const [revisions, setRevisions] = useState([]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const list = await listRevisions(bookId);
        if (!cancelled) setRevisions(list);
      } catch (e) {
        if (!cancelled) toast.error('Could not load history');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [open, bookId]);

  const handleRestore = async (revId) => {
    setRestoringId(revId);
    try {
      const restored = await restoreRevision(bookId, revId);
      toast.success('Restored. Editor refreshed.');
      onRestored?.(restored);
      setOpen(false);
    } catch (e) {
      toast.error('Could not restore that version');
    } finally {
      setRestoringId(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {!hideTrigger && (
        <DialogTrigger asChild>
          <Button
            variant="outline"
            className="bg-white border-rule hover:bg-desk text-ink rounded-sm h-8"
            data-testid="open-history-button"
            title="See and restore previous saves of this book"
          >
            <History className="w-4 h-4 mr-1" /> History
          </Button>
        </DialogTrigger>
      )}
      <DialogContent className="bg-paper border-rule rounded-sm max-w-md">
        <DialogHeader>
          <DialogTitle className="font-serif text-ink">Edit history</DialogTitle>
          <DialogDescription className="text-ink-soft">
            Every time you save, a checkpoint is kept (up to 20). Restore any version to roll back.
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] overflow-y-auto -mx-2 px-2 space-y-1">
          {loading ? (
            <div className="flex items-center justify-center py-10 text-ink-mute">
              <Loader2 className="w-4 h-4 animate-spin mr-2" /> Loading…
            </div>
          ) : revisions.length === 0 ? (
            <div className="text-center py-10 text-ink-mute text-sm">
              No checkpoints yet. Save a change to create the first one.
            </div>
          ) : (
            revisions.map((r, i) => (
              <div
                key={r.id}
                data-testid={`revision-row-${r.id}`}
                className="group flex items-center gap-3 px-3 py-2 rounded-sm hover:bg-desk transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-ink flex items-center gap-2">
                    <span>{formatTime(r.created_at)}</span>
                    {i === 0 && (
                      <span className="text-[10px] tracking-[0.18em] uppercase text-ink-mute">
                        Latest
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-ink-mute truncate" title={r.summary}>
                    {r.summary} · {r.page_count} page{r.page_count === 1 ? '' : 's'}
                  </div>
                </div>
                {i > 0 && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="opacity-0 group-hover:opacity-100 text-ink-soft hover:text-terracotta h-7 px-2"
                    onClick={() => handleRestore(r.id)}
                    disabled={restoringId === r.id}
                    data-testid={`restore-revision-${r.id}`}
                  >
                    {restoringId === r.id ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <>
                        <RotateCcw className="w-3.5 h-3.5 mr-1" /> Restore
                      </>
                    )}
                  </Button>
                )}
              </div>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
