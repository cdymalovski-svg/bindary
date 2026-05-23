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
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { getBook, updateBook, uploadImage } from '@/lib/api';
import { PAGE_SIZES, getPageSize } from '@/lib/pageSizes';
import { exportBookToPdf } from '@/lib/pdfExport';
import CanvasBlock from '@/components/CanvasBlock';
import BlockProperties from '@/components/BlockProperties';
import AssetsPanel, { ASSET_DRAG_MIME } from '@/components/AssetsPanel';

const uid = () => Math.random().toString(36).slice(2) + Date.now().toString(36);

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
  const [rightTab, setRightTab] = useState('assets'); // 'assets' | 'block'
  const fileInputRef = useRef(null);
  const exportContainerRef = useRef(null);

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
    else setRightTab('assets');
  }, [selectedBlockId]); // eslint-disable-line react-hooks/exhaustive-deps

  // --- Save ---
  const saveBook = useCallback(async (showToast = true) => {
    if (!book) return;
    setSaving(true);
    try {
      const updated = await updateBook(book.id, {
        title: book.title,
        author: book.author,
        page_size: book.page_size,
        pages: book.pages,
      });
      setBook(updated);
      if (showToast) toast.success('Saved');
    } catch (e) {
      toast.error('Save failed');
    } finally {
      setSaving(false);
    }
  }, [book]);

  // --- Mutators ---
  const updatePages = (updater) => {
    setBook((prev) => ({ ...prev, pages: updater(prev.pages) }));
  };

  const updateBlock = (blockId, patch) => {
    updatePages((pages) =>
      pages.map((p, i) =>
        i === activePageIndex
          ? { ...p, blocks: p.blocks.map((b) => (b.id === blockId ? { ...b, ...patch } : b)) }
          : p
      )
    );
  };

  const deleteBlock = (blockId) => {
    updatePages((pages) =>
      pages.map((p, i) =>
        i === activePageIndex ? { ...p, blocks: p.blocks.filter((b) => b.id !== blockId) } : p
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
      html: '<p>Once upon a time…</p>',
      font_family: 'Cormorant Garamond',
      font_size: 22,
      text_align: 'left',
      color: '#1C1B19',
    };
    updatePages((pages) =>
      pages.map((p, i) => (i === activePageIndex ? { ...p, blocks: [...p.blocks, block] } : p))
    );
    setSelectedBlockId(block.id);
  };

  const handleImageUpload = async (file) => {
    if (!file) return;
    try {
      toast.loading('Uploading…', { id: 'upload' });
      const res = await uploadImage(file);
      toast.dismiss('upload');
      addImageBlockFromAsset({ url: res.url, path: res.path });
      toast.success('Image added');
    } catch (e) {
      toast.dismiss('upload');
      toast.error('Upload failed');
    }
  };

  // Add an image block from an asset (dragged in or just uploaded).
  // If x/y not provided, places near top-left and centers.
  const addImageBlockFromAsset = (asset, position = null) => {
    const maxW = Math.min(500, pageSize.width - 80);
    const width = maxW;
    const height = maxW * 0.66;
    let x;
    let y;
    if (position) {
      x = Math.max(0, Math.min(pageSize.width - width, position.x - width / 2));
      y = Math.max(0, Math.min(pageSize.height - height, position.y - height / 2));
    } else {
      x = 60;
      y = 60;
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
      pages.map((p, i) => (i === activePageIndex ? { ...p, blocks: [...p.blocks, block] } : p))
    );
    setSelectedBlockId(block.id);
  };

  const changeZ = (delta) => {
    if (!selectedBlock) return;
    updateBlock(selectedBlock.id, { z_index: Math.max(1, (selectedBlock.z_index || 1) + delta) });
  };

  // --- Page actions ---
  const addPage = () => {
    updatePages((pages) => [...pages, { id: uid(), blocks: [], background_color: '#F9F6F0', show_page_number: true }]);
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

        <div className="flex-1" />

        <Button onClick={addTextBlock} className="bg-ink hover:bg-ink-soft text-paper rounded-sm h-8" data-testid="add-text-button">
          <TypeIcon className="w-4 h-4 mr-1" /> Text
        </Button>
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
          <div className="min-h-full flex items-center justify-center p-12">
            <PageCanvas
              page={activePage}
              pageIndex={activePageIndex}
              pageSize={pageSize}
              selectedBlockId={selectedBlockId}
              editingTextId={editingTextId}
              onSelectBlock={(blockId) => setSelectedBlockId(blockId)}
              onChangeBlock={updateBlock}
              onStartTextEdit={(blockId) => { setSelectedBlockId(blockId); setEditingTextId(blockId); }}
              onStopTextEdit={() => setEditingTextId(null)}
              onAssetDrop={(asset, pos) => addImageBlockFromAsset(asset, pos)}
            />
          </div>
        </main>

        {/* Right sidebar: Assets / Block tabs */}
        <aside className="w-72 bg-ink text-paper border-l border-rule-dark flex flex-col" data-testid="right-sidebar">
          <Tabs value={rightTab} onValueChange={setRightTab} className="flex flex-col h-full">
            <TabsList className="w-full grid grid-cols-2 rounded-none bg-rule-dark/40 border-b border-rule-dark h-10 p-1">
              <TabsTrigger
                value="assets"
                data-testid="right-tab-assets"
                className="rounded-sm data-[state=active]:bg-ink data-[state=active]:text-paper text-ink-mute text-xs tracking-[0.18em] uppercase"
              >
                <ImageIcon className="w-3.5 h-3.5 mr-1.5" /> Assets
              </TabsTrigger>
              <TabsTrigger
                value="block"
                disabled={!selectedBlock}
                data-testid="right-tab-block"
                className="rounded-sm data-[state=active]:bg-ink data-[state=active]:text-paper text-ink-mute text-xs tracking-[0.18em] uppercase disabled:opacity-40"
              >
                <SlidersHorizontal className="w-3.5 h-3.5 mr-1.5" /> Block
              </TabsTrigger>
            </TabsList>
            <TabsContent value="assets" className="flex-1 m-0 overflow-hidden">
              <AssetsPanel
                onAssetUploaded={() => { /* refresh handled inside */ }}
              />
            </TabsContent>
            <TabsContent value="block" className="flex-1 m-0 overflow-y-auto sidebar-scroll bg-paper text-ink">
              {selectedBlock ? (
                <BlockProperties
                  block={selectedBlock}
                  onChange={(patch) => updateBlock(selectedBlock.id, patch)}
                  onDelete={() => deleteBlock(selectedBlock.id)}
                  onTextCommand={onTextCommand}
                  onZ={changeZ}
                />
              ) : (
                <div className="p-6 text-ink-mute text-sm font-serif italic">Select a block to edit its properties.</div>
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
  forExport = false,
}) {
  // Scale to fit viewport for editing mode (export uses full size)
  const [scale, setScale] = useState(1);
  const [dropHover, setDropHover] = useState(false);
  const wrapperRef = useRef(null);

  useEffect(() => {
    if (forExport) { setScale(1); return; }
    const compute = () => {
      const padding = 96; // px padding around
      const availW = window.innerWidth - 224 /* left sidebar */ - 288 /* right sidebar */ - padding;
      const availH = window.innerHeight - 56 /* topbar */ - padding;
      const s = Math.min(1, availW / pageSize.width, availH / pageSize.height);
      setScale(Math.max(0.25, s));
    };
    compute();
    window.addEventListener('resize', compute);
    return () => window.removeEventListener('resize', compute);
  }, [pageSize.width, pageSize.height, forExport]);

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
    // Position relative to the scaled outer container, then un-scale.
    const rect = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - rect.left) / scale;
    const y = (e.clientY - rect.top) / scale;
    onAssetDrop(asset, { x, y });
  };

  return (
    <div
      style={{
        width: pageSize.width * scale,
        height: pageSize.height * scale,
        position: 'relative',
      }}
      onMouseDown={(e) => e.stopPropagation()}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      data-testid={`page-drop-zone-${pageIndex}`}
    >
      {dropHover && (
        <div className="absolute inset-0 z-50 border-2 border-dashed border-terracotta bg-terracotta/5 pointer-events-none" />
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
          background: page.background_color || '#F9F6F0',
        }}
        data-testid={`page-canvas-${pageIndex}`}
      >
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
        {page.show_page_number && (
          <div
            className="absolute bottom-6 right-8 font-serif text-ink-soft"
            style={{ fontSize: 14, letterSpacing: '0.05em' }}
            data-testid={`page-number-${pageIndex}`}
          >
            {pageIndex + 1}
          </div>
        )}
      </div>
    </div>
  );
}

function PageThumbnail({ page, index, active, pageSize, onClick, onDuplicate, onDelete, onTogglePageNumber }) {
  const thumbW = 160;
  const scale = thumbW / pageSize.width;
  const thumbH = pageSize.height * scale;
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
          background: page.background_color || '#F9F6F0',
        }}
      >
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
                <img alt="" src={(b.image_url.startsWith('http') ? b.image_url : `${process.env.REACT_APP_BACKEND_URL}${b.image_url}`)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
              ) : null}
            </div>
          ))}
          {page.show_page_number && (
            <div className="absolute bottom-6 right-8 font-serif text-ink-soft" style={{ fontSize: 14 }}>{index + 1}</div>
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
