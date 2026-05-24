import { useState } from 'react';
import { Eye, EyeOff, AlignLeft, AlignCenter, AlignRight } from 'lucide-react';
import {
  Popover, PopoverContent, PopoverTrigger,
} from '@/components/ui/popover';
import { Switch } from '@/components/ui/switch';

const PAGE_COLORS = [
  '#F9F6F0', // paper (default)
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
  '#E0CBA8', // wheat
  '#CFE3DC', // sage
];

export default function PagePanel({ page, pageIndex, onChange }) {
  if (!page) {
    return <div className="p-6 text-ink-mute text-sm font-serif italic">Select a page to edit its properties.</div>;
  }
  return (
    <div className="p-4 space-y-4" data-testid="page-properties-panel">
      <div className="flex items-center justify-between">
        <p className="label-caps">Page {pageIndex + 1}</p>
      </div>

      <div className="space-y-2">
        <p className="label-caps">Background Color</p>
        <div className="grid grid-cols-4 gap-2" data-testid="page-color-swatches">
          {PAGE_COLORS.map((c) => {
            const active = (page.background_color || '#F9F6F0').toLowerCase() === c.toLowerCase();
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
        <CustomColorRow value={page.background_color || '#F9F6F0'} onChange={(c) => onChange({ background_color: c })} />
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
        )}
        <p className="text-[10px] text-ink-mute leading-relaxed">
          The page number sits inside the colored area. A 1cm white margin frames the page for safe printing.
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
