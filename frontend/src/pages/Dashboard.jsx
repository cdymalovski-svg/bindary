import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { Plus, BookOpen, Trash2, FileText, Bookmark, Copy, Search, Pencil, Upload } from 'lucide-react';
import {
  listBooks, createBook, importBook, deleteBook, fileUrl,
  listTemplates, deleteTemplate, updateBook,
  duplicateBook,
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
  // When set, "Begin writing" imports the manuscript instead of creating a blank book.
  const [importFile, setImportFile] = useState(null);
  const [importing, setImporting] = useState(false);
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState('updated'); // 'updated' | 'created'
  const navigate = useNavigate();

  const refresh = useCallback(async () => {
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
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const onCreate = async () => {
    try {
      // Import path: an uploaded manuscript becomes the book's pages.
      if (importFile) {
        setImporting(true);
        const book = await importBook({
          file: importFile,
          title: form.title || undefined,
          author: form.author || undefined,
          page_size: form.page_size,
        });
        setCreateOpen(false);
        setForm({ title: '', author: '', page_size: 'a4', template_id: 'none' });
        setImportFile(null);
        toast.success(`Imported · ${book.pages.length} pages`);
        navigate(`/editor/${book.id}`);
        return;
      }
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
        await updateBook(book.id, {
          pages,
          page_size: tpl.page_size,
          text_presets: tpl.text_presets || null,
        });
      }
      setCreateOpen(false);
      setForm({ title: '', author: '', page_size: 'a4', template_id: 'none' });
      toast.success('Book created');
      navigate(`/editor/${book.id}`);
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Could not create book';
      toast.error(msg);
    } finally {
      setImporting(false);
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

  const onDuplicate = async (id) => {
    try {
      const copy = await duplicateBook(id);
      toast.success(`Duplicated "${copy.title}"`);
      refresh();
    } catch (e) {
      toast.error('Could not duplicate');
    }
  };

  const onEditDetails = async (id, { title, author }) => {
    try {
      await updateBook(id, { title: title.trim() || 'Untitled Book', author: author.trim() });
      toast.success('Details updated');
      refresh();
    } catch (e) {
      toast.error('Could not update');
      throw e;
    }
  };

  return (
    <div className="min-h-screen bg-desk">
      {/* Header */}
      <header className="border-b border-rule bg-paper/60 backdrop-blur-sm">
        <div className="max-w-6xl mx-auto px-8 py-5 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <img
              src="/logo.png"
              alt="Bindery — Book Studio"
              className="h-20 w-auto select-none"
              data-testid="brand-logo"
              draggable={false}
            />
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
                    disabled={!!importFile}
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
                  {templates.length > 0 && !importFile && (
                    <p className="text-[10px] text-ink-mute leading-relaxed">
                      Seeds 3 pages (cover · interior · back cover) using the template's style.
                    </p>
                  )}
                </div>

                {/* Import manuscript — when set, the book's pages are pre-populated
                    from the document's text instead of starting blank. */}
                <div className="space-y-2 pt-2 border-t border-rule/60">
                  <Label className="label-caps">Import manuscript (optional)</Label>
                  {importFile ? (
                    <div
                      className="flex items-center justify-between bg-paper-warm/40 border border-rule rounded-sm px-3 py-2 text-sm"
                      data-testid="import-file-chip"
                    >
                      <span className="font-serif truncate pr-2" title={importFile.name}>
                        {importFile.name}
                      </span>
                      <button
                        type="button"
                        onClick={() => setImportFile(null)}
                        className="text-ink-mute hover:text-terracotta text-xs label-caps"
                        data-testid="import-file-clear"
                      >
                        Remove
                      </button>
                    </div>
                  ) : (
                    <label
                      className="flex items-center gap-2 bg-white border border-rule border-dashed rounded-sm px-3 py-2 cursor-pointer hover:border-terracotta hover:bg-paper-warm/30 transition-colors"
                      data-testid="import-file-picker"
                    >
                      <Upload className="w-4 h-4 text-ink-mute" />
                      <span className="text-sm text-ink-soft">Choose a .docx, .md, or .txt file…</span>
                      <input
                        type="file"
                        accept=".docx,.md,.markdown,.txt"
                        className="hidden"
                        onChange={(e) => {
                          const f = e.target.files?.[0];
                          if (f) setImportFile(f);
                          e.target.value = '';
                        }}
                        data-testid="import-file-input"
                      />
                    </label>
                  )}
                  <p className="text-[10px] text-ink-mute leading-relaxed">
                    Splits your text across pages (one page per page-break or paragraph). Add illustrations after.
                  </p>
                </div>
              </div>
              <DialogFooter>
                <Button
                  data-testid="confirm-create-book"
                  onClick={onCreate}
                  disabled={importing}
                  className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm"
                >
                  {importing ? 'Importing…' : importFile ? 'Import & open' : 'Begin writing'}
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
            {templates.map((t) => {
              const titleFont = t.text_presets?.title?.font_family || 'Playfair Display';
              return (
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
                <span
                  data-testid={`template-typography-${t.id}`}
                  className="text-ink leading-none px-1.5 select-none"
                  style={{ fontFamily: titleFont, fontSize: 22 }}
                  title={`Title font: ${titleFont}`}
                >
                  Aa
                </span>
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
              );
            })}
          </div>
        </section>
      )}

      {/* Library */}
      <section className="max-w-6xl mx-auto px-8 pb-24" data-testid="books-library">
        {!loading && books.length > 0 && (
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
            <div className="relative w-full sm:max-w-xs">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by title or author…"
                data-testid="library-search-input"
                className="pl-8 h-9 bg-paper border-rule rounded-sm text-sm focus-visible:ring-1 focus-visible:ring-terracotta"
              />
            </div>
            <div className="flex items-center gap-2">
              <span className="label-caps">Sort</span>
              <div className="flex items-center bg-paper border border-rule rounded-sm p-0.5" data-testid="library-sort-toggle">
                <button
                  type="button"
                  onClick={() => setSortBy('updated')}
                  data-testid="sort-by-updated"
                  className={`h-7 px-2.5 text-xs tracking-wide rounded-sm transition-colors ${sortBy === 'updated' ? 'bg-ink text-paper' : 'text-ink-soft hover:bg-desk'}`}
                >
                  Recently edited
                </button>
                <button
                  type="button"
                  onClick={() => setSortBy('created')}
                  data-testid="sort-by-created"
                  className={`h-7 px-2.5 text-xs tracking-wide rounded-sm transition-colors ${sortBy === 'created' ? 'bg-ink text-paper' : 'text-ink-soft hover:bg-desk'}`}
                >
                  Created
                </button>
              </div>
            </div>
          </div>
        )}
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
          (() => {
            const q = search.trim().toLowerCase();
            const filtered = q
              ? books.filter((b) =>
                  (b.title || '').toLowerCase().includes(q) ||
                  (b.author || '').toLowerCase().includes(q)
                )
              : books;
            const sorted = [...filtered].sort((a, b) => {
              const af = sortBy === 'created' ? a.created_at : a.updated_at;
              const bf = sortBy === 'created' ? b.created_at : b.updated_at;
              return (bf || '').localeCompare(af || '');
            });
            if (sorted.length === 0) {
              return (
                <div className="text-ink-mute italic" data-testid="empty-search-result">
                  No books match "{search}".
                </div>
              );
            }
            return (
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-4">
                {sorted.map((b) => (
                  <BookCard
                    key={b.id}
                    book={b}
                    onOpen={() => navigate(`/editor/${b.id}`)}
                    onDelete={() => onDelete(b.id)}
                    onDuplicate={() => onDuplicate(b.id)}
                    onEditDetails={(patch) => onEditDetails(b.id, patch)}
                  />
                ))}
              </div>
            );
          })()
        )}
      </section>
    </div>
  );
}

function BookCard({ book, onOpen, onDelete, onDuplicate, onEditDetails }) {
  const cover = book.cover_image_url ? fileUrl(book.cover_image_url) : null;
  const [editOpen, setEditOpen] = useState(false);
  const [editTitle, setEditTitle] = useState(book.title || '');
  const [editAuthor, setEditAuthor] = useState(book.author || '');
  const [saving, setSaving] = useState(false);
  // Reset form whenever the dialog opens with the current values.
  useEffect(() => {
    if (editOpen) {
      setEditTitle(book.title || '');
      setEditAuthor(book.author || '');
    }
  }, [editOpen, book.title, book.author]);
  const submitEdit = async () => {
    if (!editTitle.trim()) {
      toast.error('Title cannot be empty');
      return;
    }
    setSaving(true);
    try {
      await onEditDetails?.({ title: editTitle, author: editAuthor });
      setEditOpen(false);
    } catch (e) {
      // toast already shown by parent
    } finally {
      setSaving(false);
    }
  };
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
          // object-contain so the entire cover page is visible (no crop).
          // The desk-colored background fills any letterboxed gutters.
          <img src={cover} alt={book.title} className="absolute inset-0 w-full h-full object-contain" />
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center p-3 bg-gradient-to-br from-paper to-desk">
            <FileText className="w-6 h-6 text-ink-mute mb-1.5" strokeWidth={1.25} />
            <p className="font-serif text-sm text-ink text-center leading-tight line-clamp-3">{book.title}</p>
            {book.author ? <p className="text-ink-mute mt-1 text-[10px]">by {book.author}</p> : null}
          </div>
        )}
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-ink/85 to-transparent text-paper px-2 py-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
          <p className="text-[11px] text-paper/80">{book.page_count} pages · {book.page_size}</p>
        </div>
      </button>
      <div className="flex items-center justify-between px-2.5 py-2 border-t border-rule gap-1">
        <div className="min-w-0 flex-1">
          <p className="font-serif text-sm text-ink truncate leading-tight">{book.title}</p>
          <p className="text-[10px] text-ink-mute truncate">{book.author || 'Anonymous'}</p>
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          <Dialog open={editOpen} onOpenChange={setEditOpen}>
            <DialogTrigger asChild>
              <button
                type="button"
                data-testid={`edit-book-${book.id}`}
                className="p-1 text-ink-mute hover:text-ink rounded-sm"
                title="Edit title and author"
              >
                <Pencil className="w-3 h-3" />
              </button>
            </DialogTrigger>
            <DialogContent className="bg-paper border-rule rounded-sm max-w-md">
              <DialogHeader>
                <DialogTitle className="font-serif text-2xl">Edit book details</DialogTitle>
              </DialogHeader>
              <div className="space-y-4 py-2">
                <div className="space-y-1.5">
                  <Label htmlFor={`edit-title-${book.id}`}>Title</Label>
                  <Input
                    id={`edit-title-${book.id}`}
                    data-testid={`edit-book-title-input-${book.id}`}
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') submitEdit(); }}
                    autoFocus
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`edit-author-${book.id}`}>Author</Label>
                  <Input
                    id={`edit-author-${book.id}`}
                    data-testid={`edit-book-author-input-${book.id}`}
                    value={editAuthor}
                    onChange={(e) => setEditAuthor(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') submitEdit(); }}
                    placeholder="Anonymous"
                  />
                </div>
              </div>
              <DialogFooter>
                <Button
                  variant="outline"
                  onClick={() => setEditOpen(false)}
                  data-testid={`cancel-edit-book-${book.id}`}
                  className="rounded-sm"
                  disabled={saving}
                >
                  Cancel
                </Button>
                <Button
                  onClick={submitEdit}
                  data-testid={`save-edit-book-${book.id}`}
                  className="bg-ink hover:bg-ink-soft text-paper rounded-sm"
                  disabled={saving}
                >
                  {saving ? 'Saving…' : 'Save'}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          <button
            type="button"
            onClick={onDuplicate}
            data-testid={`duplicate-book-${book.id}`}
            className="p-1 text-ink-mute hover:text-ink rounded-sm"
            title="Duplicate"
          >
            <Copy className="w-3 h-3" />
          </button>
          <AlertDialog>
          <AlertDialogTrigger asChild>
            <button
              data-testid={`delete-book-${book.id}`}
              className="p-1 text-ink-mute hover:text-terracotta rounded-sm"
              title="Delete"
            >
              <Trash2 className="w-3 h-3" />
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
    </div>
  );
}
