import { useEffect, useRef } from 'react';
import { Type as TypeIcon, Image as ImageIcon, Eye } from 'lucide-react';
import { fileUrl } from '@/lib/api';
import { sanitizeHtml } from '@/lib/sanitize';

// Photoshop-style stack list of all blocks on the active page,
// sorted top-of-list = highest z-index = visually on top.
export default function LayersList({ page, selectedBlockId, onSelect }) {
  const activeRowRef = useRef(null);

  // Scroll the active row into view whenever selection changes.
  useEffect(() => {
    if (activeRowRef.current) {
      activeRowRef.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [selectedBlockId]);

  const blocks = page?.blocks ? [...page.blocks] : [];
  blocks.sort((a, b) => {
    const za = typeof a.z_index === 'number' ? a.z_index : 1;
    const zb = typeof b.z_index === 'number' ? b.z_index : 1;
    return zb - za;
  });

  return (
    <div className="px-4 pt-4 pb-2 space-y-2 border-b border-rule" data-testid="layers-list">
      <div className="flex items-center justify-between">
        <p className="label-caps">Layers</p>
        <span className="text-[10px] text-ink-mute">{blocks.length}</span>
      </div>
      {blocks.length === 0 ? (
        <p className="text-[11px] text-ink-mute italic">No blocks on this page yet.</p>
      ) : (
        <div className="space-y-1 max-h-44 overflow-y-auto pr-1">
          {blocks.map((b) => {
            const active = b.id === selectedBlockId;
            const z = typeof b.z_index === 'number' ? b.z_index : 1;
            return (
              <button
                key={b.id}
                ref={active ? activeRowRef : null}
                type="button"
                onClick={() => onSelect(b.id)}
                data-testid={`layer-row-${b.id}`}
                className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-sm text-left text-xs transition-colors ${
                  active ? 'bg-terracotta text-paper' : 'bg-white hover:bg-desk text-ink border border-rule'
                }`}
              >
                <span className={`w-7 h-7 flex items-center justify-center rounded-sm overflow-hidden flex-shrink-0 ${active ? 'bg-paper/20' : 'bg-desk'}`}>
                  {b.type === 'image' && b.image_url ? (
                    <img
                      src={fileUrl(b.image_url)}
                      alt=""
                      className="w-full h-full object-contain"
                      draggable={false}
                    />
                  ) : b.type === 'image' ? (
                    <ImageIcon className="w-3.5 h-3.5" strokeWidth={1.5} />
                  ) : (
                    <TypeIcon className="w-3.5 h-3.5" strokeWidth={1.5} />
                  )}
                </span>
                <span className="flex-1 truncate">
                  {b.type === 'text' ? plainTextSnippet(b.html) || 'Text block' : 'Image'}
                </span>
                <span className={`text-[10px] font-mono ${active ? 'text-paper/80' : 'text-ink-mute'}`}>z {z}</span>
                {active && <Eye className="w-3 h-3" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function plainTextSnippet(html) {
  if (!html) return '';
  const tmp = document.createElement('div');
  tmp.innerHTML = sanitizeHtml(html);
  const text = (tmp.textContent || '').trim();
  return text.length > 32 ? text.slice(0, 32) + '…' : text;
}
