import { useCallback, useEffect, useRef, useState } from 'react';
import { Upload, Trash2, Loader2, ImageIcon, Search } from 'lucide-react';
import { toast } from 'sonner';
import { listAssets, uploadImage, deleteAsset, fileUrl } from '@/lib/api';
import { Input } from '@/components/ui/input';

export const ASSET_DRAG_MIME = 'application/x-bindery-asset';

export default function AssetsPanel({ bookId, onAssetUploaded }) {
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
              <AssetTile key={a.id} asset={a} onRemove={() => onRemove(a.id)} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function AssetTile({ asset, onRemove }) {
  const url = fileUrl(asset.url);
  const handleDragStart = (e) => {
    e.dataTransfer.effectAllowed = 'copy';
    const payload = JSON.stringify({ url: asset.url, path: asset.path });
    e.dataTransfer.setData(ASSET_DRAG_MIME, payload);
    e.dataTransfer.setData('text/plain', payload);
  };
  return (
    <div
      className="group relative aspect-square bg-rule-dark/40 border border-rule-dark rounded-sm overflow-hidden cursor-grab active:cursor-grabbing"
      draggable
      onDragStart={handleDragStart}
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
    </div>
  );
}
