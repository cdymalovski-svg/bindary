import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { Plus, BookOpen, Trash2, FileText, Bookmark } from 'lucide-react';
import {
  listBooks, createBook, deleteBook, fileUrl,
  listTemplates, deleteTemplate, updateBook,
} from '@/lib/api';
import { PAGE_SIZES } from '@/lib/pageSizes';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';

export default function Dashboard() {
  const [books, setBooks] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({ title: '', author: '', page_size: 'a4', template_id: 'none' });
  const navigate = useNavigate();

  const refresh = async () => {
    setLoading(true);
    try {
      const [b, t] = await Promise.all([listBooks(), listTemplates()]);
      setBooks(b);
      setTemplates(t);
    } catch (e) {
      toast.error('Could not load library');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  const onCreate = async () => {
    try {
      const book = await createBook({
        title: form.title || 'Untitled Book',
        author: form.author || '',
        page_size: form.page_size,
      });
      // Apply template if selected
      const tpl = form.template_id && form.template_id !== 'none'
        ? templates.find((t) => t.id === form.template_id)
        : null;
      if (tpl) {
        const make = (s) => ({
          id: Math.random().toString(36).slice(2),
          blocks: [],
          background_color: s.background_color,
          full_bleed: !!s.full_bleed,
          show_page_number: !!s.show_page_number,
          page_number_align: s.page_number_align || 'right',
          page_number_size: s.page_number_size || 14,
        });
        const pages = [make(tpl.cover), make(tpl.interior), make(tpl.back_cover)];
        await updateBook(book.id, { pages, page_size: tpl.page_size });
      }
      setCreateOpen(false);
      setForm({ title: '', author: '', page_size: 'a4', template_id: 'none' });
      toast.success('Book created');
      navigate(`/editor/${book.id}`);
    } catch (e) {
      toast.error('Could not create book');
    }
  };

  const onDeleteTemplate = async (id) => {
    try {
      await deleteTemplate(id);
      setTemplates((arr) => arr.filter((t) => t.id !== id));
      toast.success('Template removed');
    } catch (e) {
      toast.error('Could not remove template');
    }
  };

  const onDelete = async (id) => {
    try {
      await deleteBook(id);
      toast.success('Book deleted');
      refresh();
    } catch (e) {
      toast.error('Could not delete');
    }
  };

  return (
    <div className="min-h-screen bg-desk">
      {/* Header */}
      <header className="border-b border-rule bg-paper/60 backdrop-blur-sm">
        <div className="max-w-6xl mx-auto px-8 py-6 flex items-center justify-between">
          <div className="flex items-baseline gap-3">
            <span className="font-serif italic text-3xl text-ink" data-testid="brand-mark">Bindery</span>
            <span className="label-caps">Book Studio</span>
          </div>
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger asChild>
              <Button
                data-testid="create-book-button"
                className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm font-sans font-medium tracking-wide"
              >
                <Plus className="w-4 h-4 mr-2" /> New Book
              </Button>
            </DialogTrigger>
            <DialogContent className="bg-paper border-rule rounded-sm sm:max-w-md">
              <DialogHeader>
                <DialogTitle className="font-serif text-3xl text-ink">A new manuscript</DialogTitle>
              </DialogHeader>
              <div className="space-y-4 py-2">
                <div className="space-y-2">
                  <Label className="label-caps">Title</Label>
                  <Input
                    data-testid="new-book-title-input"
                    value={form.title}
                    onChange={(e) => setForm({ ...form, title: e.target.value })}
                    placeholder="The Treasure Map…"
                    className="bg-white border-rule rounded-sm font-serif text-lg"
                  />
                </div>
                <div className="space-y-2">
                  <Label className="label-caps">Author</Label>
                  <Input
                    data-testid="new-book-author-input"
                    value={form.author}
                    onChange={(e) => setForm({ ...form, author: e.target.value })}
                    placeholder="Your name"
                    className="bg-white border-rule rounded-sm"
                  />
                </div>
                <div className="space-y-2">
                  <Label className="label-caps">Page Size</Label>
                  <Select
                    value={form.page_size}
                    onValueChange={(v) => setForm({ ...form, page_size: v })}
                  >
                    <SelectTrigger data-testid="new-book-pagesize-trigger" className="bg-white border-rule rounded-sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(PAGE_SIZES).map(([k, v]) => (
                        <SelectItem key={k} value={k} data-testid={`pagesize-option-${k}`}>
                          {v.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label className="label-caps">Template (optional)</Label>
                  <Select
                    value={form.template_id}
                    onValueChange={(v) => setForm({ ...form, template_id: v })}
                  >
                    <SelectTrigger data-testid="new-book-template-trigger" className="bg-white border-rule rounded-sm">
                      <SelectValue placeholder="Blank" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none" data-testid="template-option-none">Blank book</SelectItem>
                      {templates.map((t) => (
                        <SelectItem key={t.id} value={t.id} data-testid={`template-option-${t.id}`}>
                          {t.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {templates.length > 0 && (
                    <p className="text-[10px] text-ink-mute leading-relaxed">
                      Seeds 3 pages (cover · interior · back cover) using the template's style.
                    </p>
                  )}
                </div>
              </div>
              <DialogFooter>
                <Button
                  data-testid="confirm-create-book"
                  onClick={onCreate}
                  className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm"
                >
                  Begin writing
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </header>

      {/* Hero */}
      <section className="max-w-6xl mx-auto px-8 pt-16 pb-10">
        <p className="label-caps mb-4">Your Library</p>
        <h1 className="font-serif text-5xl sm:text-6xl text-ink leading-[1.05] tracking-tight max-w-3xl">
          Bind your story, page by page.
        </h1>
        <p className="mt-5 text-ink-soft max-w-xl leading-relaxed">
          A quiet studio for crafting books with text, illustration and care.
          Lay out pages, place artwork, and export a print-ready PDF.
        </p>
      </section>

      {/* Templates */}
      {templates.length > 0 && (
        <section className="max-w-6xl mx-auto px-8 pb-10" data-testid="templates-section">
          <div className="flex items-center justify-between mb-4">
            <p className="label-caps flex items-center gap-2">
              <Bookmark className="w-3 h-3" />
              Templates · {templates.length}
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            {templates.map((t) => (
              <div
                key={t.id}
                data-testid={`template-card-${t.id}`}
                className="group flex items-center gap-2 bg-paper border border-rule rounded-sm px-3 py-2"
              >
                <div className="flex -space-x-1">
                  <span className="w-5 h-5 rounded-sm border border-rule" style={{ background: t.cover.background_color }} title="cover" />
                  <span className="w-5 h-5 rounded-sm border border-rule" style={{ background: t.interior.background_color }} title="interior" />
                  <span className="w-5 h-5 rounded-sm border border-rule" style={{ background: t.back_cover.background_color }} title="back cover" />
                </div>
                <span className="font-serif text-base text-ink">{t.name}</span>
                <span className="text-[10px] text-ink-mute">{t.page_size}</span>
                <button
                  type="button"
                  onClick={() => onDeleteTemplate(t.id)}
                  data-testid={`delete-template-${t.id}`}
                  className="p-1 text-ink-mute hover:text-terracotta opacity-0 group-hover:opacity-100 transition-opacity"
                  title="Delete template"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Library */}
      <section className="max-w-6xl mx-auto px-8 pb-24" data-testid="books-library">
        {loading ? (
          <div className="text-ink-mute font-serif italic text-xl">Opening the shelves…</div>
        ) : books.length === 0 ? (
          <div className="border border-dashed border-rule rounded-sm p-16 text-center bg-paper/40">
            <BookOpen className="w-10 h-10 mx-auto text-ink-mute mb-4" strokeWidth={1.25} />
            <h3 className="font-serif text-2xl text-ink mb-2">The shelf is bare</h3>
            <p className="text-ink-soft mb-6">Create your first book to begin.</p>
            <Button
              onClick={() => setCreateOpen(true)}
              data-testid="empty-create-button"
              className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm"
            >
              <Plus className="w-4 h-4 mr-2" /> New Book
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-8">
            {books.map((b) => (
              <BookCard key={b.id} book={b} onOpen={() => navigate(`/editor/${b.id}`)} onDelete={() => onDelete(b.id)} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function BookCard({ book, onOpen, onDelete }) {
  const cover = book.cover_image_url ? fileUrl(book.cover_image_url) : null;
  return (
    <div
      className="group bg-paper border border-rule rounded-sm overflow-hidden hover:shadow-lg transition-shadow"
      data-testid={`book-card-${book.id}`}
    >
      <button
        type="button"
        onClick={onOpen}
        className="w-full text-left aspect-[3/4] bg-desk relative overflow-hidden"
        data-testid={`open-book-${book.id}`}
      >
        {cover ? (
          <img src={cover} alt={book.title} className="absolute inset-0 w-full h-full object-cover" />
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center p-6 bg-gradient-to-br from-paper to-desk">
            <FileText className="w-10 h-10 text-ink-mute mb-3" strokeWidth={1.25} />
            <p className="font-serif text-2xl text-ink text-center leading-tight">{book.title}</p>
            {book.author ? <p className="text-ink-mute mt-2 text-sm">by {book.author}</p> : null}
          </div>
        )}
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-ink/85 to-transparent text-paper px-4 py-3 opacity-0 group-hover:opacity-100 transition-opacity">
          <p className="font-serif text-xl truncate">{book.title}</p>
          <p className="text-xs text-paper/70">{book.page_count} pages · {book.page_size}</p>
        </div>
      </button>
      <div className="flex items-center justify-between px-4 py-3 border-t border-rule">
        <div className="min-w-0">
          <p className="font-serif text-lg text-ink truncate">{book.title}</p>
          <p className="text-xs text-ink-mute truncate">{book.author || 'Anonymous'} · {book.page_count} pages</p>
        </div>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <button
              data-testid={`delete-book-${book.id}`}
              className="p-2 text-ink-mute hover:text-terracotta rounded-sm"
              title="Delete"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </AlertDialogTrigger>
          <AlertDialogContent className="bg-paper border-rule rounded-sm">
            <AlertDialogHeader>
              <AlertDialogTitle className="font-serif text-2xl">Delete this book?</AlertDialogTitle>
              <AlertDialogDescription>
                This will permanently remove "{book.title}". This action cannot be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel className="rounded-sm" data-testid="cancel-delete">Cancel</AlertDialogCancel>
              <AlertDialogAction
                className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm"
                onClick={onDelete}
                data-testid="confirm-delete"
              >
                Delete
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </div>
  );
}
