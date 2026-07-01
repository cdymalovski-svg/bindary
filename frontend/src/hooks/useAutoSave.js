/**
 * useAutoSave — debounced background persistence for the editor.
 *
 * Watches `book` and, 1.2 s after the last edit, calls `saveBook(false)`
 * so a silent save fires without showing a toast. The very first run
 * after mount is skipped: `book` transitions from null → loaded once,
 * and that transition is not a user edit.
 *
 * The debounce is cleared when the effect re-runs OR when the caller
 * unmounts, so a rapid burst of edits still results in exactly one
 * network call at the end of the burst.
 *
 * Returns a `skipNextRef` — a mutable ref the caller can set to `true`
 * BEFORE a programmatic setBook() call (e.g. TOC auto-refresh, history
 * restore) so the resulting book-change effect doesn't fire autosave.
 * Without this, non-user-driven state updates would trigger phantom
 * saves and flip the dirty flag.
 *
 * @param {object|null} book       Current book state (or null while loading).
 * @param {boolean}      loading   Suspends autosave until the initial load resolves.
 * @param {Function}     saveBook  Persister — invoked as `saveBook(false)` (no toast).
 * @returns {{ skipNextRef: React.MutableRefObject<boolean> }}
 */
import { useEffect, useRef } from 'react';

export default function useAutoSave(book, loading, saveBook) {
  const timerRef = useRef(null);
  // Set true so the very first effect run after load doesn't auto-save
  // a book we just fetched from the server.
  const skipNextRef = useRef(true);

  useEffect(() => {
    if (loading || !book) return;
    if (skipNextRef.current) {
      skipNextRef.current = false;
      return;
    }
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => { saveBook(false); }, 1200);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [book, loading, saveBook]);

  return { skipNextRef };
}
