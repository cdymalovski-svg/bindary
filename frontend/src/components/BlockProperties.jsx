import { useEffect, useRef, useState } from 'react';
import {
  Bold,
  Italic,
  Underline,
  AlignLeft,
  AlignCenter,
  AlignRight,
  Trash2,
  ChevronUp,
  ChevronDown,
  ChevronsUp,
  ChevronsDown,
  Maximize2,
  Save as SaveIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectLabel,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuLabel,
} from '@/components/ui/dropdown-menu';
import { FONT_GROUPS } from '@/lib/fonts';

const FONT_GROUPS_LIST = FONT_GROUPS;

const SIZES = [10, 12, 14, 16, 18, 20, 24, 28, 32, 36, 42, 48, 56, 64, 72];

const COLORS = ['#000000', '#4A4843', '#9E4532', '#0E4F3F', '#3B4A6B', '#7A5C00', '#5C2A6A', '#FFFFFF'];

export default function BlockProperties({
  block,
  onChange,
  onDelete,
  onTextCommand,
  onLayer,
  onFit,
  onSaveAsPreset,
  onResetPreset,
  textPresets,
}) {
  if (!block) return null;
  const isText = block.type === 'text';
  return (
    <div
      className="p-4 space-y-3"
      data-testid="block-properties-panel"
    >
      <div className="flex items-center justify-between">
        <p className="label-caps">{isText ? 'Text Block' : 'Image Block'}</p>
        <button data-testid="delete-block-button" onClick={onDelete} className="p-1 text-ink-mute hover:text-terracotta" title="Delete">
          <Trash2 className="w-4 h-4" />
        </button>
      </div>

      {/* Layer controls */}
      <div className="space-y-1">
        <p className="label-caps">Layer</p>
        <div className="flex items-center gap-1 bg-white border border-rule rounded-sm p-1">
          <FmtBtn testId="layer-to-back" onClick={() => onLayer('back')} title="Send to back">
            <ChevronsDown className="w-4 h-4" />
          </FmtBtn>
          <FmtBtn testId="layer-backward" onClick={() => onLayer('backward')} title="Send backward">
            <ChevronDown className="w-4 h-4" />
          </FmtBtn>
          <div className="flex-1 text-center text-[10px] font-mono text-ink-mute" data-testid="layer-current-z">
            z {typeof block.z_index === 'number' ? block.z_index : 1}
          </div>
          <FmtBtn testId="layer-forward" onClick={() => onLayer('forward')} title="Bring forward">
            <ChevronUp className="w-4 h-4" />
          </FmtBtn>
          <FmtBtn testId="layer-to-front" onClick={() => onLayer('front')} title="Bring to front">
            <ChevronsUp className="w-4 h-4" />
          </FmtBtn>
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
              <SelectContent className="max-h-72">
                {FONT_GROUPS_LIST.map((g) => (
                  <SelectGroup key={g.label}>
                    <SelectLabel className="text-[10px] tracking-[0.18em] uppercase text-ink-mute">{g.label}</SelectLabel>
                    {g.fonts.map((f) => (
                      <SelectItem key={f} value={f} style={{ fontFamily: f }} data-testid={`font-option-${f}`}>{f}</SelectItem>
                    ))}
                  </SelectGroup>
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
            <p className="text-[10px] text-ink-mute pt-1">Click a text block to edit it (or double-click). Select text then apply Bold / Italic.</p>
          </div>
        </>
      )}

      {isText && onSaveAsPreset && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              data-testid="save-as-preset-trigger"
              className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-white border border-rule hover:bg-desk text-ink rounded-sm text-sm font-medium transition-colors"
              title="Make this block's font, size, alignment and color the default for one of the Text presets"
            >
              <SaveIcon className="w-4 h-4" />
              Save as default…
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="bg-paper border-rule rounded-sm w-48 p-1">
            <DropdownMenuLabel className="text-[10px] tracking-[0.18em] uppercase text-ink-mute">
              Save this style as
            </DropdownMenuLabel>
            <DropdownMenuItem
              data-testid="save-as-preset-title"
              onClick={() => onSaveAsPreset('title')}
              className="rounded-sm cursor-pointer"
            >
              Title preset
            </DropdownMenuItem>
            <DropdownMenuItem
              data-testid="save-as-preset-subtitle"
              onClick={() => onSaveAsPreset('subtitle')}
              className="rounded-sm cursor-pointer"
            >
              Subtitle preset
            </DropdownMenuItem>
            <DropdownMenuItem
              data-testid="save-as-preset-body"
              onClick={() => onSaveAsPreset('body')}
              className="rounded-sm cursor-pointer"
            >
              Page text preset
            </DropdownMenuItem>
            {onResetPreset && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuLabel className="text-[10px] tracking-[0.18em] uppercase text-ink-mute">
                  Reset to built-in
                </DropdownMenuLabel>
                {[
                  { key: 'title', label: 'Title preset' },
                  { key: 'subtitle', label: 'Subtitle preset' },
                  { key: 'body', label: 'Page text preset' },
                ].map(({ key, label }) => {
                  const isCustom = !!textPresets?.[key];
                  return (
                    <DropdownMenuItem
                      key={key}
                      data-testid={`reset-preset-${key}`}
                      onClick={() => onResetPreset(key)}
                      disabled={!isCustom}
                      className="rounded-sm cursor-pointer text-ink-soft"
                    >
                      {label}
                    </DropdownMenuItem>
                  );
                })}
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      {isText && onFit && (
        <button
          type="button"
          onClick={onFit}
          data-testid="fit-to-content-button"
          className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm text-sm font-medium transition-colors"
          title="Resize the frame to hug the current text"
        >
          <Maximize2 className="w-4 h-4" />
          Frame from text
        </button>
      )}

      {!isText && (
        <div className="space-y-2">
          <button
            type="button"
            onClick={onFit}
            data-testid="fit-to-page-button"
            className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm text-sm font-medium transition-colors"
            title="Resize to fill the colored area of the page (preserves aspect ratio)"
          >
            <Maximize2 className="w-4 h-4" />
            Fit to page
          </button>
          <p className="text-[10px] text-ink-mute leading-relaxed">
            Drag corners to resize. Aspect ratio is locked for illustrations.
          </p>
        </div>
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

function FmtBtn({ children, onClick, active, testId, title }) {
  return (
    <button
      type="button"
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      data-testid={testId}
      title={title}
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
