import { useState } from 'react';
import { Eye, EyeOff, AlignLeft, AlignCenter, AlignRight, Copy } from 'lucide-react';
import {
  Popover, PopoverContent, PopoverTrigger,
} from '@/components/ui/popover';
import { Switch } from '@/components/ui/switch';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';

const PAGE_COLORS = [
  '#FFF8DC', // light yellow (default)
  '#F9F6F0', // paper
  '#FFFFFF', // pure white
  '#FAF3E7', // ivory
  '#F2E8D5', // parchment
  '#EFE6D8', // sand
  '#E8D9C0', // kraft
  '#1C1B19', // ink (dark)
  '#1F2D3D', // midnight
  '#0E3B2E', // forest
  '#5B1A1A', // burgundy
  '#9E4532', // terracotta
  '#3B4A6B', // dusk blue
  '#7A5C00', // mustard
  '#5C2A6A', // plum
  '#CFE3DC', // sage
];

const PAGE_NUMBER_SIZES = [10, 12, 14, 16, 18, 20, 24, 28, 32];

const PAGE_NUMBER_FONTS = [
  'Cormorant Garamond',
  'Outfit',
  'Georgia',
  'Times New Roman',
  'Helvetica',
  'Courier New',
];

export default function PagePanel({ page, pageIndex, totalPages = 1, onChange, onPageNumberStyleAll, onApplyToInterior }) {
  if (!page) {
    return <div className="p-6 text-ink-mute text-sm font-serif italic">Select a page to edit its properties.</div>;
  }
  const isCover = pageIndex === 0;
  const isBackCover = totalPages > 1 && pageIndex === totalPages - 1;
  const isFirstOrLast = isCover || isBackCover;
  const interiorCount = Math.max(0, totalPages - 2);
  return (
    <div className="p-4 space-y-4" data-testid="page-properties-panel">
      <div className="flex items-center justify-between">
        <p className="label-caps">
          Page {pageIndex + 1}
          {isCover ? ' · Cover' : isBackCover ? ' · Back cover' : ''}
        </p>
      </div>

      <div className="space-y-2">
        <p className="label-caps">Background Color</p>
        <div className="grid grid-cols-4 gap-2" data-testid="page-color-swatches">
          {PAGE_COLORS.map((c) => {
            const active = (page.background_color || '#FFF8DC').toLowerCase() === c.toLowerCase();
            return (
              <button
                key={c}
                type="button"
                onClick={() => onChange({ background_color: c })}
                className={`relative w-full aspect-square rounded-sm border ${active ? 'ring-2 ring-terracotta ring-offset-1' : 'border-rule'}`}
                style={{ background: c }}
                title={c}
                data-testid={`page-color-${c}`}
              />
            );
          })}
        </div>
        <CustomColorRow value={page.background_color || '#FFF8DC'} onChange={(c) => onChange({ background_color: c })} />
      </div>

      <div className="pt-2 border-t border-rule space-y-2">
        <div className="flex items-center justify-between">
          <div>
            <p className="label-caps">Full bleed</p>
            <p className="text-[10px] text-ink-mute mt-0.5">Edge-to-edge color (no 1cm margin)</p>
          </div>
          <Switch
            checked={!!page.full_bleed}
            onCheckedChange={(v) => onChange({ full_bleed: v })}
            data-testid="full-bleed-switch"
          />
        </div>
      </div>

      <div className="pt-2 border-t border-rule space-y-3">
        <div className="flex items-center justify-between">
          <p className="label-caps">Page Number</p>
          <Switch
            checked={!!page.show_page_number}
            onCheckedChange={(v) => onChange({ show_page_number: v })}
            data-testid="page-number-switch"
          />
        </div>
        {page.show_page_number && (
          <>
            <div className="space-y-1">
              <p className="label-caps">Alignment</p>
              <div className="flex items-center gap-1 bg-white border border-rule rounded-sm p-1">
                {[
                  { key: 'left', Icon: AlignLeft },
                  { key: 'center', Icon: AlignCenter },
                  { key: 'right', Icon: AlignRight },
                ].map(({ key, Icon }) => {
                  const active = (page.page_number_align || 'right') === key;
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => onChange({ page_number_align: key })}
                      data-testid={`page-number-align-${key}`}
                      className={`flex-1 h-7 flex items-center justify-center rounded-sm transition-colors ${
                        active ? 'bg-terracotta text-paper' : 'text-ink hover:bg-desk'
                      }`}
                      title={`Align ${key}`}
                    >
                      <Icon className="w-4 h-4" />
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="space-y-1">
              <p className="label-caps">Font · applies to all pages</p>
              <Select
                value={page.page_number_font || 'Cormorant Garamond'}
                onValueChange={(v) => onPageNumberStyleAll?.({ page_number_font: v })}
              >
                <SelectTrigger className="bg-white border-rule rounded-sm h-8 text-sm" data-testid="page-number-font-trigger">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PAGE_NUMBER_FONTS.map((f) => (
                    <SelectItem key={f} value={f} style={{ fontFamily: f }} data-testid={`page-number-font-${f}`}>{f}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <p className="label-caps">Size · applies to all pages</p>
              <Select
                value={String(page.page_number_size || 14)}
                onValueChange={(v) => onPageNumberStyleAll?.({ page_number_size: parseInt(v, 10) })}
              >
                <SelectTrigger className="bg-white border-rule rounded-sm h-8 text-sm" data-testid="page-number-size-trigger">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PAGE_NUMBER_SIZES.map((s) => (
                    <SelectItem key={s} value={String(s)} data-testid={`page-number-size-${s}`}>{s}px</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </>
        )}
        <p className="text-[10px] text-ink-mute leading-relaxed">
          The page number sits inside the colored area. A 1cm white margin frames the page for safe printing.
        </p>
      </div>

      <div className="pt-2 border-t border-rule space-y-2">
        <button
          type="button"
          onClick={onApplyToInterior}
          disabled={interiorCount === 0 || isFirstOrLast}
          data-testid="apply-to-interior-button"
          className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-ink hover:bg-ink-soft text-paper rounded-sm text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          title="Copy these settings to all interior pages"
        >
          <Copy className="w-4 h-4" />
          Apply to all interior pages
        </button>
        <p className="text-[10px] text-ink-mute leading-relaxed">
          {isFirstOrLast
            ? 'Select an interior page to copy its settings out to the other interior pages.'
            : interiorCount === 0
            ? 'Add a middle page first — covers stay independent.'
            : `Copies color, bleed and page-number settings to ${interiorCount} page${interiorCount === 1 ? '' : 's'} (skipping the front and back covers).`}
        </p>
      </div>
    </div>
  );
}

function CustomColorRow({ value, onChange }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid="page-color-custom-trigger"
          className="w-full mt-2 h-9 border border-rule rounded-sm bg-white flex items-center gap-2 px-2"
        >
          <span className="w-5 h-5 rounded-sm border border-rule" style={{ background: value }} />
          <span className="text-xs text-ink-soft flex-1 text-left">Custom · {value}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-44 bg-paper border-rule rounded-sm p-2">
        <input
          type="color"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full h-10 border border-rule rounded-sm bg-white cursor-pointer"
          data-testid="page-color-custom-input"
        />
      </PopoverContent>
    </Popover>
  );
}
