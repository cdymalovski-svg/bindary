import { useState } from 'react';
import { toast } from 'sonner';
import { Bookmark } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from '@/components/ui/dialog';
import { createTemplate } from '@/lib/api';

// Capture the active book's cover, interior and back-cover style as a re-usable template.
function pageStyle(p) {
  if (!p) return null;
  return {
    background_color: p.background_color || '#FFF8DC',
    full_bleed: !!p.full_bleed,
    show_page_number: !!p.show_page_number,
    page_number_align: p.page_number_align || 'right',
    page_number_size: p.page_number_size || 14,
  };
}

export default function SaveTemplateDialog({ book }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);

  const pages = book?.pages || [];
  const cover = pageStyle(pages[0]);
  // interior: use page index 1 if present, else fall back to cover style
  const interior = pageStyle(pages.length > 2 ? pages[1] : pages[0]);
  // back cover: last page if total >= 2, else fall back to cover
  const backCover = pageStyle(pages.length >= 2 ? pages[pages.length - 1] : pages[0]);

  const onSave = async () => {
    if (!cover || !interior || !backCover) return;
    if (!name.trim()) {
      toast.error('Name your template');
      return;
    }
    setSaving(true);
    try {
      await createTemplate({
        name: name.trim(),
        page_size: book.page_size,
        cover,
        interior,
        back_cover: backCover,
        text_presets: book.text_presets || null,
      });
      toast.success(`Template "${name.trim()}" saved`);
      setOpen(false);
      setName('');
    } catch (e) {
      toast.error('Could not save template');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          className="rounded-sm h-8 border-rule"
          data-testid="save-template-button"
        >
          <Bookmark className="w-4 h-4 mr-1" />
          Save as template
        </Button>
      </DialogTrigger>
      <DialogContent className="bg-paper border-rule rounded-sm sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl text-ink">Save book as template</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-1">
          <p className="text-sm text-ink-soft leading-relaxed">
            Captures this book's cover, interior and back-cover styling (color, bleed, page number) and any custom text presets — not its content.
          </p>
          <div className="space-y-2">
            <Label className="label-caps">Template name</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Storybook theme"
              className="bg-white border-rule rounded-sm"
              data-testid="template-name-input"
            />
          </div>
          <div className="bg-white border border-rule rounded-sm p-3 text-xs space-y-1 text-ink-soft">
            <StyleRow label="Cover" style={cover} />
            <StyleRow label="Interior" style={interior} />
            <StyleRow label="Back cover" style={backCover} />
          </div>
        </div>
        <DialogFooter>
          <Button
            onClick={onSave}
            disabled={saving}
            className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm"
            data-testid="confirm-save-template"
          >
            {saving ? 'Saving…' : 'Save template'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function StyleRow({ label, style }) {
  if (!style) return null;
  return (
    <div className="flex items-center gap-2">
      <span className="label-caps w-20">{label}</span>
      <span className="w-4 h-4 rounded-sm border border-rule" style={{ background: style.background_color }} />
      <span className="font-mono text-[10px]">{style.background_color}</span>
      {style.full_bleed && <span className="text-[10px] text-terracotta">bleed</span>}
    </div>
  );
}
