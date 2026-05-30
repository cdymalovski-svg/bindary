import { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Sparkles, Loader2, X, Wand2 } from 'lucide-react';
import { toast } from 'sonner';
import { generateAsset } from '@/lib/api';

const PROMPT_EXAMPLES = [
  'Watercolor illustration of a sleepy fox curled under an autumn tree, soft pastel palette, children\'s storybook style.',
  'Vintage botanical line drawing of a fern, tan parchment background, fine ink hatching.',
  'A whimsical hot-air balloon floating over a Tuscan hillside at sunrise, oil-painting texture.',
  'Minimal flat-vector illustration of a coffee cup with steam forming a heart, terracotta on cream.',
];

export default function GenerateImageDialog({ open, onClose, bookId, onGenerated }) {
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const textareaRef = useRef(null);

  useEffect(() => {
    if (open) {
      // Focus the textarea when the dialog opens so the user can type
      // immediately without grabbing the mouse.
      setTimeout(() => textareaRef.current?.focus(), 60);
    }
  }, [open]);

  // Allow Escape to close while not generating.
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === 'Escape' && !busy) onClose?.();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, busy, onClose]);

  if (!open) return null;

  const submit = async () => {
    const trimmed = prompt.trim();
    if (!trimmed) {
      toast.error('Describe the illustration you want first.');
      return;
    }
    setBusy(true);
    const toastId = toast.loading('Conjuring your illustration…');
    try {
      const asset = await generateAsset({ prompt: trimmed, bookId });
      toast.success('Illustration generated. Drag it onto your page.', { id: toastId });
      onGenerated?.(asset);
      setPrompt('');
      onClose?.();
    } catch (e) {
      const detail = e?.response?.data?.detail || e.message || 'Generation failed';
      toast.error(detail, { id: toastId });
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-ink/70 backdrop-blur-sm"
      data-testid="generate-image-dialog"
      onClick={() => { if (!busy) onClose?.(); }}
    >
      <div
        className="w-full max-w-lg bg-paper text-ink rounded-md shadow-2xl border border-rule overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-rule">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-terracotta" />
            <h2 className="font-serif text-lg">Generate an illustration</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            data-testid="generate-dialog-close"
            className="p-1 rounded hover:bg-rule/40 disabled:opacity-50"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-3">
          <p className="text-sm text-ink-mute leading-relaxed">
            Describe what you want and we'll generate an illustration with Gemini Nano Banana. The
            image lands in your Assets panel so you can drag it onto any page.
          </p>
          <textarea
            ref={textareaRef}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={busy}
            rows={4}
            maxLength={2000}
            placeholder="A whimsical illustration of…"
            data-testid="generate-prompt-input"
            className="w-full px-3 py-2 border border-rule rounded-sm bg-paper-2 text-ink placeholder:text-ink-mute focus:outline-none focus:ring-2 focus:ring-terracotta resize-none text-sm leading-relaxed font-serif disabled:opacity-60"
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                e.preventDefault();
                submit();
              }
            }}
          />
          <div className="flex items-center justify-between text-xs text-ink-mute">
            <span>{prompt.length}/2000</span>
            <span className="hidden sm:inline">⌘↵ to generate</span>
          </div>

          <div className="pt-1">
            <p className="text-xs uppercase tracking-[0.18em] text-ink-mute mb-2">Try one</p>
            <div className="flex flex-col gap-1.5">
              {PROMPT_EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  type="button"
                  onClick={() => setPrompt(ex)}
                  disabled={busy}
                  data-testid="generate-prompt-example"
                  className="text-left text-xs text-ink/80 hover:text-terracotta hover:bg-rule/30 px-2 py-1.5 rounded-sm border border-transparent hover:border-rule transition-colors leading-snug disabled:opacity-50"
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-rule bg-paper-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            data-testid="generate-dialog-cancel"
            className="px-3 py-1.5 text-sm rounded-sm hover:bg-rule/40 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={busy || !prompt.trim()}
            data-testid="generate-submit-button"
            className="px-4 py-1.5 text-sm rounded-sm bg-terracotta hover:bg-terracotta-dark text-paper inline-flex items-center gap-2 disabled:opacity-60"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}
            {busy ? 'Generating…' : 'Generate'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
