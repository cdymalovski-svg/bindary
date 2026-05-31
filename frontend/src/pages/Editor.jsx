import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';
import {
  ArrowLeft,
  Plus,
  Image as ImageIcon,
  Type as TypeIcon,
  Save,
  Download,
  Trash2,
  Copy,
  Eye,
  EyeOff,
  Loader2,
  SlidersHorizontal,
  BookOpen,
  Square,
  Palette,
  Heading,
  List,
  Wand2,
  ChevronUp,
  ChevronDown,
  Layers,
  PanelLeft,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu';
import { getBook, updateBook, uploadImage } from '@/lib/api';
import { PAGE_SIZES, getPageSize, PAGE_MARGIN_PX } from '@/lib/pageSizes';
import { sanitizeHtml } from '@/lib/sanitize';
import CanvasBlock from '@/components/CanvasBlock';
import BlockProperties from '@/components/BlockProperties';
import AssetsPanel, { ASSET_DRAG_MIME } from '@/components/AssetsPanel';
import PagePanel from '@/components/PagePanel';
import LayersList from '@/components/LayersList';
import SaveTemplateDialog from '@/components/SaveTemplateDialog';
import HistoryDialog from '@/components/HistoryDialog';
import ExportPopover from '@/components/ExportPopover';

const uid = () => Math.random().toString(36).slice(2) + Date.now().toString(36);

// Pre-load an image to read its intrinsic dimensions so the block can be
// sized to match the illustration's aspect ratio.
function loadImageSize(url) {
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => resolve({ w: img.naturalWidth || 0, h: img.naturalHeight || 0 });
    img.onerror = () => resolve({ w: 0, h: 0 });
    img.src = url;
  });
}

// Given a desired max area, return width/height that preserve aspect ratio.
function fitWithin(naturalW, naturalH, maxW, maxH) {
  const fallbackRatio = 4 / 3;
  const ratio = naturalW > 0 && naturalH > 0 ? naturalW / naturalH : fallbackRatio;
  let width = Math.min(maxW, naturalW || maxW);
  let height = width / ratio;
  if (height > maxH) {
    height = maxH;
    width = height * ratio;
  }
  return { width, height };
}

export default function Editor() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [book, setBook] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [activePageIndex, setActivePageIndex] = useState(0);
  const [selectedBlockId, setSelectedBlockId] = useState(null);
  const [editingTextId, setEditingTextId] = useState(null);
  const [rightTab, setRightTab] = useState('assets'); // 'assets' | 'page' | 'block'
  const [viewMode, setViewMode] = useState('single'); // 'single' | 'spread'
  // Sidebar drawer state — below the `lg` breakpoint both sidebars become
  // overlay drawers triggered from toolbar icons (Pages on the left, Panels
  // on the right). On lg+ they're always-visible static columns and these
  // values are ignored thanks to Tailwind `lg:translate-x-0`.
  // Initialised true if we already know the viewport is lg+, so a desktop
  // user never sees a flash of "closed" state.
  const [pagesDrawerOpen, setPagesDrawerOpen] = useState(
    () => (typeof window !== 'undefined' ? window.innerWidth >= 1024 : false),
  );
  const [panelsDrawerOpen, setPanelsDrawerOpen] = useState(
    () => (typeof window !== 'undefined' ? window.innerWidth >= 1024 : false),
  );
  // On window resize, snap drawers to "open" once we cross into lg+. Below
  // lg we don't auto-close — a user who explicitly opened the drawer on a
  // tablet keeps their view.
  useEffect(() => {
    const onResize = () => {
      if (window.innerWidth >= 1024) {
        setPagesDrawerOpen(true);
        setPanelsDrawerOpen(true);
      }
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const [lastSavedAt, setLastSavedAt] = useState(null);
  // Bumped after a successful export so the popover's history list
  // refetches and shows the just-completed run.
  const [exportsBump, setExportsBump] = useState(0);
  // Bumped after any asset replacement (re-uploading bytes for a missing
  // asset). Appended to every canvas <img> URL so the browser refetches
  // instead of showing the previously-cached 404.
  const [assetCacheBuster, setAssetCacheBuster] = useState(0);
  const fileInputRef = useRef(null);
  const autoSaveTimerRef = useRef(null);
  const skipNextAutoSaveRef = useRef(true);
  // Tracks the start of a one-finger horizontal swipe on the desk so we can
  // navigate to the previous/next page (or spread) on touch release.
  const deskTouchRef = useRef(null);

  // Load book
  useEffect(() => {
    (async () => {
      try {
        const b = await getBook(id);
        setBook(b);
      } catch (e) {
        toast.error('Could not load book');
        navigate('/');
      } finally {
        setLoading(false);
      }
    })();
  }, [id, navigate]);

  const activePage = book?.pages?.[activePageIndex];
  const selectedBlock = activePage?.blocks?.find((b) => b.id === selectedBlockId);
  const pageSize = book ? getPageSize(book.page_size) : PAGE_SIZES.a4;

  // Auto-switch right tab when selection changes.
  useEffect(() => {
    if (selectedBlock) setRightTab('block');
    else setRightTab((cur) => (cur === 'block' ? 'page' : cur));
  }, [selectedBlockId]); // eslint-disable-line react-hooks/exhaustive-deps

  // --- Save ---
  const saveBook = useCallback(async (showToast = true) => {
    if (!book) return;
    setSaving(true);
    const payload = {
      title: book.title,
      author: book.author,
      page_size: book.page_size,
      page_number_start: book.page_number_start || 1,
      is_chapter_book: !!book.is_chapter_book,
      text_presets: book.text_presets || null,
      pages: book.pages,
    };
    // Quick retry on transient 5xx / network blips so a container restart
    // mid-autosave doesn't surface as a scary "Save failed" — we just wait
    // a second and try once more.
    let lastErr = null;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await updateBook(book.id, payload);
        setLastSavedAt(new Date());
        if (showToast) toast.success('Saved');
        setSaving(false);
        return;
      } catch (e) {
        lastErr = e;
        const status = e?.response?.status;
        const retryable = !status || (status >= 500 && status < 600) || status === 0;
        if (!retryable || attempt > 0) break;
        await new Promise((r) => setTimeout(r, 1500));
      }
    }
    // Both attempts failed — surface the cause if it isn't already obvious.
    const status = lastErr?.response?.status;
    if (showToast) {
      const msg = status === 502 || status === 503
        ? 'Save failed — the server is restarting. Your edits are still in this tab; try again in a moment.'
        : 'Save failed';
      toast.error(msg, { duration: 6000 });
    }
    setSaving(false);
  }, [book]);

  // Auto-save: debounced 1.2s after the last edit.
  // The very first effect run (right after load) is skipped via skipNextAutoSaveRef.
  useEffect(() => {
    if (loading || !book) return;
    if (skipNextAutoSaveRef.current) {
      skipNextAutoSaveRef.current = false;
      return;
    }
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => {
      saveBook(false);
    }, 1200);
    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
  }, [book, loading, saveBook]);

  // --- Mutators ---
  const updatePages = (updater) => {
    setBook((prev) => ({ ...prev, pages: updater(prev.pages) }));
  };

  const updateBlock = (blockId, patch, pageIdx = activePageIndex) => {
    updatePages((pages) =>
      pages.map((p, i) =>
        i === pageIdx
          ? { ...p, blocks: p.blocks.map((b) => (b.id === blockId ? { ...b, ...patch } : b)) }
          : p
      )
    );
  };

  const updatePage = (patch, pageIdx = activePageIndex) => {
    updatePages((pages) =>
      pages.map((p, i) => (i === pageIdx ? { ...p, ...patch } : p))
    );
  };

  // Copy this page's appearance to every page EXCEPT the front cover (index 0)
  // and the back cover (last index). Only style/layout fields are copied — blocks are not touched.
  const applyPageToInterior = () => {
    if (!book || !activePage) return;
    const total = book.pages.length;
    if (total < 3) {
      toast.error('Add a middle page first');
      return;
    }
    const src = activePage;
    const patch = {
      background_color: src.background_color,
      full_bleed: !!src.full_bleed,
      show_page_number: !!src.show_page_number,
      page_number_align: src.page_number_align || 'right',
      page_number_size: src.page_number_size || 14,
    };
    updatePages((pages) =>
      pages.map((p, i) => (i === 0 || i === pages.length - 1 ? p : { ...p, ...patch }))
    );
    toast.success(`Applied to ${total - 2} page${total - 2 === 1 ? '' : 's'}`);
  };

  // Page-number font and size are book-wide: changing them on any page propagates
  // to every page so numbers stay consistent across the volume.
  const setAllPagesNumberStyle = (patch) => {
    updatePages((pages) => pages.map((p) => ({ ...p, ...patch })));
  };

  const deleteBlock = (blockId, pageIdx = activePageIndex) => {
    updatePages((pages) =>
      pages.map((p, i) =>
        i === pageIdx ? { ...p, blocks: p.blocks.filter((b) => b.id !== blockId) } : p
      )
    );
    setSelectedBlockId(null);
  };

  // Three text-block presets — defaults the user can override after insert.
  // Width auto-fits the page (minus a comfortable inset); height roughly fits two lines.
  const TEXT_PRESETS = {
    title: {
      label: 'Title',
      font_family: 'Playfair Display',
      font_size: 48,
      text_align: 'center',
      lineFactor: 1.4,
    },
    subtitle: {
      label: 'Subtitle',
      font_family: 'Cormorant Garamond',
      font_size: 28,
      text_align: 'center',
      lineFactor: 1.6,
    },
    body: {
      label: 'Page text',
      font_family: 'Cormorant Garamond',
      font_size: 18,
      text_align: 'left',
      lineFactor: 4.5, // ~4 lines of body copy
    },
  };

  const addTextBlock = (preset = 'body') => {
    const base = TEXT_PRESETS[preset] || TEXT_PRESETS.body;
    // Per-book override merges on top of the built-in defaults.
    const userPreset = book?.text_presets?.[preset] || {};
    const cfg = { ...base, ...userPreset };
    const margin = activePage?.full_bleed ? 0 : PAGE_MARGIN_PX;
    const maxW = pageSize.width - margin * 2 - 40;
    const width = preset === 'body' ? Math.min(420, maxW) : Math.min(560, maxW);
    const height = Math.max(60, Math.round(cfg.font_size * base.lineFactor));
    // On the cover page, prefill the Title preset with the book's title so
    // authors can drop a formatted title into place without retyping.
    const isCover = activePageIndex === 0;
    let prefillHtml = '';
    if (preset === 'title' && isCover && book.title) {
      prefillHtml = `<p>${book.title.replace(/</g, '&lt;')}</p>`;
    }
    const block = {
      id: uid(),
      type: 'text',
      x: Math.max(margin + 20, (pageSize.width - width) / 2),
      y: margin + 40,
      width,
      height,
      z_index: 1,
      html: prefillHtml,
      font_family: cfg.font_family,
      font_size: cfg.font_size,
      text_align: cfg.text_align,
      color: cfg.color || '#000000',
    };
    updatePages((pages) =>
      pages.map((p, i) => (i === activePageIndex ? { ...p, blocks: [...p.blocks, block] } : p))
    );
    setSelectedBlockId(block.id);
    // Skip auto-edit when prefilled so the user sees the formatted title first.
    if (!prefillHtml) setEditingTextId(block.id);
  };

  // Persist the selected text block's current style as the default for one of
  // the three presets (title/subtitle/body). Lives on the book document so
  // each book can keep its own typographic voice. Saved immediately (skipping
  // the 1.2s debounce) so reloading the editor picks up the new default.
  const saveBlockAsPreset = (presetKey) => {
    if (!selectedBlock || selectedBlock.type !== 'text') return;
    const presetPatch = {
      font_family: selectedBlock.font_family,
      font_size: selectedBlock.font_size,
      text_align: selectedBlock.text_align,
      color: selectedBlock.color,
    };
    const newPresets = { ...(book.text_presets || {}), [presetKey]: presetPatch };
    setBook((b) => ({ ...b, text_presets: newPresets }));
    const label = TEXT_PRESETS[presetKey]?.label || presetKey;
    // Persist right away — don't wait for the autosave debounce.
    updateBook(book.id, {
      title: book.title,
      author: book.author,
      page_size: book.page_size,
      page_number_start: book.page_number_start || 1,
      is_chapter_book: !!book.is_chapter_book,
      text_presets: newPresets,
      pages: book.pages,
    })
      .then(() => {
        setLastSavedAt(new Date());
        toast.success(`Saved as default "${label}" for this book`);
      })
      .catch(() => toast.error('Could not save preset'));
  };

  // Add a chapter-heading text block. Auto-numbers based on existing chapter blocks
  // across the whole book.
  const addChapterBlock = () => {
    let n = 0;
    (book?.pages || []).forEach((p) => {
      (p.blocks || []).forEach((b) => { if (b.is_chapter) n += 1; });
    });
    const number = n + 1;
    const pageW = pageSize.width;
    const margin = activePage?.full_bleed ? 0 : PAGE_MARGIN_PX;
    const width = Math.min(pageW - margin * 2 - 40, 600);
    const block = {
      id: uid(),
      type: 'text',
      x: (pageW - width) / 2,
      y: margin + 40,
      width,
      height: 110,
      z_index: 1,
      html: `<p>Chapter ${number}</p>`,
      font_family: 'Cormorant Garamond',
      font_size: 56,
      text_align: 'center',
      color: '#000000',
      is_chapter: true,
    };
    updatePages((pages) =>
      pages.map((p, i) => (i === activePageIndex ? { ...p, blocks: [...p.blocks, block] } : p))
    );
    setSelectedBlockId(block.id);
    toast.success(`Chapter ${number} added`);
  };

  // One-click cover designer: lays out the active page as a polished cover.
  // - Forces full bleed, hides page number.
  // - Keeps any existing image block and resizes it to fill the page (background art).
  // - Sends images to the back, places a centered title + author over the top.
  // Existing text blocks on the cover page are replaced.
  const designCover = () => {
    if (!activePage) return;
    const pageW = pageSize.width;
    const pageH = pageSize.height;
    const existingImages = (activePage.blocks || []).filter((b) => b.type === 'image' && b.image_url);
    const hasArt = existingImages.length > 0;

    // Resize the first image to fill the whole page; subsequent images keep their layout.
    const reshapedImages = existingImages.map((b, i) => {
      if (i !== 0) return { ...b, z_index: 0 };
      return { ...b, x: 0, y: 0, width: pageW, height: pageH, z_index: 0 };
    });

    // Pick text colors that read well over the background.
    const bg = activePage.background_color || '#FFF8DC';
    const onDark = hasArt || isDarkHex(bg);
    const titleColor = onDark ? '#F9F6F0' : '#1C1B19';
    const authorColor = onDark ? '#E8E2D4' : '#4A4843';

    // Title sits in the upper third; author below it.
    const titleText = book.title || 'Untitled';
    const titleWidth = Math.min(pageW - 80, 640);
    // Scale title font down for longer titles so it stays on 1-2 lines.
    const baseTitleSize = Math.round(pageW * 0.11);
    const lengthScale = titleText.length > 28 ? 28 / titleText.length : 1;
    const titleFontSize = Math.max(28, Math.round(baseTitleSize * lengthScale));
    // Give the title room for up to 3 wrapped lines.
    const titleLineHeight = titleFontSize * 1.15;
    const titleHeight = Math.round(titleLineHeight * 3 + 12);
    const titleY = Math.round(pageH * 0.18);
    const titleBlock = {
      id: uid(),
      type: 'text',
      x: (pageW - titleWidth) / 2,
      y: titleY,
      width: titleWidth,
      height: titleHeight,
      z_index: 10,
      html: `<p>${titleText.replace(/</g, '&lt;')}</p>`,
      font_family: 'Playfair Display',
      font_size: titleFontSize,
      text_align: 'center',
      color: titleColor,
    };

    const authorText = book.author ? `by ${book.author}` : '';
    const authorWidth = Math.min(pageW - 120, 480);
    const authorFontSize = Math.max(16, Math.round(titleFontSize * 0.32));
    const authorBlock = authorText
      ? {
          id: uid(),
          type: 'text',
          x: (pageW - authorWidth) / 2,
          y: titleY + titleHeight + 20,
          width: authorWidth,
          height: authorFontSize * 2,
          z_index: 10,
          html: `<p><em>${authorText.replace(/</g, '&lt;')}</em></p>`,
          font_family: 'Cormorant Garamond',
          font_size: authorFontSize,
          text_align: 'center',
          color: authorColor,
        }
      : null;

    const newBlocks = [...reshapedImages, titleBlock, ...(authorBlock ? [authorBlock] : [])];

    updatePages((pages) =>
      pages.map((p, i) =>
        i === activePageIndex
          ? {
              ...p,
              full_bleed: true,
              show_page_number: false,
              blocks: newBlocks,
            }
          : p
      )
    );
    setSelectedBlockId(titleBlock.id);
    toast.success(hasArt ? 'Cover designed with your artwork' : 'Cover designed — drop in art for a backdrop');
  };

  // Build the TOC body HTML (header + one row per chapter) from the current
  // book. Pure — does not touch state. Used by both the one-shot inserter
  // and the live-sync effect below so the two paths can never drift.
  const computeTocPayload = useCallback(() => {
    if (!book?.pages?.length) return null;
    const start = book?.page_number_start || 1;
    const last = (book?.pages?.length || 1) - 1;
    const entries = [];
    book.pages.forEach((p, pi) => {
      (p.blocks || []).forEach((b) => {
        if (!b.is_chapter) return;
        const tmp = document.createElement('div');
        tmp.innerHTML = sanitizeHtml(b.html || '');
        const title = (tmp.textContent || '').trim() || `Chapter ${entries.length + 1}`;
        const oneBased = pi + 1;
        const displayed = pi === last ? '' : oneBased >= start ? String(oneBased - start + 1) : '';
        entries.push({ title, displayed, pageIndex: pi });
      });
    });
    if (entries.length === 0) return null;
    const rows = entries
      .map((e) =>
        `<p data-toc-target="${e.pageIndex}" style="display:flex;justify-content:space-between;gap:1em;margin:0 0 .35em 0;cursor:pointer;">` +
        `<span>${e.title}</span><span>${e.displayed}</span>` +
        `</p>`
      ).join('');
    const headerHtml = `<p style="text-align:center;font-size:1.4em;margin:0 0 .6em 0;">Contents</p>`;
    return { html: headerHtml + rows, entryCount: entries.length };
  }, [book]);

  // Build a Table of Contents text block from every chapter heading in the
  // book. Inserted blocks are tagged `is_toc: true` so the live-sync effect
  // below keeps them current as chapters are added, edited, or reordered.
  const addTocBlock = () => {
    const payload = computeTocPayload();
    if (!payload) {
      toast.error('Add chapters first');
      return;
    }
    const pageW = pageSize.width;
    const margin = activePage?.full_bleed ? 0 : PAGE_MARGIN_PX;
    const width = Math.min(pageW - margin * 2 - 40, 540);
    const height = Math.max(180, 60 + payload.entryCount * 28);
    const block = {
      id: uid(),
      type: 'text',
      x: (pageW - width) / 2,
      y: margin + 80,
      width,
      height,
      z_index: 1,
      html: payload.html,
      font_family: 'Cormorant Garamond',
      font_size: 20,
      text_align: 'left',
      color: '#000000',
      is_toc: true,
    };
    updatePages((pages) =>
      pages.map((p, i) => {
        if (i !== activePageIndex) return p;
        // Drop any empty starter text blocks so the TOC isn't visually buried.
        const filtered = p.blocks.filter(
          (b) => !(b.type === 'text' && (!b.html || !b.html.trim()) && !b.is_chapter)
        );
        return { ...p, blocks: [...filtered, block] };
      })
    );
    setSelectedBlockId(block.id);
    toast.success(`Contents inserted (${payload.entryCount} chapter${payload.entryCount === 1 ? '' : 's'}) — auto-updates as you go`);
  };

  // Live-sync any block tagged `is_toc: true` with the current chapter list.
  // Triggers whenever the book changes; early-exits when the computed html
  // already matches what's on the block, so this is a no-op once the TOC
  // is in steady state. Skips runs while the user is actively editing the
  // TOC's own text so we don't yank the caret mid-edit.
  useEffect(() => {
    if (!book?.pages) return;
    const payload = computeTocPayload();
    if (!payload) return;
    let changed = false;
    const newPages = book.pages.map((p) => {
      let pageChanged = false;
      const newBlocks = (p.blocks || []).map((b) => {
        if (!b.is_toc) return b;
        if (editingTextId === b.id) return b; // user is typing inside it
        if (b.html === payload.html) return b;
        pageChanged = true;
        return { ...b, html: payload.html };
      });
      if (!pageChanged) return p;
      changed = true;
      return { ...p, blocks: newBlocks };
    });
    if (!changed) return;
    // Bypass autosave for THIS state update — the user didn't make this
    // change, the TOC just refreshed itself. Without this every chapter
    // edit would flip the dirty flag and re-save.
    skipNextAutoSaveRef.current = true;
    setBook((prev) => ({ ...prev, pages: newPages }));
  }, [book, computeTocPayload, editingTextId]);

  const handleImageUpload = async (file) => {
    if (!file) return;
    try {
      toast.loading('Uploading…', { id: 'upload' });
      const res = await uploadImage(file, book?.id);
      toast.dismiss('upload');
      addImageBlockFromAsset({ url: res.url, path: res.path });
      toast.success('Image added');
    } catch (e) {
      toast.dismiss('upload');
      toast.error('Upload failed');
    }
  };

  // Add an image block from an asset (dragged in or just uploaded).
  // The block is sized to match the illustration's natural aspect ratio so the
  // entire asset is visible without cropping.
  const addImageBlockFromAsset = async (asset, position = null, pageIdx = activePageIndex) => {
    const previewUrl = (asset.url || '').startsWith('http')
      ? asset.url
      : `${process.env.REACT_APP_BACKEND_URL}${asset.url}`;
    const { w: natW, h: natH } = await loadImageSize(previewUrl);
    // Cap at the page's safe area (page minus 1cm margin).
    const maxW = Math.min(640, pageSize.width - PAGE_MARGIN_PX * 2);
    const maxH = pageSize.height - PAGE_MARGIN_PX * 2;
    const { width, height } = fitWithin(natW, natH, maxW, maxH);
    let x;
    let y;
    if (position) {
      x = Math.max(0, Math.min(pageSize.width - width, position.x - width / 2));
      y = Math.max(0, Math.min(pageSize.height - height, position.y - height / 2));
    } else {
      x = Math.max(PAGE_MARGIN_PX, (pageSize.width - width) / 2);
      y = Math.max(PAGE_MARGIN_PX, (pageSize.height - height) / 2);
    }
    const block = {
      id: uid(),
      type: 'image',
      x,
      y,
      width,
      height,
      z_index: 1,
      image_url: asset.url,
      image_path: asset.path,
    };
    updatePages((pages) =>
      pages.map((p, i) => (i === pageIdx ? { ...p, blocks: [...p.blocks, block] } : p))
    );
    setActivePageIndex(pageIdx);
    setSelectedBlockId(block.id);
  };
  // Clear the user's override for a preset key so the built-in default is used
  // again. Persists immediately so reload reflects the reset.
  const resetPresetToDefault = (presetKey) => {
    const current = book.text_presets || {};
    if (!current[presetKey]) {
      toast.error('Already at the default');
      return;
    }
    // Set to null (instead of delete) so the value is explicit in the API call.
    const newPresets = { ...current, [presetKey]: null };
    setBook((b) => ({ ...b, text_presets: newPresets }));
    const label = TEXT_PRESETS[presetKey]?.label || presetKey;
    updateBook(book.id, {
      title: book.title,
      author: book.author,
      page_size: book.page_size,
      page_number_start: book.page_number_start || 1,
      is_chapter_book: !!book.is_chapter_book,
      text_presets: newPresets,
      pages: book.pages,
    })
      .then(() => {
        setLastSavedAt(new Date());
        toast.success(`"${label}" reset to default`);
      })
      .catch(() => toast.error('Could not reset preset'));
  };

  // Quick context-menu actions invoked from the Assets panel.
  const replaceSelectedImageWithAsset = async (asset) => {
    if (!selectedBlock || selectedBlock.type !== 'image') {
      toast.error('Select an image block first');
      return;
    }
    const previewUrl = asset.url.startsWith('http')
      ? asset.url
      : `${process.env.REACT_APP_BACKEND_URL}${asset.url}`;
    const { w, h } = await loadImageSize(previewUrl);
    // Keep block position and width; recompute height from the new image's
    // aspect ratio so the artwork isn't stretched.
    const ratio = w > 0 && h > 0 ? w / h : selectedBlock.width / selectedBlock.height;
    const newHeight = Math.round(selectedBlock.width / ratio);
    updateBlock(selectedBlock.id, {
      image_url: asset.url,
      image_path: asset.path,
      height: newHeight,
    });
    toast.success('Image replaced');
  };

  // Drop an asset on page 1 as a full-bleed backdrop.
  // Replaces any existing image on the cover so a single backdrop wins.
  const setAssetAsCoverBackdrop = (asset) => {
    const coverIdx = 0;
    const pageW = pageSize.width;
    const pageH = pageSize.height;
    const block = {
      id: uid(),
      type: 'image',
      x: 0,
      y: 0,
      width: pageW,
      height: pageH,
      z_index: 0,
      image_url: asset.url,
      image_path: asset.path,
    };
    updatePages((pages) =>
      pages.map((p, i) => {
        if (i !== coverIdx) return p;
        const withoutImages = (p.blocks || []).filter((b) => b.type !== 'image');
        return {
          ...p,
          full_bleed: true,
          show_page_number: false,
          blocks: [block, ...withoutImages],
        };
      })
    );
    setActivePageIndex(coverIdx);
    setSelectedBlockId(block.id);
    toast.success('Cover backdrop set');
  };


  const changeLayer = (action) => {
    if (!selectedBlock || !activePage) return;
    const all = activePage.blocks;
    const zs = all.map((b) => (typeof b.z_index === 'number' ? b.z_index : 1));
    const cur = typeof selectedBlock.z_index === 'number' ? selectedBlock.z_index : 1;
    let next = cur;
    if (action === 'forward') next = cur + 1;
    else if (action === 'backward') next = cur - 1;
    else if (action === 'front') next = Math.max(...zs) + 1;
    else if (action === 'back') next = Math.min(...zs) - 1;
    updateBlock(selectedBlock.id, { z_index: next });
  };

  // Resize the selected image block to fill the safe (or full-bleed) area of the page,
  // preserving the image's natural aspect ratio. The block is centered.
  const fitImageBlockToPage = async () => {
    if (!selectedBlock || selectedBlock.type !== 'image' || !selectedBlock.image_url) return;
    const url = selectedBlock.image_url.startsWith('http')
      ? selectedBlock.image_url
      : `${process.env.REACT_APP_BACKEND_URL}${selectedBlock.image_url}`;
    const { w, h } = await loadImageSize(url);
    const margin = activePage?.full_bleed ? 0 : PAGE_MARGIN_PX;
    const maxW = pageSize.width - margin * 2;
    const maxH = pageSize.height - margin * 2;
    const { width, height } = fitWithin(w || maxW, h || maxH, maxW, maxH);
    const x = (pageSize.width - width) / 2;
    const y = (pageSize.height - height) / 2;
    updateBlock(selectedBlock.id, { x, y, width, height });
    toast.success('Image fit to page');
  };

  // Resize the selected text block height to hug its current content.
  // Uses an off-DOM clone for measurement so we capture the true content height
  // even when the original is bound to a fixed parent height.
  const frameTextBlockToContent = () => {
    if (!selectedBlock || selectedBlock.type !== 'text') return;
    const el = document.querySelector(`[data-testid="text-block-content-${selectedBlock.id}"]`);
    if (!el) return;
    const cs = window.getComputedStyle(el);
    const probe = document.createElement('div');
    probe.innerHTML = sanitizeHtml(el.innerHTML);
    // Copy critical typography + box rules so the measured height matches the live render.
    probe.style.cssText = [
      `position:fixed`,
      `top:-99999px`,
      `left:-99999px`,
      `width:${el.clientWidth}px`,
      `font-family:${cs.fontFamily}`,
      `font-size:${cs.fontSize}`,
      `font-weight:${cs.fontWeight}`,
      `font-style:${cs.fontStyle}`,
      `line-height:${cs.lineHeight}`,
      `letter-spacing:${cs.letterSpacing}`,
      `text-align:${cs.textAlign}`,
      `padding:${cs.paddingTop} ${cs.paddingRight} ${cs.paddingBottom} ${cs.paddingLeft}`,
      `box-sizing:${cs.boxSizing}`,
      `white-space:normal`,
      `word-wrap:break-word`,
      `overflow:visible`,
      `visibility:hidden`,
    ].join(';');
    document.body.appendChild(probe);
    const measured = probe.offsetHeight;
    document.body.removeChild(probe);
    const newHeight = Math.max(32, Math.ceil(measured));
    updateBlock(selectedBlock.id, { height: newHeight });
    toast.success('Frame fit to text');
  };

  const fitSelectedBlock = () => {
    if (!selectedBlock) return;
    if (selectedBlock.type === 'image') return fitImageBlockToPage();
    if (selectedBlock.type === 'text') return frameTextBlockToContent();
  };

  // --- Page actions ---
  const addPage = () => {
    const ref = book?.pages?.[activePageIndex] || book?.pages?.[0];
    updatePages((pages) => [
      ...pages,
      {
        id: uid(),
        blocks: [],
        background_color: '#FFF8DC',
        show_page_number: true,
        page_number_align: 'right',
        page_number_size: ref?.page_number_size || 14,
        page_number_font: ref?.page_number_font || 'Cormorant Garamond',
      },
    ]);
    setActivePageIndex((book?.pages?.length || 0));
    setSelectedBlockId(null);
  };

  const duplicatePage = (idx) => {
    updatePages((pages) => {
      const copy = JSON.parse(JSON.stringify(pages[idx]));
      copy.id = uid();
      copy.blocks = copy.blocks.map((b) => ({ ...b, id: uid() }));
      const next = [...pages];
      next.splice(idx + 1, 0, copy);
      return next;
    });
  };

  // Insert a blank page after `idx` (or before everything if idx < 0).
  const insertPageAfter = (idx) => {
    const ref = book?.pages?.[idx] || book?.pages?.[0];
    const fresh = {
      id: uid(),
      blocks: [],
      background_color: '#FFF8DC',
      show_page_number: true,
      page_number_align: 'right',
      page_number_size: ref?.page_number_size || 14,
      page_number_font: ref?.page_number_font || 'Cormorant Garamond',
    };
    const insertAt = Math.max(0, idx + 1);
    updatePages((pages) => {
      const next = [...pages];
      next.splice(insertAt, 0, fresh);
      return next;
    });
    // Focus the brand-new page so the user lands on it immediately.
    setActivePageIndex(insertAt);
    setSelectedBlockId(null);
  };

  // Reorder a page by ±1 position. Keeps the moved page selected.
  const movePage = (idx, direction) => {
    const delta = direction === 'up' ? -1 : 1;
    const target = idx + delta;
    if (target < 0 || target >= book.pages.length) return;
    updatePages((pages) => {
      const next = [...pages];
      const [moved] = next.splice(idx, 1);
      next.splice(target, 0, moved);
      return next;
    });
    setActivePageIndex(target);
  };

  const removePage = (idx) => {
    if (book.pages.length <= 1) {
      toast.error('A book must have at least one page');
      return;
    }
    updatePages((pages) => pages.filter((_, i) => i !== idx));
    setActivePageIndex((cur) => Math.max(0, Math.min(cur, book.pages.length - 2)));
    setSelectedBlockId(null);
  };

  const togglePageNumber = (idx) => {
    updatePages((pages) =>
      pages.map((p, i) => (i === idx ? { ...p, show_page_number: !p.show_page_number } : p))
    );
  };

  // --- Text formatting commands ---
  const onTextCommand = (cmd, value = null) => {
    document.execCommand(cmd, false, value);
    // Sync HTML back to state for the editing block
    if (editingTextId) {
      const el = document.querySelector(`[data-testid="text-block-content-${editingTextId}"]`);
      if (el) updateBlock(editingTextId, { html: sanitizeHtml(el.innerHTML) });
    }
  };

  // --- Page navigation (Single & Spread) ---
  // Compute prev/next so a click or keyboard ←/→ jumps by 1 page in single
  // mode and by 2 pages in spread mode (one spread = two pages), with the
  // cover handled as its own half-spread.
  const navStep = useMemo(() => {
    if (!book) return { prev: null, next: null };
    const total = book.pages.length;
    if (total === 0) return { prev: null, next: null };
    const isSpread = viewMode === 'spread';
    const cur = activePageIndex;
    let prev = null;
    let next = null;
    if (!isSpread) {
      prev = cur > 0 ? cur - 1 : null;
      next = cur < total - 1 ? cur + 1 : null;
    } else {
      // In spread mode the cover sits alone, then pairs are (1,2), (3,4)…
      // From cover (0) → next spread starts at 1.
      // From index N inside a pair → next spread starts at (left of next pair).
      let leftOfCurrent;
      if (cur === 0) leftOfCurrent = 0;
      else leftOfCurrent = cur % 2 === 1 ? cur : cur - 1;
      // Prev: cover anchors the first half-spread; previous pair begins at
      // leftOfCurrent - 2 (clamped). If we're on the first pair (1), prev = 0 (cover).
      if (cur === 0) prev = null;
      else if (leftOfCurrent === 1) prev = 0;
      else prev = leftOfCurrent - 2;
      // Next: cover advances to pair start (1). Otherwise advance by 2.
      if (cur === 0) next = total > 1 ? 1 : null;
      else {
        const candidate = leftOfCurrent + 2;
        next = candidate < total ? candidate : null;
      }
    }
    return { prev, next };
  }, [book, activePageIndex, viewMode]);

  const goPrevPage = useCallback(() => {
    if (navStep.prev === null) return;
    setActivePageIndex(navStep.prev);
    setSelectedBlockId(null);
    setEditingTextId(null);
  }, [navStep.prev]);

  const goNextPage = useCallback(() => {
    if (navStep.next === null) return;
    setActivePageIndex(navStep.next);
    setSelectedBlockId(null);
    setEditingTextId(null);
  }, [navStep.next]);

  // Jump straight to a chapter from a TOC click (or tap on iPad).
  const onTocJump = useCallback((pageIndex) => {
    if (pageIndex == null || !book?.pages) return;
    if (pageIndex < 0 || pageIndex >= book.pages.length) return;
    setActivePageIndex(pageIndex);
    setSelectedBlockId(null);
    setEditingTextId(null);
  }, [book?.pages]);

  // Keep the active page's thumbnail in view in the left sidebar when the
  // active page changes (arrow click, keyboard ←/→, swipe, etc.). Without
  // this the user can advance past the visible scroll region and lose the
  // visual breadcrumb of where they are in the book.
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const el = document.querySelector(`[data-testid="page-thumbnail-${activePageIndex}"]`);
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [activePageIndex]);

  // --- Keyboard shortcuts ---
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.isContentEditable) return;
      // Don't steal arrow keys when the user is typing in an input/textarea.
      const tag = (e.target?.tagName || '').toLowerCase();
      const inField = tag === 'input' || tag === 'textarea' || tag === 'select';
      if (!inField && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (e.key === 'ArrowLeft') {
          e.preventDefault();
          goPrevPage();
          return;
        }
        if (e.key === 'ArrowRight') {
          e.preventDefault();
          goNextPage();
          return;
        }
      }
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedBlockId) {
        deleteBlock(selectedBlockId);
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        saveBook();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedBlockId, saveBook, goPrevPage, goNextPage]);

  // --- PDF export (server-side, job-based to survive proxy timeouts) ---
  // `range` is an optional `{ start, end }` 1-indexed inclusive slice.
  // Used by the export popover to ship a long book as several PDFs.
  // `options.previewInTab=true` opens the result in a new browser tab
  // (using the browser's built-in PDF viewer) instead of triggering a
  // download — useful for iterating on layout without piling up files.
  const onExportPdf = async (range = null, options = {}) => {
    const previewInTab = options?.previewInTab === true;
    const pdfx = options?.pdfx === true;
    const coverSpread = options?.coverSpread === true;
    const spineWidthIn = options?.spineWidthIn;
    if (!book) return;
    setExporting(true);
    const toastId = 'pdf-export';
    let kindLabel;
    if (coverSpread) {
      kindLabel = ' cover spread';
    } else if (range) {
      kindLabel = ` (pages ${range.start}–${range.end})`;
    } else {
      kindLabel = '';
    }
    const modeLabel = pdfx ? ' · Print-ready' : '';
    toast.loading(`Building PDF${kindLabel}${modeLabel}…`, { id: toastId });
    const t0 = performance.now();
    const BASE = process.env.REACT_APP_BACKEND_URL;
    try {
      // Persist any in-flight edits before the server renders.
      await saveBook(false);
      // Kick off the background job. This call returns in milliseconds —
      // proxies and CDNs never see a long-lived request.
      // Note: URL avoids `.pdf` in the path because some CDNs treat dot-pdf
      // URLs as static-file fetches and short-circuit with 404.
      const bodyObj = {};
      if (range) { bodyObj.start_page = range.start; bodyObj.end_page = range.end; }
      if (pdfx) bodyObj.pdfx = true;
      if (coverSpread) bodyObj.cover_spread = true;
      if (spineWidthIn != null) bodyObj.spine_width_in = spineWidthIn;
      const body = Object.keys(bodyObj).length ? JSON.stringify(bodyObj) : undefined;
      // All `/api/*` calls require the JWT — pull it from the same store
      // axios uses so we don't bypass auth when using raw fetch.
      const token = (() => {
        try { return localStorage.getItem('bindery_token'); } catch { return null; }
      })();
      const authHeaders = token ? { Authorization: `Bearer ${token}` } : {};
      const startResp = await fetch(`${BASE}/api/books/${book.id}/pdf-jobs`, {
        method: 'POST',
        headers: {
          ...(body ? { 'Content-Type': 'application/json' } : {}),
          ...authHeaders,
        },
        body,
      });
      if (!startResp.ok) {
        let detail = `HTTP ${startResp.status}`;
        try { const b = await startResp.json(); if (b?.detail) detail = b.detail; } catch {}
        throw new Error(detail);
      }
      const { job_id } = await startResp.json();
      // Poll status. Cap at ~10 min so a cold start (Chromium install) or a
      // very large book still has time to finish. Each individual request
      // is sub-second; only the wall-clock can grow.
      const STATUS_URL = `${BASE}/api/books/${book.id}/pdf-jobs/${job_id}`;
      const start = Date.now();
      let lastStatus = 'pending';
      let lastStage = '';
      let serverFilename = null;
      while (Date.now() - start < 600_000) {
        await new Promise((r) => setTimeout(r, 1200));
        const s = await fetch(STATUS_URL, { headers: authHeaders });
        if (!s.ok) {
          if (s.status === 404) throw new Error('PDF job expired — please try again');
          continue; // transient — keep polling
        }
        const sb = await s.json();
        lastStatus = sb.status;
        if (sb.stage) lastStage = sb.stage;
        if (sb.status === 'ready') { serverFilename = sb.filename || null; break; }
        if (sb.status === 'failed') {
          const detail = sb.error || 'PDF build failed';
          const tail = sb.trace ? ` (${String(sb.trace).slice(0, 120)})` : '';
          throw new Error(`${detail}${tail}`);
        }
        // Otherwise (pending) — show stage + elapsed on the toast.
        const elapsed = Math.round((Date.now() - start) / 1000);
        const label = lastStage ? `${lastStage} · ${elapsed}s` : `${elapsed}s`;
        toast.loading(`Building PDF${kindLabel}… ${label}`, { id: toastId });
      }
      if (lastStatus !== 'ready') {
        const stageHint = lastStage ? ` (stuck at: ${lastStage})` : '';
        throw new Error(`PDF timed out${stageHint} — try again or check /api/pdf-health`);
      }

      // Stream the bytes — this is a fast, fully-buffered response, so no
      // proxy timeout risk.
      const dl = await fetch(`${BASE}/api/books/${book.id}/pdf-jobs/${job_id}/download`, {
        headers: authHeaders,
      });
      if (!dl.ok) throw new Error(`Download failed (HTTP ${dl.status})`);
      const blob = await dl.blob();
      // Prefer the server's filename (carries the page-range suffix for
      // partial exports). Fall back to a sanitised title if missing.
      const fallback = `${(book.title || 'book').replace(/[^a-z0-9-_]+/gi, '_')}.pdf`;
      const downloadName = serverFilename || fallback;
      const url = URL.createObjectURL(blob);
      if (previewInTab) {
        // Open in a new tab so the browser's built-in PDF viewer renders
        // it. We do NOT immediately revoke the object URL — the new tab
        // still needs it. Schedule revoke after a generous delay; the
        // browser caches the blob, so navigating away from the tab is
        // fine, and worst case the OS reclaims the memory on tab close.
        const win = window.open(url, '_blank', 'noopener,noreferrer');
        if (!win) {
          // Popup blocked — fall back to download so the user still gets
          // their PDF rather than a silent no-op.
          const a = document.createElement('a');
          a.href = url;
          a.download = downloadName;
          document.body.appendChild(a);
          a.click();
          a.remove();
          toast.warning(
            'Popup blocked — downloaded instead. Allow popups for this site to use Preview-in-tab.',
            { id: toastId, duration: 8000 },
          );
          // Old toast was replaced; create a new "exported" one below.
        }
        // Revoke after 60s; the new tab has well-cached the bytes by then.
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      } else {
        const a = document.createElement('a');
        a.href = url;
        a.download = downloadName;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      }
      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      toast.success(
        previewInTab ? `PDF ready in ${secs}s — opened in new tab` : `PDF exported in ${secs}s`,
        { id: toastId },
      );
      // Refresh the export-history list so the popover updates immediately.
      setExportsBump((n) => n + 1);
    } catch (e) {
      console.error(e);
      const raw = e?.message || 'Export failed';
      const isNetwork =
        e?.name === 'AbortError' ||
        /failed to fetch|networkerror|load failed/i.test(raw);
      const friendly = isNetwork
        ? 'Couldn\'t reach the PDF service. Check your connection and try again.'
        : `Export failed: ${raw.slice(0, 200)}`;
      toast.error(friendly, { id: toastId, duration: 10000 });
    } finally {
      setExporting(false);
    }
  };

  if (loading || !book) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-desk">
        <p className="font-serif italic text-2xl text-ink-mute">Opening the book…</p>
      </div>
    );
  }

  // In spread view, show the active page and its sibling (left = even index, right = odd index).
  // Page 0 (cover) shows alone on the right side.
  const renderSpread = () => {
    const total = book.pages.length;
    let leftIdx;
    let rightIdx;
    if (activePageIndex === 0) {
      leftIdx = null;
      rightIdx = 0;
    } else if (activePageIndex % 2 === 1) {
      leftIdx = activePageIndex;
      rightIdx = activePageIndex + 1 < total ? activePageIndex + 1 : null;
    } else {
      leftIdx = activePageIndex - 1;
      rightIdx = activePageIndex;
    }
    const renderOne = (idx) => {
      if (idx === null) return <SpreadPlaceholder pageSize={pageSize} />;
      const p = book.pages[idx];
      return (
        <PageCanvas
          page={p}
          pageIndex={idx}
          pageSize={pageSize}
          viewMode="spread"
          totalPages={book.pages.length}
          pageNumberStart={book.page_number_start || 1}
          isFocused={idx === activePageIndex}
          selectedBlockId={selectedBlockId}
          editingTextId={editingTextId}
          assetCacheBuster={assetCacheBuster}
          onSelectBlock={(blockId) => { setActivePageIndex(idx); setSelectedBlockId(blockId); }}
          onChangeBlock={(blockId, patch) => updateBlock(blockId, patch, idx)}
          onStartTextEdit={(blockId) => { setActivePageIndex(idx); setSelectedBlockId(blockId); setEditingTextId(blockId); }}
          onStopTextEdit={() => setEditingTextId(null)}
          onAssetDrop={(asset, pos) => addImageBlockFromAsset(asset, pos, idx)}
          onFocusPage={() => setActivePageIndex(idx)}
          onTocJump={onTocJump}
        />
      );
    };
    return (
      <>
        {renderOne(leftIdx)}
        {renderOne(rightIdx)}
      </>
    );
  };

  return (
    <div className="h-screen w-screen flex flex-col bg-desk overflow-hidden">
      {/* Top bar.
          Two-section layout so Save + Export PDF are ALWAYS visible:
          - left: a horizontally-scrollable strip with all secondary
            controls (title, page-size, view mode, insert tools, etc.).
          - right: a sticky/pinned cluster with the actions a user reaches
            for most often. On iPad portrait the left strip becomes
            scrollable; the right cluster never leaves the viewport. */}
      <header className="h-14 border-b border-rule bg-paper flex items-center z-20 min-w-0">
        <div className="flex-1 flex items-center px-3 gap-2 min-w-0 overflow-x-auto desk-scroll">
        <Button
          variant="ghost"
          onClick={() => navigate('/')}
          className="rounded-sm text-ink hover:bg-desk px-2 shrink-0"
          data-testid="back-to-library"
        >
          <ArrowLeft className="w-4 h-4 lg:mr-1" /> <span className="hidden lg:inline">Library</span>
        </Button>
        {/* Sidebar toggles — only visible below lg. On lg+ the sidebars are
            statically pinned so these would be redundant. */}
        <button
          type="button"
          onClick={() => setPagesDrawerOpen((o) => !o)}
          className="lg:hidden h-8 px-2 rounded-sm text-ink hover:bg-desk inline-flex items-center gap-1 shrink-0"
          data-testid="toggle-pages-drawer"
          aria-label="Toggle pages panel"
          aria-pressed={pagesDrawerOpen}
        >
          <PanelLeft className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={() => setPanelsDrawerOpen((o) => !o)}
          className="lg:hidden h-8 px-2 rounded-sm text-ink hover:bg-desk inline-flex items-center gap-1 shrink-0"
          data-testid="toggle-panels-drawer"
          aria-label="Toggle properties panel"
          aria-pressed={panelsDrawerOpen}
        >
          <Layers className="w-4 h-4" />
        </button>
        <div className="w-px h-6 bg-rule shrink-0" />
        <Input
          value={book.title}
          onChange={(e) => setBook({ ...book, title: e.target.value })}
          className="bg-transparent border-0 font-serif text-xl text-ink focus-visible:ring-1 focus-visible:ring-terracotta rounded-sm w-32 sm:w-40 lg:w-56 xl:w-64 min-w-0 shrink-0"
          data-testid="book-title-input"
        />
        <Input
          value={book.author || ''}
          onChange={(e) => setBook({ ...book, author: e.target.value })}
          placeholder="Author"
          className="bg-transparent border-0 text-ink-soft text-sm italic w-24 lg:w-32 xl:w-40 focus-visible:ring-1 focus-visible:ring-terracotta rounded-sm min-w-0 hidden lg:block shrink-0"
          data-testid="book-author-input"
        />
        <div className="w-px h-6 bg-rule shrink-0 hidden lg:block" />
        <Select value={book.page_size} onValueChange={(v) => setBook({ ...book, page_size: v })}>
          <SelectTrigger className="bg-white border-rule rounded-sm h-8 w-24 lg:w-32 xl:w-36 text-sm shrink-0" data-testid="page-size-trigger">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(PAGE_SIZES).map(([k, v]) => (
              <SelectItem key={k} value={k} data-testid={`pagesize-opt-${k}`}>{v.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <div className="w-px h-6 bg-rule shrink-0 hidden lg:block" />
        <div className="hidden lg:flex items-center bg-white border border-rule rounded-sm h-8 p-0.5 shrink-0" data-testid="view-mode-toggle">
          <button
            type="button"
            onClick={() => setViewMode('single')}
            data-testid="view-mode-single"
            className={`h-7 px-2.5 flex items-center gap-1 rounded-sm text-xs tracking-wide transition-colors ${viewMode === 'single' ? 'bg-ink text-paper' : 'text-ink-soft hover:bg-desk'}`}
            title="Single page"
          >
            <Square className="w-3.5 h-3.5" /> <span className="hidden xl:inline">Single</span>
          </button>
          <button
            type="button"
            onClick={() => setViewMode('spread')}
            data-testid="view-mode-spread"
            className={`h-7 px-2.5 flex items-center gap-1 rounded-sm text-xs tracking-wide transition-colors ${viewMode === 'spread' ? 'bg-ink text-paper' : 'text-ink-soft hover:bg-desk'}`}
            title="Two-page spread"
          >
            <BookOpen className="w-3.5 h-3.5" /> <span className="hidden xl:inline">Spread</span>
          </button>
        </div>

        <div className="flex-1 min-w-0" />

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button className="bg-ink hover:bg-ink-soft text-paper rounded-sm h-8 shrink-0 px-2 lg:px-3" data-testid="add-text-button">
              <TypeIcon className="w-4 h-4 lg:mr-1" /> <span className="hidden lg:inline">Text</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="bg-paper border-rule rounded-sm w-52 p-1">
            {[
              { key: 'title', label: 'Title' },
              { key: 'subtitle', label: 'Subtitle' },
              { key: 'body', label: 'Page text' },
            ].map(({ key, label }) => {
              // Reflect the user's saved override (if any) so the menu always
              // shows the font + size the block will actually use.
              const base = TEXT_PRESETS[key];
              const user = book?.text_presets?.[key] || {};
              const effFont = user.font_family || base.font_family;
              const effSize = user.font_size || base.font_size;
              const isCustom = !!(user.font_family || user.font_size);
              return (
                <DropdownMenuItem
                  key={key}
                  data-testid={`add-text-${key}`}
                  onClick={() => addTextBlock(key)}
                  className="rounded-sm cursor-pointer flex flex-col items-start gap-0 py-2"
                >
                  <span style={{ fontFamily: effFont, fontSize: key === 'body' ? 14 : key === 'subtitle' ? 16 : 18, fontStyle: key === 'subtitle' ? 'italic' : 'normal' }}>
                    {label}
                  </span>
                  <span className="text-[10px] text-ink-mute">
                    {effFont} · {effSize}px
                    {isCustom && <span className="ml-1 text-terracotta">· custom</span>}
                  </span>
                </DropdownMenuItem>
              );
            })}
          </DropdownMenuContent>
        </DropdownMenu>
        {activePageIndex === 0 && (
          <Button
            onClick={designCover}
            className="bg-terracotta/90 hover:bg-terracotta text-paper rounded-sm h-8 shrink-0 px-2 lg:px-3"
            data-testid="design-cover-button"
            title="Auto-arrange a cover from your title, author and any artwork on this page"
          >
            <Wand2 className="w-4 h-4 lg:mr-1" /> <span className="hidden lg:inline">Design cover</span>
          </Button>
        )}
        {book.is_chapter_book && (
          <Button onClick={addChapterBlock} className="bg-ink hover:bg-ink-soft text-paper rounded-sm h-8 shrink-0 px-2 lg:px-3" data-testid="add-chapter-button">
            <Heading className="w-4 h-4 lg:mr-1" /> <span className="hidden lg:inline">Chapter</span>
          </Button>
        )}
        {book.is_chapter_book && (
          <Button onClick={addTocBlock} className="bg-ink hover:bg-ink-soft text-paper rounded-sm h-8 shrink-0 px-2 lg:px-3" data-testid="add-toc-button" title="Insert Table of Contents from chapter headings">
            <List className="w-4 h-4 lg:mr-1" /> <span className="hidden lg:inline">Contents</span>
          </Button>
        )}
        <Button
          onClick={() => fileInputRef.current?.click()}
          className="bg-ink hover:bg-ink-soft text-paper rounded-sm h-8 shrink-0 px-2 lg:px-3"
          data-testid="add-image-button"
        >
          <ImageIcon className="w-4 h-4 lg:mr-1" /> <span className="hidden lg:inline">Image</span>
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          className="hidden"
          data-testid="image-file-input"
          onChange={(e) => {
            const f = e.target.files?.[0];
            handleImageUpload(f);
            e.target.value = '';
          }}
        />
        <div className="w-px h-6 bg-rule mx-1 shrink-0 hidden md:block" />
        <div className="hidden md:block shrink-0">
          <SaveStatus saving={saving} lastSavedAt={lastSavedAt} />
        </div>
        <div className="shrink-0">
          <HistoryDialog
            bookId={book.id}
            onRestored={(restored) => {
              // Skip the next autosave so the restored snapshot isn't immediately
              // overwritten by a stale in-memory state.
              skipNextAutoSaveRef.current = true;
              setBook(restored);
              setSelectedBlockId(null);
              setEditingTextId(null);
              setActivePageIndex(0);
            }}
          />
        </div>
        <div className="shrink-0">
          <SaveTemplateDialog book={book} />
        </div>
        </div>
        {/* Pinned right cluster — never scrolls off-screen. */}
        <div className="flex items-center gap-2 px-3 border-l border-rule shrink-0 bg-paper h-full">
        <Button
          onClick={() => saveBook()}
          variant="outline"
          className="rounded-sm h-8 border-rule shrink-0 px-2 lg:px-3"
          disabled={saving}
          data-testid="save-button"
        >
          {saving ? <Loader2 className="w-4 h-4 lg:mr-1 animate-spin" /> : <Save className="w-4 h-4 lg:mr-1" />}
          <span className="hidden lg:inline">Save</span>
        </Button>
        <ExportPopover
          bookId={book.id}
          totalPages={book.pages.length}
          exporting={exporting}
          onExport={onExportPdf}
          refreshKey={exportsBump}
        />
        </div>
      </header>

      <div className="flex-1 flex overflow-hidden relative">
        {/* Backdrop — covers the desk when either drawer is open on small
            screens. Tapping it closes both drawers. Hidden entirely on lg+. */}
        {(pagesDrawerOpen || panelsDrawerOpen) && (
          <button
            type="button"
            onClick={() => { setPagesDrawerOpen(false); setPanelsDrawerOpen(false); }}
            data-testid="sidebar-backdrop"
            aria-label="Close panels"
            className="lg:hidden fixed inset-0 top-14 bg-ink/40 backdrop-blur-[1px] z-20 transition-opacity"
          />
        )}
        {/* Left sidebar: page thumbnails.
            On lg+: static column. Below lg: slide-in drawer triggered by the
            "Pages" toolbar icon. Backdrop above closes it. */}
        <aside
          data-testid="left-sidebar"
          className={`bg-ink text-paper border-r border-rule-dark flex flex-col w-56 lg:static lg:translate-x-0 fixed left-0 top-14 bottom-0 z-30 transform transition-transform duration-200 ease-out ${pagesDrawerOpen ? 'translate-x-0' : '-translate-x-full'}`}
        >
          <div className="px-4 py-3 border-b border-rule-dark flex items-center justify-between">
            <p className="label-caps text-ink-mute">Pages</p>
            <span className="text-xs text-ink-mute">{book.pages.length}</span>
          </div>
          <div className="flex-1 overflow-y-auto sidebar-scroll px-3 py-3" data-testid="pages-list">
            {book.pages.map((p, i) => (
              <div key={p.id}>
                <PageThumbnail
                  page={p}
                  index={i}
                  active={i === activePageIndex}
                  pageSize={pageSize}
                  totalPages={book.pages.length}
                  pageNumberStart={book.page_number_start || 1}
                  assetCacheBuster={assetCacheBuster}
                  onClick={() => { setActivePageIndex(i); setSelectedBlockId(null); }}
                  onDuplicate={() => duplicatePage(i)}
                  onDelete={() => removePage(i)}
                  onTogglePageNumber={() => togglePageNumber(i)}
                  onMoveUp={i > 0 ? () => movePage(i, 'up') : null}
                  onMoveDown={i < book.pages.length - 1 ? () => movePage(i, 'down') : null}
                />
                {/* Between-pages insert gutter — hidden until you hover it. */}
                <button
                  type="button"
                  onClick={() => insertPageAfter(i)}
                  data-testid={`insert-page-after-${i}`}
                  title="Insert a new page here"
                  className="group/insert w-full h-3 my-1 flex items-center justify-center opacity-0 hover:opacity-100 transition-opacity"
                >
                  <span className="h-px flex-1 bg-terracotta/60" />
                  <span className="mx-1 flex items-center justify-center w-4 h-4 rounded-full bg-terracotta text-paper">
                    <Plus className="w-2.5 h-2.5" strokeWidth={3} />
                  </span>
                  <span className="h-px flex-1 bg-terracotta/60" />
                </button>
              </div>
            ))}
          </div>
          <div className="p-3 border-t border-rule-dark">
            <Button
              onClick={addPage}
              className="w-full bg-rule-dark hover:bg-rule-dark/70 text-paper rounded-sm justify-start h-9"
              data-testid="add-page-button"
            >
              <Plus className="w-4 h-4 mr-2" /> Add Page
            </Button>
          </div>
        </aside>

        {/* Desk */}
        <main
          className="flex-1 overflow-auto desk-scroll relative"
          // Only clear selection when the mousedown lands DIRECTLY on the
          // desk background — never when it's bubbling up from a child
          // (block, page canvas, page handles). On iOS Safari the touch
          // event sequence differs from desktop and the previous unfiltered
          // handler was tearing down active text-editing as soon as the
          // user tapped inside the contentEditable to position their caret.
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) {
              setSelectedBlockId(null);
              setEditingTextId(null);
            }
          }}
          // Horizontal swipe to navigate pages (iPad / trackpad). We only
          // act on a clear horizontal flick — vertical scroll is preserved.
          onTouchStart={(e) => {
            if (e.touches.length !== 1) { deskTouchRef.current = null; return; }
            const t = e.touches[0];
            deskTouchRef.current = { x: t.clientX, y: t.clientY, t: Date.now() };
          }}
          onTouchEnd={(e) => {
            const start = deskTouchRef.current;
            deskTouchRef.current = null;
            if (!start) return;
            const t = e.changedTouches[0];
            const dx = t.clientX - start.x;
            const dy = t.clientY - start.y;
            const dt = Date.now() - start.t;
            // Require a clean horizontal swipe: large dx, small dy, quick.
            if (Math.abs(dx) < 70 || Math.abs(dy) > 50 || dt > 600) return;
            if (dx < 0) goNextPage(); else goPrevPage();
          }}
        >
          {/* Page-navigation arrows. Vertically centred over the desk so
              they're always reachable regardless of scroll position. */}
          {navStep.prev !== null && (
            <button
              type="button"
              onClick={goPrevPage}
              data-testid="page-nav-prev"
              aria-label="Previous page"
              title={viewMode === 'spread' ? 'Previous spread (←)' : 'Previous page (←)'}
              className="hidden md:flex absolute left-3 top-1/2 -translate-y-1/2 z-10 w-10 h-10 items-center justify-center rounded-full bg-paper/85 hover:bg-paper border border-rule shadow-sm text-ink hover:text-terracotta transition-colors"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>
          )}
          {navStep.next !== null && (
            <button
              type="button"
              onClick={goNextPage}
              data-testid="page-nav-next"
              aria-label="Next page"
              title={viewMode === 'spread' ? 'Next spread (→)' : 'Next page (→)'}
              className="hidden md:flex absolute right-3 top-1/2 -translate-y-1/2 z-10 w-10 h-10 items-center justify-center rounded-full bg-paper/85 hover:bg-paper border border-rule shadow-sm text-ink hover:text-terracotta transition-colors"
            >
              <ChevronRight className="w-5 h-5" />
            </button>
          )}
          <div
            className="min-h-full flex items-center justify-center p-4 sm:p-6 lg:p-12 gap-6"
            onMouseDown={(e) => {
              // Same rule for the inner flex wrapper.
              if (e.target === e.currentTarget) {
                setSelectedBlockId(null);
                setEditingTextId(null);
              }
            }}
          >
            {viewMode === 'spread' ? (
              renderSpread()
            ) : (
              <PageCanvas
                page={activePage}
                pageIndex={activePageIndex}
                pageSize={pageSize}
                viewMode="single"
                totalPages={book.pages.length}
                pageNumberStart={book.page_number_start || 1}
                selectedBlockId={selectedBlockId}
                editingTextId={editingTextId}
                assetCacheBuster={assetCacheBuster}
                onSelectBlock={(blockId) => setSelectedBlockId(blockId)}
                onChangeBlock={updateBlock}
                onStartTextEdit={(blockId) => { setSelectedBlockId(blockId); setEditingTextId(blockId); }}
                onStopTextEdit={() => setEditingTextId(null)}
                onAssetDrop={(asset, pos) => addImageBlockFromAsset(asset, pos)}
                onFocusPage={() => setActivePageIndex(activePageIndex)}
                onTocJump={onTocJump}
              />
            )}
          </div>
        </main>

        {/* Right sidebar: Assets / Page / Block tabs.
            On lg+: static. Below lg: slide-in drawer from the right. */}
        <aside
          data-testid="right-sidebar"
          className={`bg-ink text-paper border-l border-rule-dark flex flex-col w-72 lg:static lg:translate-x-0 fixed right-0 top-14 bottom-0 z-30 transform transition-transform duration-200 ease-out ${panelsDrawerOpen ? 'translate-x-0' : 'translate-x-full'}`}
        >
          <Tabs value={rightTab} onValueChange={setRightTab} className="flex flex-col h-full">
            <TabsList className="w-full grid grid-cols-3 rounded-none bg-rule-dark/40 border-b border-rule-dark h-10 p-1">
              <TabsTrigger
                value="assets"
                data-testid="right-tab-assets"
                className="rounded-sm data-[state=active]:bg-ink data-[state=active]:text-paper text-ink-mute text-[10px] tracking-[0.18em] uppercase"
              >
                <ImageIcon className="w-3.5 h-3.5 mr-1" /> Assets
              </TabsTrigger>
              <TabsTrigger
                value="page"
                data-testid="right-tab-page"
                className="rounded-sm data-[state=active]:bg-ink data-[state=active]:text-paper text-ink-mute text-[10px] tracking-[0.18em] uppercase"
              >
                <Palette className="w-3.5 h-3.5 mr-1" /> Page
              </TabsTrigger>
              <TabsTrigger
                value="block"
                data-testid="right-tab-block"
                className="rounded-sm data-[state=active]:bg-ink data-[state=active]:text-paper text-ink-mute text-[10px] tracking-[0.18em] uppercase"
              >
                <SlidersHorizontal className="w-3.5 h-3.5 mr-1" /> Block
              </TabsTrigger>
            </TabsList>
            <TabsContent value="assets" className="flex-1 m-0 overflow-hidden">
              <AssetsPanel
                bookId={book.id}
                onAssetUploaded={() => { /* refresh inside */ }}
                onAssetReplaced={() => setAssetCacheBuster((n) => n + 1)}
                onInsertAsset={(asset) => addImageBlockFromAsset(asset)}
                onReplaceSelectedImage={replaceSelectedImageWithAsset}
                onSetAsCoverBackdrop={setAssetAsCoverBackdrop}
                canReplaceSelected={!!selectedBlock && selectedBlock.type === 'image'}
              />
            </TabsContent>
            <TabsContent value="page" className="flex-1 m-0 overflow-y-auto sidebar-scroll bg-paper text-ink">
              <PagePanel
                page={activePage}
                pageIndex={activePageIndex}
                totalPages={book.pages.length}
                pageNumberStart={book.page_number_start || 1}
                isChapterBook={!!book.is_chapter_book}
                onChange={(patch) => updatePage(patch)}
                onPageNumberStyleAll={setAllPagesNumberStyle}
                onPageNumberStartChange={(n) => setBook((b) => ({ ...b, page_number_start: n }))}
                onChapterBookToggle={(v) => setBook((b) => ({ ...b, is_chapter_book: v }))}
                onApplyToInterior={applyPageToInterior}
              />
            </TabsContent>
            <TabsContent value="block" className="flex-1 m-0 overflow-y-auto sidebar-scroll bg-paper text-ink">
              <LayersList
                page={activePage}
                selectedBlockId={selectedBlockId}
                onSelect={(id) => setSelectedBlockId(id)}
              />
              {selectedBlock ? (
                <BlockProperties
                  block={selectedBlock}
                  onChange={(patch) => updateBlock(selectedBlock.id, patch)}
                  onDelete={() => deleteBlock(selectedBlock.id)}
                  onTextCommand={onTextCommand}
                  onLayer={changeLayer}
                  onFit={fitSelectedBlock}
                  onSaveAsPreset={saveBlockAsPreset}
                  onResetPreset={resetPresetToDefault}
                  textPresets={book.text_presets}
                />
              ) : (
                <div className="p-6 text-ink-mute text-sm font-serif italic">Select a block above to edit its properties.</div>
              )}
            </TabsContent>
          </Tabs>
        </aside>
      </div>
    </div>
  );
}

function timeAgo(d) {
  const seconds = Math.max(1, Math.floor((Date.now() - d.getTime()) / 1000));
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return `${h}h ago`;
}

function SaveStatus({ saving, lastSavedAt }) {
  if (saving) {
    return (
      <span data-testid="save-status" className="text-xs text-ink-mute flex items-center gap-1">
        <Loader2 className="w-3 h-3 animate-spin" /> Saving…
      </span>
    );
  }
  if (lastSavedAt) {
    return (
      <span data-testid="save-status" className="text-xs text-ink-mute">
        Saved {timeAgo(lastSavedAt)}
      </span>
    );
  }
  return null;
}


function isDarkHex(hex) {
  if (!hex || typeof hex !== 'string') return false;
  const h = hex.replace('#', '');
  const v = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  if (v.length !== 6) return false;
  const r = parseInt(v.slice(0, 2), 16);
  const g = parseInt(v.slice(2, 4), 16);
  const b = parseInt(v.slice(4, 6), 16);
  // Perceived luminance (Rec. 709).
  const L = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return L < 0.55;
}

function PageCanvas({
  page,
  pageIndex,
  pageSize,
  selectedBlockId,
  editingTextId,
  onSelectBlock,
  onChangeBlock,
  onStartTextEdit,
  onStopTextEdit,
  onAssetDrop,
  onFocusPage,
  onTocJump,
  viewMode = 'single',
  isFocused = true,
  forExport = false,
  totalPages = 1,
  pageNumberStart = 1,
  assetCacheBuster = 0,
}) {  // Scale to fit viewport for editing mode (export uses full size)
  const [fitScale, setFitScale] = useState(1);
  // User-driven pinch zoom (touch devices). Multiplied with the auto-fit
  // scale below so a small iPad still gets sensible defaults, but the user
  // can pinch in/out to inspect detail.
  const [userZoom, setUserZoom] = useState(1);
  // Mirror in a ref so the native touch listener can read the latest
  // value without being reinstalled on every zoom delta (which would
  // drop in-flight touch events mid-gesture).
  const userZoomRef = useRef(1);
  useEffect(() => { userZoomRef.current = userZoom; }, [userZoom]);
  const scale = fitScale * userZoom;
  const [dropHover, setDropHover] = useState(false);
  const wrapperRef = useRef(null);
  // Outer touch-target — covers the full page footprint including any
  // react-rnd blocks. Listener attached with `capture: true` so pinch
  // works even if a child element (a block being touched) swallows
  // touch events on its own.
  const touchHostRef = useRef(null);
  // Pinch gesture state — only used during an active 2-finger touch.
  const pinchRef = useRef(null);

  useEffect(() => {
    if (forExport) { setFitScale(1); return; }
    const compute = () => {
      const padding = 96; // px padding around
      const sidebars = 224 + 288; // left + right
      const availW = window.innerWidth - sidebars - padding;
      const availH = window.innerHeight - 56 /* topbar */ - padding;
      const widthMultiplier = viewMode === 'spread' ? 2 : 1;
      const gap = viewMode === 'spread' ? 24 : 0;
      const s = Math.min(
        1,
        (availW - gap) / (pageSize.width * widthMultiplier),
        availH / pageSize.height
      );
      setFitScale(Math.max(0.18, s));
    };
    compute();
    window.addEventListener('resize', compute);
    return () => window.removeEventListener('resize', compute);
  }, [pageSize.width, pageSize.height, forExport, viewMode]);

  // --- Pinch-to-zoom (2-finger gesture, touch only) ---
  // Implemented with native event listeners (not React's synthetic touch
  // events) because we need `passive: false` to call preventDefault and
  // stop iOS's built-in browser pinch-zoom of the whole viewport.
  useEffect(() => {
    if (forExport) return undefined;
    const node = touchHostRef.current;
    if (!node) return undefined;
    const dist = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    const onTouchStart = (e) => {
      if (e.touches.length !== 2) return;
      pinchRef.current = {
        startDist: dist(e.touches[0], e.touches[1]),
        startZoom: userZoomRef.current,
      };
      e.preventDefault();
    };
    const onTouchMove = (e) => {
      if (e.touches.length !== 2 || !pinchRef.current) return;
      const d = dist(e.touches[0], e.touches[1]);
      const ratio = d / pinchRef.current.startDist;
      // Clamp to a sensible range — below 0.5 the page becomes unusable,
      // above 4 you're effectively just looking at one block.
      const next = Math.max(0.5, Math.min(4, pinchRef.current.startZoom * ratio));
      setUserZoom(next);
      e.preventDefault();
    };
    const onTouchEnd = (e) => {
      if (e.touches.length < 2) pinchRef.current = null;
    };
    node.addEventListener('touchstart', onTouchStart, { passive: false, capture: true });
    node.addEventListener('touchmove', onTouchMove, { passive: false, capture: true });
    node.addEventListener('touchend', onTouchEnd, { passive: true, capture: true });
    node.addEventListener('touchcancel', onTouchEnd, { passive: true, capture: true });
    return () => {
      node.removeEventListener('touchstart', onTouchStart, { capture: true });
      node.removeEventListener('touchmove', onTouchMove, { capture: true });
      node.removeEventListener('touchend', onTouchEnd, { capture: true });
      node.removeEventListener('touchcancel', onTouchEnd, { capture: true });
    };
  }, [forExport]);

  if (!page) return null;

  const handleDragOver = (e) => {
    if (forExport || !onAssetDrop) return;
    if (Array.from(e.dataTransfer.types || []).includes(ASSET_DRAG_MIME)) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      setDropHover(true);
    }
  };
  const handleDragLeave = () => setDropHover(false);
  const handleDrop = (e) => {
    if (forExport || !onAssetDrop) return;
    const raw = e.dataTransfer.getData(ASSET_DRAG_MIME) || e.dataTransfer.getData('text/plain');
    if (!raw) return;
    e.preventDefault();
    setDropHover(false);
    let asset;
    try { asset = JSON.parse(raw); } catch { return; }
    if (!asset?.url) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - rect.left) / scale;
    const y = (e.clientY - rect.top) / scale;
    onAssetDrop(asset, { x, y });
  };

  const bg = page.background_color || '#F9F6F0';
  const fullBleed = !!page.full_bleed;
  const margin = fullBleed ? 0 : PAGE_MARGIN_PX;
  const innerW = pageSize.width - margin * 2;
  const innerH = pageSize.height - margin * 2;
  const showFocusRing = viewMode === 'spread' && isFocused && !forExport;
  const pageNumAlign = page.page_number_align || 'right';
  const pageNumSize = page.page_number_size || 14;
  const pageNumFont = page.page_number_font || 'Cormorant Garamond';
  const pageNumColor = isDarkHex(bg) ? '#E8E2D4' : '#3A3833';
  // Page number visibility & label.
  // - Back cover (last page) is always hidden.
  // - Pages before pageNumberStart are hidden.
  // - The displayed number is 1-based starting at the configured start page.
  const isBackCover = totalPages > 1 && pageIndex === totalPages - 1;
  const oneBasedIndex = pageIndex + 1;
  const isBeforeStart = oneBasedIndex < pageNumberStart;
  const showPageNumber = !!page.show_page_number && !isBackCover && !isBeforeStart;
  const displayedNumber = oneBasedIndex - pageNumberStart + 1;

  return (
    <div
      ref={touchHostRef}
      style={{
        width: pageSize.width * scale,
        height: pageSize.height * scale,
        position: 'relative',
        // iOS Safari's default touch-action on contentEditable areas is
        // very permissive. Disabling browser pinch-zoom here ensures our
        // 2-finger gesture is recognised as a canvas zoom, not a viewport
        // zoom. Single-finger pan still works for scrolling the desk.
        touchAction: 'pan-x pan-y',
      }}
      onMouseDown={(e) => { e.stopPropagation(); onFocusPage?.(); }}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      data-testid={`page-drop-zone-${pageIndex}`}
    >
      {dropHover && (
        <div className="absolute inset-0 z-50 border-2 border-dashed border-terracotta bg-terracotta/5 pointer-events-none" />
      )}
      {showFocusRing && (
        <div className="absolute -inset-1 z-40 ring-2 ring-terracotta/70 rounded-sm pointer-events-none" />
      )}
      {/* Pinch-zoom indicator — only visible when the user has zoomed.
          Tap to reset to fit-scale. Positioned over the page corner so it
          stays on-screen regardless of where the canvas is scrolled. */}
      {!forExport && Math.abs(userZoom - 1) > 0.01 && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setUserZoom(1); }}
          data-testid={`zoom-reset-${pageIndex}`}
          title="Reset zoom to fit"
          className="absolute z-40 top-2 right-2 px-2.5 py-1 bg-ink/85 text-paper text-[10px] tracking-wider uppercase rounded-sm shadow-lg hover:bg-ink transition-colors"
        >
          {Math.round(userZoom * 100)}% · reset
        </button>
      )}
      <div
        ref={wrapperRef}
        className="book-page"
        style={{
          width: pageSize.width,
          height: pageSize.height,
          transform: `scale(${scale})`,
          transformOrigin: 'top left',
          position: 'absolute',
          top: 0,
          left: 0,
          background: '#FFFFFF',
        }}
        data-testid={`page-canvas-${pageIndex}`}
      >
        {/* Colored inner area inset by margin (0 when full_bleed) */}
        <div
          aria-hidden
          data-testid={`page-color-fill-${pageIndex}`}
          style={{
            position: 'absolute',
            top: margin,
            left: margin,
            width: innerW,
            height: innerH,
            background: bg,
            pointerEvents: 'none',
          }}
        />
        {page.blocks.map((b) => (
          <CanvasBlock
            key={b.id}
            block={b}
            selected={!forExport && selectedBlockId === b.id}
            editingTextId={forExport ? null : editingTextId}
            onSelect={onSelectBlock}
            onChange={onChangeBlock}
            onStartTextEdit={onStartTextEdit}
            onStopTextEdit={onStopTextEdit}
            onTocJump={onTocJump}
            scale={scale}
            pageWidth={pageSize.width}
            pageHeight={pageSize.height}
          />
        ))}
        {showPageNumber && (
          <div
            className="absolute"
            style={{
              // Place inside the colored area, 16px above its bottom edge.
              bottom: margin + 16,
              left: pageNumAlign === 'left' ? margin + 16 : undefined,
              right: pageNumAlign === 'right' ? margin + 16 : undefined,
              ...(pageNumAlign === 'center'
                ? {
                    left: 0,
                    right: 0,
                    textAlign: 'center',
                  }
                : {}),
              fontFamily: pageNumFont,
              fontSize: pageNumSize,
              letterSpacing: '0.05em',
              color: pageNumColor,
            }}
            data-testid={`page-number-${pageIndex}`}
          >
            {displayedNumber}
          </div>
        )}
      </div>
    </div>
  );
}

function SpreadPlaceholder({ pageSize }) {
  // Empty slot for cover/last-page spread positioning.
  const aspect = pageSize.height / pageSize.width;
  return (
    <div
      style={{ width: 200, height: 200 * aspect, opacity: 0.4 }}
      className="border border-dashed border-rule rounded-sm flex items-center justify-center text-ink-mute font-serif italic text-sm"
      data-testid="spread-placeholder"
    >
      —
    </div>
  );
}

function PageThumbnail({ page, index, active, pageSize, totalPages = 1, pageNumberStart = 1, onClick, onDuplicate, onDelete, onTogglePageNumber, onMoveUp, onMoveDown, assetCacheBuster = 0 }) {
  const thumbW = 160;
  const scale = thumbW / pageSize.width;
  const thumbH = pageSize.height * scale;
  const isBackCover = totalPages > 1 && index === totalPages - 1;
  const oneBasedIdx = index + 1;
  const showPageNumber = !!page.show_page_number && !isBackCover && oneBasedIdx >= pageNumberStart;
  const displayedNumber = oneBasedIdx - pageNumberStart + 1;
  return (
    <div
      className={`group relative rounded-sm border ${active ? 'border-terracotta' : 'border-rule-dark'} bg-rule-dark/50 overflow-hidden cursor-pointer`}
      onClick={onClick}
      data-testid={`page-thumbnail-${index}`}
    >
      <div
        style={{
          width: thumbW,
          height: thumbH,
          position: 'relative',
          background: '#FFFFFF',
        }}
      >
        <div
          aria-hidden
          style={{
            position: 'absolute',
            top: PAGE_MARGIN_PX * scale,
            left: PAGE_MARGIN_PX * scale,
            width: (pageSize.width - PAGE_MARGIN_PX * 2) * scale,
            height: (pageSize.height - PAGE_MARGIN_PX * 2) * scale,
            background: page.background_color || '#F9F6F0',
          }}
        />
        <div
          style={{
            width: pageSize.width,
            height: pageSize.height,
            transform: `scale(${scale})`,
            transformOrigin: 'top left',
            position: 'absolute',
            top: 0,
            left: 0,
            pointerEvents: 'none',
          }}
        >
          {page.blocks.map((b) => {
            const isText = b.type === 'text';
            return (
            <div
              key={b.id}
              style={{
                position: 'absolute',
                left: b.x,
                top: b.y,
                width: b.width,
                // Text blocks use min-height so long content isn't clipped
                // when the user typed more than the block was sized for.
                ...(isText ? { minHeight: b.height } : { height: b.height }),
                // Text overflow is visible for PDF export so descenders and
                // wrapped lines are never sliced off; non-text keeps clipping.
                overflow: isText ? 'visible' : 'hidden',
              }}
            >
              {isText ? (
                <div
                  style={{
                    fontFamily: b.font_family,
                    fontSize: b.font_size,
                    textAlign: b.text_align,
                    color: b.color,
                    lineHeight: 1.4,
                  }}
                  dangerouslySetInnerHTML={{ __html: sanitizeHtml(b.html || '') }}
                />
              ) : b.image_url ? (
                <img alt="" src={(() => {
                  const base = b.image_url.startsWith('http') ? b.image_url : `${process.env.REACT_APP_BACKEND_URL}${b.image_url}`;
                  return assetCacheBuster
                    ? `${base}${base.includes('?') ? '&' : '?'}v=${assetCacheBuster}`
                    : base;
                })()} style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
              ) : null}
            </div>
            );
          })}
          {showPageNumber && (
            <div
              className="absolute"
              style={{
                fontFamily: page.page_number_font || 'Cormorant Garamond',
                fontSize: page.page_number_size || 14,
                color: isDarkHex(page.background_color || '#FFF8DC') ? '#E8E2D4' : '#3A3833',
                bottom: (page.full_bleed ? 0 : PAGE_MARGIN_PX) + 16,
                left: (page.page_number_align || 'right') === 'left' ? (page.full_bleed ? 0 : PAGE_MARGIN_PX) + 16 : undefined,
                right: (page.page_number_align || 'right') === 'right' ? (page.full_bleed ? 0 : PAGE_MARGIN_PX) + 16 : undefined,
                ...((page.page_number_align || 'right') === 'center'
                  ? { left: 0, right: 0, textAlign: 'center' }
                  : {}),
              }}
            >
              {displayedNumber}
            </div>
          )}
        </div>
      </div>
      <div className="absolute top-1 left-1 text-[10px] font-sans px-1.5 py-0.5 bg-ink/80 text-paper rounded-sm">
        {index + 1}
      </div>
      <div className="absolute bottom-1 right-1 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        {onMoveUp && (
          <button
            onClick={(e) => { e.stopPropagation(); onMoveUp(); }}
            className="p-1 bg-ink/80 hover:bg-ink text-paper rounded-sm"
            title="Move page up"
            data-testid={`move-page-up-${index}`}
          >
            <ChevronUp className="w-3 h-3" />
          </button>
        )}
        {onMoveDown && (
          <button
            onClick={(e) => { e.stopPropagation(); onMoveDown(); }}
            className="p-1 bg-ink/80 hover:bg-ink text-paper rounded-sm"
            title="Move page down"
            data-testid={`move-page-down-${index}`}
          >
            <ChevronDown className="w-3 h-3" />
          </button>
        )}
        <button
          onClick={(e) => { e.stopPropagation(); onTogglePageNumber(); }}
          className="p-1 bg-ink/80 hover:bg-ink text-paper rounded-sm"
          title={page.show_page_number ? 'Hide page number' : 'Show page number'}
          data-testid={`toggle-page-number-${index}`}
        >
          {page.show_page_number ? <Eye className="w-3 h-3" /> : <EyeOff className="w-3 h-3" />}
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onDuplicate(); }}
          className="p-1 bg-ink/80 hover:bg-ink text-paper rounded-sm"
          title="Duplicate"
          data-testid={`duplicate-page-${index}`}
        >
          <Copy className="w-3 h-3" />
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          className="p-1 bg-ink/80 hover:bg-terracotta text-paper rounded-sm"
          title="Delete page"
          data-testid={`delete-page-${index}`}
        >
          <Trash2 className="w-3 h-3" />
        </button>
      </div>
    </div>
  );
}
