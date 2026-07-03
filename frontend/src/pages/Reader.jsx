/**
 * Reader — read-only full-screen book viewer.
 *
 * Opened from the library's "Read" (eye) icon on each book card. Purely
 * for reading/displaying the book — no editing, no toolbars, no draggable
 * blocks. Uses the shared `<PagePreview>` component (also used by the
 * dashboard cover thumbnails) so what the reader shows matches how the
 * book will print/PDF.
 *
 * Behaviours:
 *   - Full-screen, dark backdrop. `Esc` or the X button closes back to
 *     the library.
 *   - Display mode toggle at the top: Single (one page) or Spread (two
 *     facing pages). Persisted in localStorage as `bindery.reader.mode`
 *     so each user's preference sticks per browser.
 *   - Page 0 (the cover) is always shown alone on the right in Spread
 *     mode — matches the Editor's spread pairing (cover alone → then
 *     verso/recto pairs 1&2, 3&4, …), so what the author designed in
 *     Spread view reads identically here.
 *   - Keyboard: `←` / `→` navigate; `Esc` closes. Buttons on-screen too.
 *   - Deep-linkable via `/read/:id` (choice 4b) — browser back closes.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ChevronLeft, ChevronRight, X, BookOpen, BookMarked } from 'lucide-react';
import { Button } from '@/components/ui/button';
import PagePreview from '@/components/PagePreview';
import { getBook } from '@/lib/api';
import { PAGE_SIZES } from '@/lib/pageSizes';

const READER_MODE_KEY = 'bindery.reader.mode';

// Read the last-used mode from localStorage (choice 2c). Default to
// spread on wide screens, single on narrow — respects both the user's
// last choice and the initial viewport.
function loadInitialMode() {
  try {
    const saved = localStorage.getItem(READER_MODE_KEY);
    if (saved === 'single' || saved === 'spread') return saved;
  } catch (_e) { /* localStorage may be unavailable */ }
  return typeof window !== 'undefined' && window.innerWidth >= 1024 ? 'spread' : 'single';
}

export default function Reader() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [book, setBook] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [mode, setMode] = useState(loadInitialMode);
  const [currentIndex, setCurrentIndex] = useState(0);
  const stageRef = useRef(null);
  const [stageBox, setStageBox] = useState({ width: 1200, height: 700 });

  // Persist mode changes.
  useEffect(() => {
    try { localStorage.setItem(READER_MODE_KEY, mode); } catch (_e) { /* noop */ }
  }, [mode]);

  // Fetch the book payload once on mount. Full book (all pages).
  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    getBook(id)
      .then((b) => { if (!cancelled) setBook(b); })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(err?.response?.data?.detail || err?.message || 'Failed to load book');
        }
      });
    return () => { cancelled = true; };
  }, [id]);

  // Measure the stage so pages render at max-fit for the current window.
  useEffect(() => {
    const measure = () => {
      if (!stageRef.current) return;
      const rect = stageRef.current.getBoundingClientRect();
      setStageBox({ width: rect.width, height: rect.height });
    };
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [mode, book]);

  const totalPages = book?.pages?.length || 0;
  const pageSize = book ? (PAGE_SIZES[book.page_size] || PAGE_SIZES.a4) : PAGE_SIZES.a4;
  const pageNumberStart = book?.page_number_start || 1;

  // Spread pairing — matches Editor's `renderSpread` exactly.
  // Index 0 (cover) is shown alone on the right; then pairs 1&2, 3&4, …
  const spread = useMemo(() => {
    if (currentIndex === 0) return { left: null, right: 0 };
    if (currentIndex % 2 === 1) {
      return { left: currentIndex, right: currentIndex + 1 < totalPages ? currentIndex + 1 : null };
    }
    return { left: currentIndex - 1, right: currentIndex };
  }, [currentIndex, totalPages]);

  const goPrev = useCallback(() => {
    if (mode === 'single') {
      setCurrentIndex((i) => Math.max(0, i - 1));
    } else {
      // Spread advance = 2 pages at a time (except when leaving the cover).
      setCurrentIndex((i) => {
        if (i <= 0) return 0;
        if (i === 1) return 0;
        // Snap to previous spread's left-page (odd index).
        const prevLeft = (i % 2 === 1 ? i : i - 1) - 2;
        return Math.max(0, prevLeft);
      });
    }
  }, [mode]);

  const goNext = useCallback(() => {
    if (mode === 'single') {
      setCurrentIndex((i) => Math.min(totalPages - 1, i + 1));
    } else {
      setCurrentIndex((i) => {
        if (i === 0) return Math.min(1, totalPages - 1);
        const nextLeft = (i % 2 === 1 ? i : i - 1) + 2;
        return Math.min(totalPages - 1, nextLeft);
      });
    }
  }, [mode, totalPages]);

  const close = useCallback(() => {
    // Prefer browser back so users retain their scroll position on the
    // library. Fall back to /  if there's no history entry (opened via
    // direct link / new tab).
    if (window.history.length > 1) {
      navigate(-1);
    } else {
      navigate('/');
    }
  }, [navigate]);

  // Keyboard: ← / → / Esc.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); close(); return; }
      if (e.key === 'ArrowLeft')  { e.preventDefault(); goPrev(); return; }
      if (e.key === 'ArrowRight') { e.preventDefault(); goNext(); return; }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [goPrev, goNext, close]);

  // Fit calc — max size that fits `pagesShown` side-by-side into the stage.
  const fit = useMemo(() => {
    const pagesShown = mode === 'spread' ? 2 : 1;
    const gap = mode === 'spread' ? 24 : 0;
    const availW = Math.max(200, stageBox.width - 32 - gap);
    const availH = Math.max(200, stageBox.height - 32);
    const perPageW = availW / pagesShown;
    const scale = Math.min(perPageW / pageSize.width, availH / pageSize.height);
    return {
      pageWidth: Math.floor(pageSize.width * scale),
    };
  }, [stageBox, mode, pageSize]);

  // Loading / error states.
  if (loadError) {
    return (
      <div className="min-h-screen bg-ink text-paper flex flex-col items-center justify-center p-8" data-testid="reader-error">
        <p className="font-serif text-2xl mb-2">Could not open this book</p>
        <p className="text-paper/60 mb-6 text-sm">{loadError}</p>
        <Button onClick={close} className="bg-rule-dark hover:bg-rule-dark/70 rounded-sm">
          Back to library
        </Button>
      </div>
    );
  }
  if (!book) {
    return (
      <div className="min-h-screen bg-ink text-paper flex items-center justify-center" data-testid="reader-loading">
        <p className="font-serif italic text-2xl text-paper/50">Opening the book…</p>
      </div>
    );
  }

  const label = mode === 'spread'
    ? (() => {
        const parts = [];
        if (spread.left != null)  parts.push(spread.left + 1);
        if (spread.right != null) parts.push(spread.right + 1);
        return `${parts.join('–')} / ${totalPages}`;
      })()
    : `${currentIndex + 1} / ${totalPages}`;

  return (
    <div
      className="fixed inset-0 bg-ink text-paper flex flex-col z-50"
      data-testid="reader-root"
    >
      {/* Top bar */}
      <header className="flex items-center justify-between px-4 py-3 border-b border-rule-dark bg-ink/95 backdrop-blur">
        <div className="min-w-0 flex-1">
          <p className="font-serif text-lg truncate" data-testid="reader-title">{book.title || 'Untitled'}</p>
          {book.author ? (
            <p className="text-xs text-paper/50 truncate italic">by {book.author}</p>
          ) : null}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {/* Mode toggle — Single | Spread */}
          <div className="flex items-center bg-rule-dark rounded-sm overflow-hidden" role="tablist" aria-label="Reader mode">
            <button
              type="button"
              onClick={() => setMode('single')}
              className={`px-3 py-1.5 text-xs flex items-center gap-1.5 transition-colors ${
                mode === 'single' ? 'bg-paper text-ink' : 'text-paper/70 hover:text-paper'
              }`}
              data-testid="reader-mode-single"
              aria-pressed={mode === 'single'}
            >
              <BookOpen className="w-3.5 h-3.5" />
              Single
            </button>
            <button
              type="button"
              onClick={() => setMode('spread')}
              className={`px-3 py-1.5 text-xs flex items-center gap-1.5 transition-colors ${
                mode === 'spread' ? 'bg-paper text-ink' : 'text-paper/70 hover:text-paper'
              }`}
              data-testid="reader-mode-spread"
              aria-pressed={mode === 'spread'}
            >
              <BookMarked className="w-3.5 h-3.5" />
              Spread
            </button>
          </div>
          <span className="text-xs text-paper/60 tabular-nums px-2" data-testid="reader-page-indicator">
            {label}
          </span>
          <button
            type="button"
            onClick={close}
            className="p-1.5 rounded-sm text-paper/70 hover:text-paper hover:bg-rule-dark"
            title="Close (Esc)"
            aria-label="Close reader"
            data-testid="reader-close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </header>

      {/* Stage */}
      <div className="flex-1 flex items-stretch min-h-0 relative">
        {/* Prev arrow — outside the stage so it never overlaps the page. */}
        <button
          type="button"
          onClick={goPrev}
          disabled={currentIndex === 0}
          className="w-14 flex items-center justify-center text-paper/40 hover:text-paper hover:bg-ink-soft/40 disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
          title="Previous page (←)"
          aria-label="Previous page"
          data-testid="reader-prev"
        >
          <ChevronLeft className="w-8 h-8" />
        </button>

        <div
          ref={stageRef}
          className="flex-1 flex items-center justify-center overflow-hidden"
          data-testid="reader-stage"
        >
          {mode === 'single' ? (
            <PagePreview
              key={`single-${currentIndex}`}
              page={book.pages[currentIndex]}
              pageSizeKey={book.page_size}
              width={fit.pageWidth}
              showPageNumber={
                !!book.pages[currentIndex]?.show_page_number
                && !(totalPages > 1 && currentIndex === totalPages - 1)  // hide on back cover
                && (currentIndex + 1) >= pageNumberStart
              }
              pageNumberValue={(currentIndex + 1) - pageNumberStart + 1}
              className="shadow-2xl"
            />
          ) : (
            <div className="flex items-center gap-6" data-testid="reader-spread">
              {spread.left != null ? (
                <PagePreview
                  key={`sp-l-${spread.left}`}
                  page={book.pages[spread.left]}
                  pageSizeKey={book.page_size}
                  width={fit.pageWidth}
                  showPageNumber={
                    !!book.pages[spread.left]?.show_page_number
                    && !(totalPages > 1 && spread.left === totalPages - 1)
                    && (spread.left + 1) >= pageNumberStart
                  }
                  pageNumberValue={(spread.left + 1) - pageNumberStart + 1}
                  className="shadow-2xl"
                />
              ) : (
                // Cover-alone placeholder — invisible left slot matching page dims
                // so the recto (cover) sits center-right like a real closed book.
                <div style={{ width: fit.pageWidth, height: fit.pageWidth * (pageSize.height / pageSize.width) }} data-testid="reader-spread-empty" />
              )}
              {spread.right != null ? (
                <PagePreview
                  key={`sp-r-${spread.right}`}
                  page={book.pages[spread.right]}
                  pageSizeKey={book.page_size}
                  width={fit.pageWidth}
                  showPageNumber={
                    !!book.pages[spread.right]?.show_page_number
                    && !(totalPages > 1 && spread.right === totalPages - 1)
                    && (spread.right + 1) >= pageNumberStart
                  }
                  pageNumberValue={(spread.right + 1) - pageNumberStart + 1}
                  className="shadow-2xl"
                />
              ) : (
                <div style={{ width: fit.pageWidth, height: fit.pageWidth * (pageSize.height / pageSize.width) }} data-testid="reader-spread-empty" />
              )}
            </div>
          )}
        </div>

        <button
          type="button"
          onClick={goNext}
          disabled={
            mode === 'single'
              ? currentIndex >= totalPages - 1
              : (spread.right != null ? spread.right : spread.left) >= totalPages - 1
          }
          className="w-14 flex items-center justify-center text-paper/40 hover:text-paper hover:bg-ink-soft/40 disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
          title="Next page (→)"
          aria-label="Next page"
          data-testid="reader-next"
        >
          <ChevronRight className="w-8 h-8" />
        </button>
      </div>
    </div>
  );
}
