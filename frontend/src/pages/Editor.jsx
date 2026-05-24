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
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { getBook, updateBook, uploadImage } from '@/lib/api';
import { PAGE_SIZES, getPageSize, PAGE_MARGIN_PX } from '@/lib/pageSizes';
import { exportBookToPdf } from '@/lib/pdfExport';
import CanvasBlock from '@/components/CanvasBlock';
import BlockProperties from '@/components/BlockProperties';
import AssetsPanel, { ASSET_DRAG_MIME } from '@/components/AssetsPanel';
import PagePanel from '@/components/PagePanel';
import LayersList from '@/components/LayersList';
import SaveTemplateDialog from '@/components/SaveTemplateDialog';

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
  const [lastSavedAt, setLastSavedAt] = useState(null);
  const fileInputRef = useRef(null);
  const exportContainerRef = useRef(null);
  const autoSaveTimerRef = useRef(null);
  const skipNextAutoSaveRef = useRef(true);

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
    try {
      await updateBook(book.id, {
        title: book.title,
        author: book.author,
        page_size: book.page_size,
        page_number_start: book.page_number_start || 1,
        is_chapter_book: !!book.is_chapter_book,
        pages: book.pages,
      });
      setLastSavedAt(new Date());
      if (showToast) toast.success('Saved');
    } catch (e) {
      toast.error('Save failed');
    } finally {
      setSaving(false);
    }
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

  const addTextBlock = () => {
    const block = {
      id: uid(),
      type: 'text',
      x: 60,
      y: 60,
      width: 360,
      height: 140,
      z_index: 1,
      html: '',
      font_family: 'Cormorant Garamond',
      font_size: 22,
      text_align: 'left',
      color: '#000000',
    };
    updatePages((pages) =>
      pages.map((p, i) => (i === activePageIndex ? { ...p, blocks: [...p.blocks, block] } : p))
    );
    setSelectedBlockId(block.id);
    setEditingTextId(block.id);
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
    probe.innerHTML = el.innerHTML;
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
      if (el) updateBlock(editingTextId, { html: el.innerHTML });
    }
  };

  // --- Keyboard shortcuts ---
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.isContentEditable) return;
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
  }, [selectedBlockId, saveBook]);

  // --- PDF export ---
  const onExportPdf = async () => {
    if (!book) return;
    setExporting(true);
    try {
      // First save
      await saveBook(false);
      // Wait a tick for offscreen render
      await new Promise((r) => setTimeout(r, 100));
      const nodes = Array.from(
        exportContainerRef.current?.querySelectorAll('[data-export-page]') || []
      );
      await exportBookToPdf(book, nodes);
      toast.success('PDF exported');
    } catch (e) {
      console.error(e);
      toast.error('Export failed');
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
          onSelectBlock={(blockId) => { setActivePageIndex(idx); setSelectedBlockId(blockId); }}
          onChangeBlock={(blockId, patch) => updateBlock(blockId, patch, idx)}
          onStartTextEdit={(blockId) => { setActivePageIndex(idx); setSelectedBlockId(blockId); setEditingTextId(blockId); }}
          onStopTextEdit={() => setEditingTextId(null)}
          onAssetDrop={(asset, pos) => addImageBlockFromAsset(asset, pos, idx)}
          onFocusPage={() => setActivePageIndex(idx)}
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
      {/* Top bar */}
      <header className="h-14 border-b border-rule bg-paper flex items-center px-4 gap-3 z-20">
        <Button
          variant="ghost"
          onClick={() => navigate('/')}
          className="rounded-sm text-ink hover:bg-desk px-2"
          data-testid="back-to-library"
        >
          <ArrowLeft className="w-4 h-4 mr-1" /> Library
        </Button>
        <div className="w-px h-6 bg-rule" />
        <Input
          value={book.title}
          onChange={(e) => setBook({ ...book, title: e.target.value })}
          className="bg-transparent border-0 font-serif text-xl text-ink focus-visible:ring-1 focus-visible:ring-terracotta rounded-sm w-72"
          data-testid="book-title-input"
        />
        <Input
          value={book.author || ''}
          onChange={(e) => setBook({ ...book, author: e.target.value })}
          placeholder="Author"
          className="bg-transparent border-0 text-ink-soft text-sm italic w-40 focus-visible:ring-1 focus-visible:ring-terracotta rounded-sm"
          data-testid="book-author-input"
        />
        <div className="w-px h-6 bg-rule" />
        <Select value={book.page_size} onValueChange={(v) => setBook({ ...book, page_size: v })}>
          <SelectTrigger className="bg-white border-rule rounded-sm h-8 w-36 text-sm" data-testid="page-size-trigger">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(PAGE_SIZES).map(([k, v]) => (
              <SelectItem key={k} value={k} data-testid={`pagesize-opt-${k}`}>{v.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <div className="w-px h-6 bg-rule" />
        <div className="flex items-center bg-white border border-rule rounded-sm h-8 p-0.5" data-testid="view-mode-toggle">
          <button
            type="button"
            onClick={() => setViewMode('single')}
            data-testid="view-mode-single"
            className={`h-7 px-2.5 flex items-center gap-1 rounded-sm text-xs tracking-wide transition-colors ${viewMode === 'single' ? 'bg-ink text-paper' : 'text-ink-soft hover:bg-desk'}`}
            title="Single page"
          >
            <Square className="w-3.5 h-3.5" /> Single
          </button>
          <button
            type="button"
            onClick={() => setViewMode('spread')}
            data-testid="view-mode-spread"
            className={`h-7 px-2.5 flex items-center gap-1 rounded-sm text-xs tracking-wide transition-colors ${viewMode === 'spread' ? 'bg-ink text-paper' : 'text-ink-soft hover:bg-desk'}`}
            title="Two-page spread"
          >
            <BookOpen className="w-3.5 h-3.5" /> Spread
          </button>
        </div>

        <div className="flex-1" />

        <Button onClick={addTextBlock} className="bg-ink hover:bg-ink-soft text-paper rounded-sm h-8" data-testid="add-text-button">
          <TypeIcon className="w-4 h-4 mr-1" /> Text
        </Button>
        {book.is_chapter_book && (
          <Button onClick={addChapterBlock} className="bg-ink hover:bg-ink-soft text-paper rounded-sm h-8" data-testid="add-chapter-button">
            <Heading className="w-4 h-4 mr-1" /> Chapter
          </Button>
        )}
        <Button
          onClick={() => fileInputRef.current?.click()}
          className="bg-ink hover:bg-ink-soft text-paper rounded-sm h-8"
          data-testid="add-image-button"
        >
          <ImageIcon className="w-4 h-4 mr-1" /> Image
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
        <div className="w-px h-6 bg-rule mx-1" />
        <SaveStatus saving={saving} lastSavedAt={lastSavedAt} />
        <SaveTemplateDialog book={book} />
        <Button
          onClick={() => saveBook()}
          variant="outline"
          className="rounded-sm h-8 border-rule"
          disabled={saving}
          data-testid="save-button"
        >
          {saving ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}
          Save
        </Button>
        <Button
          onClick={onExportPdf}
          disabled={exporting}
          className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm h-8"
          data-testid="export-pdf-button"
        >
          {exporting ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Download className="w-4 h-4 mr-1" />}
          Export PDF
        </Button>
      </header>

      <div className="flex-1 flex overflow-hidden">
        {/* Left sidebar: page thumbnails */}
        <aside className="w-56 bg-ink text-paper border-r border-rule-dark flex flex-col">
          <div className="px-4 py-3 border-b border-rule-dark flex items-center justify-between">
            <p className="label-caps text-ink-mute">Pages</p>
            <span className="text-xs text-ink-mute">{book.pages.length}</span>
          </div>
          <div className="flex-1 overflow-y-auto sidebar-scroll px-3 py-3 space-y-2" data-testid="pages-list">
            {book.pages.map((p, i) => (
              <PageThumbnail
                key={p.id}
                page={p}
                index={i}
                active={i === activePageIndex}
                pageSize={pageSize}
                totalPages={book.pages.length}
                pageNumberStart={book.page_number_start || 1}
                onClick={() => { setActivePageIndex(i); setSelectedBlockId(null); }}
                onDuplicate={() => duplicatePage(i)}
                onDelete={() => removePage(i)}
                onTogglePageNumber={() => togglePageNumber(i)}
              />
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
        <main className="flex-1 overflow-auto desk-scroll relative" onMouseDown={() => { setSelectedBlockId(null); setEditingTextId(null); }}>
          <div className="min-h-full flex items-center justify-center p-12 gap-6">
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
                onSelectBlock={(blockId) => setSelectedBlockId(blockId)}
                onChangeBlock={updateBlock}
                onStartTextEdit={(blockId) => { setSelectedBlockId(blockId); setEditingTextId(blockId); }}
                onStopTextEdit={() => setEditingTextId(null)}
                onAssetDrop={(asset, pos) => addImageBlockFromAsset(asset, pos)}
                onFocusPage={() => setActivePageIndex(activePageIndex)}
              />
            )}
          </div>
        </main>

        {/* Right sidebar: Assets / Page / Block tabs */}
        <aside className="w-72 bg-ink text-paper border-l border-rule-dark flex flex-col" data-testid="right-sidebar">
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
              <AssetsPanel bookId={book.id} onAssetUploaded={() => { /* refresh inside */ }} />
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
                />
              ) : (
                <div className="p-6 text-ink-mute text-sm font-serif italic">Select a block above to edit its properties.</div>
              )}
            </TabsContent>
          </Tabs>
        </aside>
      </div>

      {/* Offscreen export container - renders all pages at full size for PDF capture */}
      {exporting && (
        <div
          ref={exportContainerRef}
          style={{
            position: 'fixed',
            left: '-100000px',
            top: 0,
            pointerEvents: 'none',
          }}
        >
          {book.pages.map((p, i) => (
            <div key={p.id} data-export-page>
              <PageCanvas
                page={p}
                pageIndex={i}
                pageSize={pageSize}
                totalPages={book.pages.length}
                pageNumberStart={book.page_number_start || 1}
                selectedBlockId={null}
                editingTextId={null}
                onSelectBlock={() => {}}
                onChangeBlock={() => {}}
                onStartTextEdit={() => {}}
                onStopTextEdit={() => {}}
                forExport
              />
            </div>
          ))}
        </div>
      )}
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
  viewMode = 'single',
  isFocused = true,
  forExport = false,
  totalPages = 1,
  pageNumberStart = 1,
}) {  // Scale to fit viewport for editing mode (export uses full size)
  const [scale, setScale] = useState(1);
  const [dropHover, setDropHover] = useState(false);
  const wrapperRef = useRef(null);

  useEffect(() => {
    if (forExport) { setScale(1); return; }
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
      setScale(Math.max(0.18, s));
    };
    compute();
    window.addEventListener('resize', compute);
    return () => window.removeEventListener('resize', compute);
  }, [pageSize.width, pageSize.height, forExport, viewMode]);

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
      style={{
        width: pageSize.width * scale,
        height: pageSize.height * scale,
        position: 'relative',
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

function PageThumbnail({ page, index, active, pageSize, totalPages = 1, pageNumberStart = 1, onClick, onDuplicate, onDelete, onTogglePageNumber }) {
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
          {page.blocks.map((b) => (
            <div
              key={b.id}
              style={{
                position: 'absolute',
                left: b.x,
                top: b.y,
                width: b.width,
                height: b.height,
                overflow: 'hidden',
              }}
            >
              {b.type === 'text' ? (
                <div
                  style={{
                    fontFamily: b.font_family,
                    fontSize: b.font_size,
                    textAlign: b.text_align,
                    color: b.color,
                    lineHeight: 1.4,
                  }}
                  dangerouslySetInnerHTML={{ __html: b.html || '' }}
                />
              ) : b.image_url ? (
                <img alt="" src={(b.image_url.startsWith('http') ? b.image_url : `${process.env.REACT_APP_BACKEND_URL}${b.image_url}`)} style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
              ) : null}
            </div>
          ))}
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
