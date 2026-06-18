import { useEffect, useRef } from 'react';
import { Rnd } from 'react-rnd';
import { fileUrl } from '@/lib/api';
import { sanitizeHtml } from '@/lib/sanitize';

export default function CanvasBlock({
  block,
  selected,
  editingTextId,
  onSelect,
  onChange,
  onStartTextEdit,
  onStopTextEdit,
  onTocJump,
  inkOverdrawn = false,
  scale = 1,
  pageWidth,
  pageHeight,
}) {
  const isText = block.type === 'text';
  const isImage = block.type === 'image';
  const editingThisText = isText && editingTextId === block.id;
  const editorRef = useRef(null);
  // Track pointer position on mousedown so we can detect "click without drag"
  // on mouseup. We use mouseup-with-threshold instead of onClick because
  // react-rnd swallows synthetic clicks once any small drag is detected.
  const downPosRef = useRef(null);

  // Initialize / sync DOM innerHTML when NOT editing.
  // While editing, we don't touch the DOM so the user's typing & caret stay intact.
  const PLACEHOLDER_HTML =
    '<span data-placeholder="true" style="opacity:0.45;font-style:italic">Double-click to edit</span>';
  useEffect(() => {
    if (!isText) return;
    if (editingThisText) return;
    if (!editorRef.current) return;
    const next = block.html && block.html.trim() ? sanitizeHtml(block.html) : PLACEHOLDER_HTML;
    if (editorRef.current.innerHTML !== next) {
      editorRef.current.innerHTML = next;
    }
  }, [block.html, editingThisText, isText]);

  // When entering edit mode, focus and place caret at end.
  useEffect(() => {
    if (!editingThisText || !editorRef.current) return;
    const el = editorRef.current;
    // If currently showing only the placeholder, clear it so the user types fresh.
    const onlyPlaceholder =
      el.firstChild && el.firstChild.nodeType === 1 && el.firstChild.getAttribute?.('data-placeholder') === 'true';
    if (onlyPlaceholder || !block.html) {
      el.innerHTML = '';
    }
    el.focus();
    const range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editingThisText]);

  return (
    <Rnd
      data-testid={`block-${block.id}`}
      bounds="parent"
      size={{ width: block.width, height: block.height }}
      position={{ x: block.x, y: block.y }}
      scale={scale}
      disableDragging={editingThisText}
      onDragStop={(e, d) => {
        onChange(block.id, { x: Math.max(0, Math.min(pageWidth - block.width, d.x)), y: Math.max(0, Math.min(pageHeight - block.height, d.y)) });
      }}
      onResizeStop={(e, dir, ref, delta, position) => {
        onChange(block.id, {
          width: parseFloat(ref.style.width),
          height: parseFloat(ref.style.height),
          x: position.x,
          y: position.y,
        });
      }}
      enableResizing={selected && !editingThisText}
      lockAspectRatio={isImage}
      className={`block-wrapper ${selected ? 'block-selected' : 'block-hover'}${inkOverdrawn ? ' block-ink-warning' : ''}`}
      style={{ zIndex: typeof block.z_index === 'number' ? block.z_index : 1 }}
      resizeHandleStyles={selected && !editingThisText ? handleStyles : {}}
    >
      <div
        onMouseDown={(e) => {
          if (editingThisText) {
            // Don't deselect/start drag while editing; let click reach the contentEditable.
            e.stopPropagation();
            return;
          }
          downPosRef.current = { x: e.clientX, y: e.clientY };
          if (!selected) {
            e.stopPropagation();
            onSelect(block.id);
          }
        }}
        onTouchStart={(e) => {
          // iOS Safari sometimes fires touchstart but no mousedown when a
          // contentEditable is focused. Without this guard, the touch
          // bubbles to the desk handler and immediately ends the edit
          // session — making it impossible to position the caret with a
          // second tap on iPad.
          if (editingThisText) {
            e.stopPropagation();
            return;
          }
          // Two-finger touches are pinch-zoom — leave them to the desk.
          if (e.touches.length >= 2) return;
          const t = e.touches[0];
          if (t) downPosRef.current = { x: t.clientX, y: t.clientY };
          if (!selected) {
            e.stopPropagation();
            onSelect(block.id);
          }
        }}
        onTouchEnd={(e) => {
          // Touch-equivalent of the onMouseUp "tap to enter text edit"
          // branch. iPad / iOS Safari does NOT reliably fire a synthetic
          // `dblclick` on double-tap (double-tap is reserved for the OS
          // zoom gesture), so the desktop double-click pattern is dead in
          // the water on touch. Single-tap on an already-selected text
          // block is the standard touch UX and we mirror it here.
          if (!isText || editingThisText) return;
          const start = downPosRef.current;
          downPosRef.current = null;
          if (!start) return;
          const t = e.changedTouches[0];
          if (!t) return;
          const dx = Math.abs(t.clientX - start.x);
          const dy = Math.abs(t.clientY - start.y);
          // Slightly more forgiving threshold than mouse — fingers wobble.
          if (dx < 8 && dy < 8) {
            // TOC click → jump to chapter page on tap.
            const row = e.target?.closest?.('[data-toc-target]');
            if (block.is_toc && row && onTocJump) {
              e.stopPropagation();
              const idx = parseInt(row.getAttribute('data-toc-target'), 10);
              if (!Number.isNaN(idx)) onTocJump(idx);
              return;
            }
            e.stopPropagation();
            onStartTextEdit(block.id);
          }
        }}
        onMouseUp={(e) => {
          // Mouseup-based click detection: only enter edit mode if the pointer
          // didn't move (i.e. not a drag). This bypasses react-rnd's click
          // swallowing on drag.
          if (!isText || editingThisText) return;
          const start = downPosRef.current;
          downPosRef.current = null;
          if (!start) return;
          const dx = Math.abs(e.clientX - start.x);
          const dy = Math.abs(e.clientY - start.y);
          if (dx < 4 && dy < 4) {
            // TOC click → jump to chapter page, never enter edit on a link.
            const row = e.target?.closest?.('[data-toc-target]');
            if (block.is_toc && row && onTocJump) {
              e.stopPropagation();
              const idx = parseInt(row.getAttribute('data-toc-target'), 10);
              if (!Number.isNaN(idx)) onTocJump(idx);
              return;
            }
            e.stopPropagation();
            onStartTextEdit(block.id);
          }
        }}
        onDoubleClick={(e) => {
          if (isText && !editingThisText) {
            e.stopPropagation();
            onStartTextEdit(block.id);
          }
        }}
        className={`w-full h-full ${editingThisText ? '' : 'select-none'}`}
      >
        {isText ? (
          <div
            ref={editorRef}
            className="text-block-editor px-2 py-1 w-full h-full overflow-hidden"
            contentEditable={editingThisText}
            suppressContentEditableWarning
            data-testid={`text-block-content-${block.id}`}
            style={{
              fontFamily: block.font_family,
              fontSize: `${block.font_size}px`,
              textAlign: block.text_align,
              color: block.color,
              lineHeight: 1.45,
              cursor: editingThisText ? 'text' : 'move',
            }}
            onBlur={(e) => {
              const html = e.currentTarget.innerHTML;
              // Treat placeholder-only content as empty.
              const isPlaceholder =
                e.currentTarget.firstChild?.nodeType === 1 &&
                e.currentTarget.firstChild.getAttribute?.('data-placeholder') === 'true' &&
                e.currentTarget.childNodes.length === 1;
              // Sanitise on save so persisted content never carries scripts/handlers.
              onChange(block.id, { html: isPlaceholder ? '' : sanitizeHtml(html) });
              onStopTextEdit();
            }}
            onPaste={(e) => {
              // Force-paste as plain text so pasted HTML (e.g. <img onerror=...>)
              // never reaches the live contentEditable — eliminates transient
              // self-XSS even before onBlur sanitises.
              e.preventDefault();
              const text = e.clipboardData?.getData('text/plain') || '';
              document.execCommand('insertText', false, text);
            }}
          />
        ) : null}
        {isImage ? (
          block.image_url ? (
            <img
              src={fileUrl(block.image_url)}
              alt=""
              className="w-full h-full object-contain pointer-events-none"
              draggable={false}
              crossOrigin="anonymous"
              data-testid={`image-block-img-${block.id}`}
              style={block.background_color ? { background: block.background_color } : undefined}
            />
          ) : block.background_color ? (
            // No image yet, but the block has a background fill —
            // becomes a pure colour-tile (e.g. a coloured accent panel
            // behind text). No "No image" placeholder.
            <div
              className="w-full h-full"
              style={{ background: block.background_color }}
              data-testid={`image-block-color-${block.id}`}
            />
          ) : (
            <div className="w-full h-full bg-desk flex items-center justify-center text-ink-mute text-sm">
              No image
            </div>
          )
        ) : null}
      </div>
    </Rnd>
  );
}

const handleSize = 10;
const handleBase = {
  width: handleSize,
  height: handleSize,
  background: '#9E4532',
  border: '2px solid #F9F6F0',
  borderRadius: 0,
};
const handleStyles = {
  topLeft: handleBase,
  top: handleBase,
  topRight: handleBase,
  right: handleBase,
  bottomRight: handleBase,
  bottom: handleBase,
  bottomLeft: handleBase,
  left: handleBase,
};
