import { useCallback, useEffect, useRef, useState } from 'react';
import { Upload, Trash2, Loader2, ImageIcon, Search, Replace, BookOpen, Plus } from 'lucide-react';
import { toast } from 'sonner';
import { listAssets, uploadImage, deleteAsset, fileUrl } from '@/lib/api';
import { Input } from '@/components/ui/input';
import {
  ContextMenu,
  ContextMenuTrigger,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
} from '@/components/ui/context-menu';

export const ASSET_DRAG_MIME = 'application/x-bindery-asset';

export default function AssetsPanel({
  bookId,
  onAssetUploaded,
  onInsertAsset,
  onReplaceSelectedImage,
  onSetAsCoverBackdrop,
  canReplaceSelected = false,
}) {
  const [assets, setAssets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [search, setSearch] = useState('');
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const data = await listAssets(bookId);
      setAssets(data);
    } catch (e) {
      toast.error('Could not load assets');
    } finally {
      setLoading(false);
    }
  }, [bookId]);

  useEffect(() => { refresh(); }, [refresh]);

  const handleFiles = async (fileList) => {
    const files = Array.from(fileList || []).filter((f) => f.type.startsWith('image/'));
    if (!files.length) return;
    setUploading(true);
    let success = 0;
    for (const file of files) {
      try {
        // eslint-disable-next-line no-await-in-loop
        const uploaded = await uploadImage(file, bookId);
        success += 1;
        onAssetUploaded?.(uploaded);
      } catch (e) {
        toast.error(`Failed to upload ${file.name}`);
      }
    }
    setUploading(false);
    if (success) toast.success(`${success} image${success > 1 ? 's' : ''} uploaded`);
    await refresh();
  };

  const onRemove = async (id) => {
    try {
      await deleteAsset(id);
      setAssets((a) => a.filter((x) => x.id !== id));
      toast.success('Asset removed');
    } catch (e) {
      toast.error('Could not remove asset');
    }
  };

  const filtered = assets.filter((a) =>
    !search.trim() || (a.original_filename || '').toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div
      className="flex flex-col h-full bg-ink text-paper"
      data-testid="assets-panel"
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        // Only handle file drops here (not internal asset drags).
        if (e.dataTransfer.files?.length) {
          e.preventDefault();
          setDragging(false);
          handleFiles(e.dataTransfer.files);
        }
      }}
    >
      <div className="px-4 py-3 border-b border-rule-dark flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ImageIcon className="w-4 h-4 text-ink-mute" />
          <p className="label-caps text-ink-mute">Assets</p>
        </div>
        <span className="text-xs text-ink-mute">{assets.length}</span>
      </div>

      <div className="px-3 pt-3 pb-2 space-y-2">
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
          data-testid="bulk-upload-button"
          className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm text-sm font-medium transition-colors disabled:opacity-60"
        >
          {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
          {uploading ? 'Uploading…' : 'Upload images'}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*"
          className="hidden"
          data-testid="bulk-upload-input"
          onChange={(e) => {
            handleFiles(e.target.files);
            e.target.value = '';
          }}
        />
        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-ink-mute" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search…"
            data-testid="assets-search-input"
            className="pl-7 h-8 bg-rule-dark/40 border-rule-dark text-paper placeholder:text-ink-mute focus-visible:ring-terracotta rounded-sm text-xs"
          />
        </div>
      </div>

      <div
        className={`flex-1 overflow-y-auto sidebar-scroll px-3 pb-4 ${dragging ? 'ring-2 ring-terracotta ring-inset' : ''}`}
        data-testid="assets-grid-wrapper"
      >
        {loading ? (
          <p className="text-ink-mute font-serif italic text-sm py-4">Loading…</p>
        ) : filtered.length === 0 ? (
          <div className="border border-dashed border-rule-dark rounded-sm py-10 px-3 text-center">
            <ImageIcon className="w-8 h-8 mx-auto text-ink-mute mb-2" strokeWidth={1.25} />
            <p className="text-xs text-ink-mute leading-relaxed">
              {assets.length === 0 ? 'Drop images here or click Upload to begin.' : 'No matches.'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2" data-testid="assets-grid">
            {filtered.map((a) => (
              <AssetTile
                key={a.id}
                asset={a}
                onRemove={() => onRemove(a.id)}
                onInsert={onInsertAsset}
                onReplace={onReplaceSelectedImage}
                onCoverBackdrop={onSetAsCoverBackdrop}
                canReplace={canReplaceSelected}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function AssetTile({ asset, onRemove, onInsert, onReplace, onCoverBackdrop, canReplace = false }) {
  const url = fileUrl(asset.url);
  const [hovered, setHovered] = useState(false);
  const [previewTop, setPreviewTop] = useState(0);
  const tileRef = useRef(null);
  const handleDragStart = (e) => {
    e.dataTransfer.effectAllowed = 'copy';
    const payload = JSON.stringify({ url: asset.url, path: asset.path });
    e.dataTransfer.setData(ASSET_DRAG_MIME, payload);
    e.dataTransfer.setData('text/plain', payload);
    // Suppress the preview while the user is actively dragging.
    setHovered(false);
  };
  const showPreview = () => {
    if (!tileRef.current) return;
    const r = tileRef.current.getBoundingClientRect();
    // Vertically center the preview on the thumbnail, but clamp to the viewport.
    const previewSize = 640;
    const idealTop = r.top + r.height / 2 - previewSize / 2;
    const clamped = Math.max(16, Math.min(window.innerHeight - previewSize - 16, idealTop));
    setPreviewTop(clamped);
    setHovered(true);
  };
  const assetPayload = { url: asset.url, path: asset.path };
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <div
          ref={tileRef}
          className="group relative aspect-square bg-rule-dark/40 border border-rule-dark rounded-sm overflow-hidden cursor-grab active:cursor-grabbing"
          draggable
          onDragStart={handleDragStart}
          onMouseEnter={showPreview}
          onMouseLeave={() => setHovered(false)}
          data-testid={`asset-tile-${asset.id}`}
          title={asset.original_filename || 'image'}
        >
          <img
            src={url}
            alt={asset.original_filename || ''}
            className="absolute inset-0 w-full h-full object-contain pointer-events-none p-1"
            crossOrigin="anonymous"
            draggable={false}
          />
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onRemove(); }}
            data-testid={`asset-delete-${asset.id}`}
            className="absolute top-1 right-1 p-1 bg-ink/80 hover:bg-terracotta text-paper rounded-sm opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <Trash2 className="w-3 h-3" />
          </button>
          {hovered && (
            <div
              // Floats to the left of the right-side Assets panel (which is 288px wide).
              // Pointer-events disabled so the popup never interferes with dragging.
              className="hidden lg:block fixed z-50 pointer-events-none animate-in fade-in zoom-in-95 duration-150"
              style={{ right: 296, top: previewTop }}
              data-testid={`asset-preview-${asset.id}`}
            >
              <div className="bg-paper border border-rule rounded-sm shadow-2xl p-2" style={{ width: 640 }}>
                <div className="bg-desk rounded-sm overflow-hidden" style={{ height: 560 }}>
                  <img
                    src={url}
                    alt={asset.original_filename || ''}
                    className="w-full h-full object-contain"
                    crossOrigin="anonymous"
                    draggable={false}
                  />
                </div>
                {asset.original_filename && (
                  <p className="text-[11px] text-ink-soft truncate pt-1.5 px-0.5" title={asset.original_filename}>
                    {asset.original_filename}
                  </p>
                )}
              </div>
            </div>
          )}
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent
        className="bg-paper border-rule rounded-sm w-56"
        data-testid={`asset-context-${asset.id}`}
      >
        <ContextMenuItem
          onClick={() => { setHovered(false); onInsert?.(assetPayload); }}
          disabled={!onInsert}
          data-testid={`asset-action-insert-${asset.id}`}
          className="cursor-pointer"
        >
          <Plus className="w-4 h-4 mr-2" /> Insert on this page
        </ContextMenuItem>
        <ContextMenuItem
          onClick={() => { setHovered(false); onReplace?.(assetPayload); }}
          disabled={!canReplace || !onReplace}
          data-testid={`asset-action-replace-${asset.id}`}
          className="cursor-pointer"
        >
          <Replace className="w-4 h-4 mr-2" /> Replace selected image
        </ContextMenuItem>
        <ContextMenuItem
          onClick={() => { setHovered(false); onCoverBackdrop?.(assetPayload); }}
          disabled={!onCoverBackdrop}
          data-testid={`asset-action-cover-${asset.id}`}
          className="cursor-pointer"
        >
          <BookOpen className="w-4 h-4 mr-2" /> Set as cover backdrop
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          onClick={() => { setHovered(false); onRemove(); }}
          data-testid={`asset-action-delete-${asset.id}`}
          className="cursor-pointer text-terracotta focus:text-terracotta"
        >
          <Trash2 className="w-4 h-4 mr-2" /> Remove from library
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  );
}
