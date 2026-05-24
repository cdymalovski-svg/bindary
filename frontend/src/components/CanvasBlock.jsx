import { useEffect, useRef } from 'react';
import { Rnd } from 'react-rnd';
import { fileUrl } from '@/lib/api';

export default function CanvasBlock({
  block,
  selected,
  editingTextId,
  onSelect,
  onChange,
  onStartTextEdit,
  onStopTextEdit,
  scale = 1,
  pageWidth,
  pageHeight,
}) {
  const isText = block.type === 'text';
  const isImage = block.type === 'image';
  const editingThisText = isText && editingTextId === block.id;
  const editorRef = useRef(null);

  // Initialize / sync DOM innerHTML when NOT editing.
  // While editing, we don't touch the DOM so the user's typing & caret stay intact.
  const PLACEHOLDER_HTML =
    '<span data-placeholder="true" style="opacity:0.45;font-style:italic">Double-click to edit</span>';
  useEffect(() => {
    if (!isText) return;
    if (editingThisText) return;
    if (!editorRef.current) return;
    const next = block.html && block.html.trim() ? block.html : PLACEHOLDER_HTML;
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
      className={`block-wrapper ${selected ? 'block-selected' : 'block-hover'}`}
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
          if (!selected) {
            e.stopPropagation();
            onSelect(block.id);
          }
        }}
        onClick={(e) => {
          // Single click on a text block always enters edit mode.
          // React-rnd swallows the synthetic click when a real drag has occurred,
          // so dragging continues to work. This avoids racing with React 18's batched
          // state updates between mousedown and click.
          if (isText && !editingThisText) {
            e.stopPropagation();
            onStartTextEdit(block.id);
          }
        }}
        onDoubleClick={(e) => {
          if (isText) {
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
              onChange(block.id, { html: isPlaceholder ? '' : html });
              onStopTextEdit();
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
