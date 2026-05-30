import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Upload, Trash2, Loader2, ImageIcon, Search, Replace, BookOpen, Plus, AlertTriangle, RefreshCw, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { listAssets, uploadImage, deleteAsset, replaceAsset, fileUrl } from '@/lib/api';
import { Input } from '@/components/ui/input';
import {
  ContextMenu,
  ContextMenuTrigger,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
} from '@/components/ui/context-menu';
import GenerateImageDialog from './GenerateImageDialog';

export const ASSET_DRAG_MIME = 'application/x-bindery-asset';

export default function AssetsPanel({
  bookId,
  onAssetUploaded,
  onAssetReplaced,
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
  // Tracks which assets failed to load images (orphaned bytes in storage).
  // Keyed by asset.id; we don't persist this — it's recomputed on each panel
  // open from <img onError>, which is robust to any failure mode (404, 500,
  // network blocked) without needing a separate API ping.
  const [brokenIds, setBrokenIds] = useState(() => new Set());
  // Bumped after a replace so the <img> re-fetches instead of serving the
  // browser's cached failure.
  const [versionTags, setVersionTags] = useState(() => ({}));
  const [generateOpen, setGenerateOpen] = useState(false);
  const fileInputRef = useRef(null);
  const fixAllInputRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const data = await listAssets(bookId);
      setAssets(data);
      // Drop stale broken markers when the underlying list changes.
      setBrokenIds(new Set());
      setVersionTags({});
    } catch (e) {
      toast.error('Could not load assets');
    } finally {
      setLoading(false);
    }
  }, [bookId]);

  useEffect(() => { refresh(); }, [refresh]);

  const markBroken = useCallback((id) => {
    setBrokenIds((prev) => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  }, []);

  const markFixed = useCallback((id) => {
    setBrokenIds((prev) => {
      if (!prev.has(id)) return prev;
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  const handleReplaceAsset = useCallback(async (id, file) => {
    if (!file) return;
    try {
      await replaceAsset(id, file);
      // Cache-bust the <img> so the freshly-uploaded bytes appear.
      setVersionTags((v) => ({ ...v, [id]: Date.now() }));
      markFixed(id);
      // Tell the editor so canvas <img> elements re-fetch too.
      onAssetReplaced?.();
      toast.success(`Replaced "${file.name}"`);
    } catch (e) {
      toast.error(`Could not replace asset: ${e?.response?.data?.detail || e.message}`);
    }
  }, [markFixed, onAssetReplaced]);

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

  // Bulk-fix flow: user picks N files, we match each one to a BROKEN asset by
  // filename (case-insensitive, with a fallback to the basename without
  // extension). Unmatched files get appended as new uploads so nothing is
  // silently lost.
  const handleFixAllFiles = async (fileList) => {
    const files = Array.from(fileList || []).filter((f) => f.type.startsWith('image/'));
    if (!files.length) return;
    const brokenAssets = assets.filter((a) => brokenIds.has(a.id));
    if (brokenAssets.length === 0) {
      toast.info('No broken assets to replace.');
      return;
    }
    setUploading(true);
    const stem = (s) => (s || '').toLowerCase().split('.').slice(0, -1).join('.') || (s || '').toLowerCase();
    const remaining = [...brokenAssets];
    let replaced = 0;
    let appended = 0;
    let unmatched = 0;
    for (const f of files) {
      // Try exact filename match first, then basename-without-extension.
      let idx = remaining.findIndex((a) => (a.original_filename || '').toLowerCase() === f.name.toLowerCase());
      if (idx < 0) {
        idx = remaining.findIndex((a) => stem(a.original_filename) === stem(f.name));
      }
      if (idx >= 0) {
        const asset = remaining.splice(idx, 1)[0];
        try {
          // eslint-disable-next-line no-await-in-loop
          await replaceAsset(asset.id, f);
          setVersionTags((v) => ({ ...v, [asset.id]: Date.now() }));
          markFixed(asset.id);
          replaced += 1;
        } catch (e) {
          unmatched += 1;
        }
      } else {
        // No matching broken slot — upload as a new asset so the user
        // still ends up with the bytes in storage.
        try {
          // eslint-disable-next-line no-await-in-loop
          const uploaded = await uploadImage(f, bookId);
          onAssetUploaded?.(uploaded);
          appended += 1;
        } catch (e) {
          unmatched += 1;
        }
      }
    }
    setUploading(false);
    const parts = [];
    if (replaced) parts.push(`${replaced} replaced`);
    if (appended) parts.push(`${appended} added as new`);
    if (unmatched) parts.push(`${unmatched} failed`);
    toast.success(parts.join(' · ') || 'Done');
    if (replaced) onAssetReplaced?.();
    if (appended) await refresh();
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

  const filtered = useMemo(
    () => assets.filter((a) =>
      !search.trim() || (a.original_filename || '').toLowerCase().includes(search.toLowerCase())
    ),
    [assets, search],
  );
  const brokenCount = brokenIds.size;

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
        <button
          type="button"
          onClick={() => setGenerateOpen(true)}
          disabled={uploading}
          data-testid="generate-image-button"
          title="Generate an illustration from a text prompt"
          className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-rule-dark/40 hover:bg-rule-dark/70 border border-rule-dark text-paper rounded-sm text-xs transition-colors disabled:opacity-60"
        >
          <Sparkles className="w-3.5 h-3.5 text-terracotta" />
          Generate with AI…
        </button>
        {brokenCount > 0 && (
          <button
            type="button"
            onClick={() => fixAllInputRef.current?.click()}
            disabled={uploading}
            data-testid="fix-missing-button"
            title="Re-upload images to replace orphaned assets. We match by filename when possible; un-matched files are appended as new."
            className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-amber-700/30 hover:bg-amber-700/50 border border-amber-600/60 text-amber-200 rounded-sm text-xs transition-colors disabled:opacity-60"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Fix {brokenCount} missing image{brokenCount > 1 ? 's' : ''}…
          </button>
        )}
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
        <input
          ref={fixAllInputRef}
          type="file"
          multiple
          accept="image/*"
          className="hidden"
          data-testid="fix-missing-input"
          onChange={(e) => {
            handleFixAllFiles(e.target.files);
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
                version={versionTags[a.id]}
                isBroken={brokenIds.has(a.id)}
                onMarkBroken={() => markBroken(a.id)}
                onReplaceBytes={(file) => handleReplaceAsset(a.id, file)}
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

      <GenerateImageDialog
        open={generateOpen}
        onClose={() => setGenerateOpen(false)}
        bookId={bookId}
        onGenerated={(asset) => {
          onAssetUploaded?.(asset);
          refresh();
        }}
      />
    </div>
  );
}

function AssetTile({ asset, version, isBroken, onMarkBroken, onReplaceBytes, onRemove, onInsert, onReplace, onCoverBackdrop, canReplace = false }) {
  // `version` is bumped after a successful replace so the <img> reloads
  // instead of serving the browser's cached failure response.
  const baseUrl = fileUrl(asset.url);
  const url = version ? `${baseUrl}${baseUrl.includes('?') ? '&' : '?'}v=${version}` : baseUrl;
  const [hovered, setHovered] = useState(false);
  const [previewTop, setPreviewTop] = useState(0);
  const tileRef = useRef(null);
  const replaceInputRef = useRef(null);
  const handleDragStart = (e) => {
    // Don't allow dragging a broken asset onto the canvas — it'd just be
    // a placeholder. The user needs to fix it first.
    if (isBroken) { e.preventDefault(); return; }
    e.dataTransfer.effectAllowed = 'copy';
    const payload = JSON.stringify({ url: asset.url, path: asset.path });
    e.dataTransfer.setData(ASSET_DRAG_MIME, payload);
    e.dataTransfer.setData('text/plain', payload);
    // Suppress the preview while the user is actively dragging.
    setHovered(false);
  };
  const showPreview = () => {
    if (isBroken) return;  // No useful preview for a broken tile.
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
  const tileClassName = `group relative aspect-square border rounded-sm overflow-hidden ${
    isBroken
      ? 'bg-amber-950/30 border-amber-700/60 cursor-not-allowed'
      : 'bg-rule-dark/40 border-rule-dark cursor-grab active:cursor-grabbing'
  }`;
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <div
          ref={tileRef}
          className={tileClassName}
          draggable={!isBroken}
          onDragStart={handleDragStart}
          onMouseEnter={showPreview}
          onMouseLeave={() => setHovered(false)}
          data-testid={`asset-tile-${asset.id}`}
          data-broken={isBroken ? 'true' : 'false'}
          title={isBroken ? `${asset.original_filename || 'image'} — missing from storage` : (asset.original_filename || 'image')}
        >
          {!isBroken && (
            <img
              src={url}
              alt={asset.original_filename || ''}
              className="absolute inset-0 w-full h-full object-contain pointer-events-none p-1"
              crossOrigin="anonymous"
              draggable={false}
              onError={() => onMarkBroken?.()}
            />
          )}
          {isBroken && (
            <div
              className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 p-2 text-amber-200"
              data-testid={`asset-broken-${asset.id}`}
            >
              <AlertTriangle className="w-5 h-5 text-amber-400" />
              <p className="text-[10px] text-center leading-tight font-medium">Missing</p>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); replaceInputRef.current?.click(); }}
                data-testid={`asset-replace-bytes-${asset.id}`}
                className="text-[10px] px-2 py-0.5 bg-amber-600/40 hover:bg-amber-600/60 border border-amber-500/60 rounded-sm transition-colors"
              >
                Re-upload
              </button>
              <input
                ref={replaceInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                data-testid={`asset-replace-bytes-input-${asset.id}`}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) onReplaceBytes?.(f);
                  e.target.value = '';
                }}
              />
              {asset.original_filename && (
                <p className="text-[9px] text-amber-300/70 truncate w-full text-center" title={asset.original_filename}>
                  {asset.original_filename}
                </p>
              )}
            </div>
          )}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onRemove(); }}
            data-testid={`asset-delete-${asset.id}`}
            className="absolute top-1 right-1 p-1 bg-ink/80 hover:bg-terracotta text-paper rounded-sm opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <Trash2 className="w-3 h-3" />
          </button>
          {hovered && !isBroken && (
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
        {isBroken && (
          <>
            <ContextMenuItem
              onClick={() => { setHovered(false); replaceInputRef.current?.click(); }}
              data-testid={`asset-action-reupload-${asset.id}`}
              className="cursor-pointer"
            >
              <RefreshCw className="w-4 h-4 mr-2" /> Re-upload bytes
            </ContextMenuItem>
            <ContextMenuSeparator />
          </>
        )}
        <ContextMenuItem
          onClick={() => { setHovered(false); onInsert?.(assetPayload); }}
          disabled={!onInsert || isBroken}
          data-testid={`asset-action-insert-${asset.id}`}
          className="cursor-pointer"
        >
          <Plus className="w-4 h-4 mr-2" /> Insert on this page
        </ContextMenuItem>
        <ContextMenuItem
          onClick={() => { setHovered(false); onReplace?.(assetPayload); }}
          disabled={!canReplace || !onReplace || isBroken}
          data-testid={`asset-action-replace-${asset.id}`}
          className="cursor-pointer"
        >
          <Replace className="w-4 h-4 mr-2" /> Replace selected image
        </ContextMenuItem>
        <ContextMenuItem
          onClick={() => { setHovered(false); onCoverBackdrop?.(assetPayload); }}
          disabled={!onCoverBackdrop || isBroken}
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
