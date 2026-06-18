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
  Wand2,
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
  onApplyPreset,
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

      {/* Apply-default — the inverse of Save-as-default. Snaps the
          selected block back to one of the book's saved presets so a
          manually-tweaked block can be restored to the canonical style.
          Hidden when there's nothing to apply (no role would match) —
          but we always render the menu since the built-in presets are
          always available as a fallback. */}
      {isText && onApplyPreset && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              data-testid="apply-preset-trigger"
              className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-white border border-rule hover:bg-desk text-ink rounded-sm text-sm font-medium transition-colors"
              title="Snap this block back to one of the book's default text styles"
            >
              <Wand2 className="w-4 h-4" />
              Apply default style…
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="bg-paper border-rule rounded-sm w-48 p-1">
            <DropdownMenuLabel className="text-[10px] tracking-[0.18em] uppercase text-ink-mute">
              Apply preset
            </DropdownMenuLabel>
            <DropdownMenuItem
              data-testid="apply-preset-title"
              onClick={() => onApplyPreset('title')}
              className="rounded-sm cursor-pointer"
            >
              Title preset
            </DropdownMenuItem>
            <DropdownMenuItem
              data-testid="apply-preset-subtitle"
              onClick={() => onApplyPreset('subtitle')}
              className="rounded-sm cursor-pointer"
            >
              Subtitle preset
            </DropdownMenuItem>
            <DropdownMenuItem
              data-testid="apply-preset-body"
              onClick={() => onApplyPreset('body')}
              className="rounded-sm cursor-pointer"
            >
              Page text preset
            </DropdownMenuItem>
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

          {/* Background fill — shows through transparent areas of the
              image (handy for hand-drawn illustrations on a coloured
              backdrop) AND turns an empty image block into a pure
              colour tile (useful as an accent panel behind text). */}
          <div className="pt-2 border-t border-rule space-y-2">
            <div className="flex items-center justify-between">
              <p className="label-caps">Background fill</p>
              {block.background_color && (
                <button
                  type="button"
                  onClick={() => onChange({ background_color: null })}
                  className="text-[10px] text-ink-mute hover:text-terracotta"
                  data-testid="image-bg-clear"
                  title="Remove background fill"
                >
                  Clear
                </button>
              )}
            </div>
            <ImageBackgroundPicker
              value={block.background_color}
              onChange={(c) => onChange({ background_color: c })}
            />
            <p className="text-[10px] text-ink-mute leading-snug">
              Shows through transparent PNGs and fills the block when no image is set.
            </p>
          </div>
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

// Larger swatch palette tuned for backgrounds (kids' book pastels +
// editorial darks). Pairs with the native colour input for fully
// arbitrary hex values + a hex text field for paste-from-Figma flows.
const IMAGE_BG_COLORS = [
  '#FFF8DC', '#FAF3E7', '#F2E8D5', '#E8D9C0',
  '#CFE3DC', '#B7D2C1', '#F8C9B7', '#EFB47E',
  '#E6A56C', '#D88A6E', '#9E4532', '#5B1A1A',
  '#7A5C00', '#3B4A6B', '#0E3B2E', '#1C1B19',
];

/**
 * Background-fill picker for image blocks. Shows three input affordances:
 *   • A 4×4 swatch palette of editorial-friendly colours
 *   • A native browser colour wheel (`<input type="color">`) for any RGB
 *   • A plain-text hex field for paste-from-design-tools workflows
 * All three are wired to the same `onChange` so the user picks whatever
 * affordance fits the moment.
 */
function ImageBackgroundPicker({ value, onChange }) {
  const [open, setOpen] = useState(false);
  const [hex, setHex] = useState(value || '');
  // Sync local input state whenever the upstream block value changes
  // (e.g. after a swatch click) so the field always reflects truth.
  useEffect(() => { setHex(value || ''); }, [value]);

  const commitHex = (raw) => {
    const t = (raw || '').trim();
    if (!t) { onChange(null); return; }
    // Accept "FFE9C8" as well as "#FFE9C8" — designers commonly paste
    // without the leading hash from Figma.
    const normalised = t.startsWith('#') ? t : `#${t}`;
    if (/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(normalised)) {
      onChange(normalised);
    }
  };

  const display = value || 'No fill';
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid="image-bg-trigger"
          className="w-full h-9 border border-rule rounded-sm bg-white flex items-center gap-2 px-2"
        >
          <span
            className="w-5 h-5 rounded-sm border border-rule"
            style={
              value
                ? { background: value }
                : {
                    // Checkerboard signals "transparent / no fill".
                    backgroundImage:
                      'linear-gradient(45deg,#ccc 25%,transparent 25%),linear-gradient(-45deg,#ccc 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#ccc 75%),linear-gradient(-45deg,transparent 75%,#ccc 75%)',
                    backgroundSize: '8px 8px',
                    backgroundPosition: '0 0, 0 4px, 4px -4px, -4px 0',
                  }
            }
          />
          <span className="text-xs text-ink-soft flex-1 text-left truncate">{display}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-52 bg-paper border-rule rounded-sm p-2 space-y-2" data-testid="image-bg-popover">
        <div className="grid grid-cols-4 gap-1.5" data-testid="image-bg-swatches">
          {IMAGE_BG_COLORS.map((c) => {
            const active = (value || '').toLowerCase() === c.toLowerCase();
            return (
              <button
                key={c}
                type="button"
                onClick={() => { onChange(c); setOpen(false); }}
                data-testid={`image-bg-swatch-${c}`}
                className={`w-full aspect-square rounded-sm border ${active ? 'ring-2 ring-terracotta ring-offset-1' : 'border-rule'}`}
                style={{ background: c }}
                title={c}
              />
            );
          })}
        </div>
        <input
          type="color"
          value={value || '#FFFFFF'}
          onChange={(e) => onChange(e.target.value)}
          className="w-full h-9 border border-rule rounded-sm bg-white cursor-pointer"
          data-testid="image-bg-color-input"
        />
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-ink-mute uppercase tracking-widest">Hex</span>
          <input
            type="text"
            value={hex}
            placeholder="#FFE9C8"
            onChange={(e) => setHex(e.target.value)}
            onBlur={() => commitHex(hex)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { commitHex(hex); setOpen(false); }
            }}
            className="flex-1 h-7 border border-rule rounded-sm bg-white px-2 text-xs font-mono"
            data-testid="image-bg-hex-input"
            spellCheck={false}
          />
        </div>
      </PopoverContent>
    </Popover>
  );
}