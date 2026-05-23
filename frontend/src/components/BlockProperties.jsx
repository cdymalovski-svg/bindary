import { useEffect, useRef, useState } from 'react';
import {
  Bold,
  Italic,
  Underline,
  AlignLeft,
  AlignCenter,
  AlignRight,
  Trash2,
  Type,
  Palette,
  ChevronUp,
  ChevronDown,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';

const FONTS = [
  'Cormorant Garamond',
  'Outfit',
  'Georgia',
  'Times New Roman',
  'Helvetica',
  'Courier New',
];

const SIZES = [10, 12, 14, 16, 18, 20, 24, 28, 32, 36, 42, 48, 56, 64, 72];

const COLORS = ['#1C1B19', '#4A4843', '#9E4532', '#0E4F3F', '#3B4A6B', '#7A5C00', '#5C2A6A', '#FFFFFF'];

export default function BlockProperties({
  block,
  onChange,
  onDelete,
  onTextCommand,
  onZ,
}) {
  if (!block) return null;
  const isText = block.type === 'text';
  return (
    <div
      className="fixed top-20 right-6 z-30 w-72 bg-paper border border-rule rounded-sm shadow-xl p-4 space-y-3"
      data-testid="block-properties-panel"
    >
      <div className="flex items-center justify-between">
        <p className="label-caps">{isText ? 'Text Block' : 'Image Block'}</p>
        <div className="flex items-center gap-1">
          <button data-testid="block-bring-forward" onClick={() => onZ(1)} className="p-1 text-ink-mute hover:text-ink" title="Bring forward">
            <ChevronUp className="w-4 h-4" />
          </button>
          <button data-testid="block-send-backward" onClick={() => onZ(-1)} className="p-1 text-ink-mute hover:text-ink" title="Send backward">
            <ChevronDown className="w-4 h-4" />
          </button>
          <button data-testid="delete-block-button" onClick={onDelete} className="p-1 text-ink-mute hover:text-terracotta" title="Delete">
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {isText && (
        <>
          <div className="space-y-1">
            <p className="label-caps">Font</p>
            <Select value={block.font_family} onValueChange={(v) => onChange({ font_family: v })}>
              <SelectTrigger data-testid="font-family-trigger" className="bg-white border-rule rounded-sm h-8 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FONTS.map((f) => (
                  <SelectItem key={f} value={f} style={{ fontFamily: f }} data-testid={`font-option-${f}`}>{f}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <p className="label-caps">Size</p>
              <Select value={String(block.font_size)} onValueChange={(v) => onChange({ font_size: parseInt(v, 10) })}>
                <SelectTrigger data-testid="font-size-trigger" className="bg-white border-rule rounded-sm h-8 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SIZES.map((s) => (
                    <SelectItem key={s} value={String(s)} data-testid={`font-size-${s}`}>{s}px</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <p className="label-caps">Color</p>
              <ColorPicker value={block.color} onChange={(c) => onChange({ color: c })} />
            </div>
          </div>

          <div className="space-y-1">
            <p className="label-caps">Format Selection</p>
            <div className="flex items-center gap-1 bg-white border border-rule rounded-sm p-1">
              <FmtBtn testId="fmt-bold" onClick={() => onTextCommand('bold')}><Bold className="w-4 h-4" /></FmtBtn>
              <FmtBtn testId="fmt-italic" onClick={() => onTextCommand('italic')}><Italic className="w-4 h-4" /></FmtBtn>
              <FmtBtn testId="fmt-underline" onClick={() => onTextCommand('underline')}><Underline className="w-4 h-4" /></FmtBtn>
              <div className="w-px h-5 bg-rule mx-1" />
              <FmtBtn testId="align-left" active={block.text_align === 'left'} onClick={() => onChange({ text_align: 'left' })}><AlignLeft className="w-4 h-4" /></FmtBtn>
              <FmtBtn testId="align-center" active={block.text_align === 'center'} onClick={() => onChange({ text_align: 'center' })}><AlignCenter className="w-4 h-4" /></FmtBtn>
              <FmtBtn testId="align-right" active={block.text_align === 'right'} onClick={() => onChange({ text_align: 'right' })}><AlignRight className="w-4 h-4" /></FmtBtn>
            </div>
            <p className="text-[10px] text-ink-mute pt-1">Double-click block to edit text. Select text then apply Bold / Italic.</p>
          </div>
        </>
      )}

      {!isText && (
        <p className="text-sm text-ink-soft">Drag corners to resize. Drag the block to reposition.</p>
      )}

      <div className="pt-2 border-t border-rule grid grid-cols-2 gap-2 text-xs text-ink-mute">
        <div>W: {Math.round(block.width)}px</div>
        <div>H: {Math.round(block.height)}px</div>
        <div>X: {Math.round(block.x)}px</div>
        <div>Y: {Math.round(block.y)}px</div>
      </div>
    </div>
  );
}

function FmtBtn({ children, onClick, active, testId }) {
  return (
    <button
      type="button"
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      data-testid={testId}
      className={`w-7 h-7 flex items-center justify-center rounded-sm transition-colors ${
        active ? 'bg-terracotta text-paper' : 'text-ink hover:bg-desk'
      }`}
    >
      {children}
    </button>
  );
}

function ColorPicker({ value, onChange }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          data-testid="text-color-trigger"
          className="w-full h-8 border border-rule rounded-sm bg-white flex items-center gap-2 px-2"
        >
          <span className="w-4 h-4 rounded-sm border border-rule" style={{ background: value }} />
          <span className="text-xs text-ink-soft">{value}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-44 bg-paper border-rule rounded-sm p-2">
        <div className="grid grid-cols-4 gap-2">
          {COLORS.map((c) => (
            <button
              key={c}
              onClick={() => { onChange(c); setOpen(false); }}
              data-testid={`color-swatch-${c}`}
              className="w-8 h-8 rounded-sm border border-rule"
              style={{ background: c }}
            />
          ))}
        </div>
        <input
          type="color"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="mt-2 w-full h-8 border border-rule rounded-sm bg-white"
          data-testid="text-color-input"
        />
      </PopoverContent>
    </Popover>
  );
}
