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
      enableResizing={selected}
      className={`block-wrapper ${selected ? 'block-selected' : 'block-hover'}`}
      style={{ zIndex: block.z_index || 1 }}
      resizeHandleStyles={selected ? handleStyles : {}}
    >
      <div
        onMouseDown={(e) => {
          if (!selected) {
            e.stopPropagation();
            onSelect(block.id);
          }
        }}
        onDoubleClick={(e) => {
          if (isText) {
            e.stopPropagation();
            onStartTextEdit(block.id);
          }
        }}
        className="w-full h-full select-none"
      >
        {isText ? (
          <div
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
              onChange(block.id, { html: e.currentTarget.innerHTML });
              onStopTextEdit();
            }}
            dangerouslySetInnerHTML={{ __html: block.html || '<p>Double-click to edit</p>' }}
          />
        ) : null}
        {isImage ? (
          block.image_url ? (
            <img
              src={fileUrl(block.image_url)}
              alt=""
              className="w-full h-full object-cover pointer-events-none"
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
