import axios from 'axios';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

export const api = axios.create({ baseURL: API });

export const listBooks = () => api.get('/books').then((r) => r.data);
export const getBook = (id) => api.get(`/books/${id}`).then((r) => r.data);
export const createBook = (data) => api.post('/books', data).then((r) => r.data);
export const importBook = async ({ file, title, author, page_size }) => {
  const form = new FormData();
  form.append('file', file);
  if (title) form.append('title', title);
  if (author) form.append('author', author);
  if (page_size) form.append('page_size', page_size);
  const res = await api.post('/books/import', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
};
export const duplicateBook = (id) => api.post(`/books/${id}/duplicate`).then((r) => r.data);
export const updateBook = (id, data) => api.put(`/books/${id}`, data).then((r) => r.data);
export const deleteBook = (id) => api.delete(`/books/${id}`).then((r) => r.data);

export const uploadImage = async (file, bookId) => {
  const form = new FormData();
  form.append('file', file);
  if (bookId) form.append('book_id', bookId);
  const res = await api.post('/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
};

export const listAssets = (bookId) =>
  api.get('/assets', { params: bookId ? { book_id: bookId } : undefined }).then((r) => r.data);
export const deleteAsset = (id) => api.delete(`/assets/${id}`).then((r) => r.data);

export const listTemplates = () => api.get('/templates').then((r) => r.data);
export const createTemplate = (data) => api.post('/templates', data).then((r) => r.data);
export const deleteTemplate = (id) => api.delete(`/templates/${id}`).then((r) => r.data);

// Audit log
export const listRevisions = (bookId) =>
  api.get(`/books/${bookId}/revisions`).then((r) => r.data);
export const restoreRevision = (bookId, revId) =>
  api.post(`/books/${bookId}/revisions/${revId}/restore`).then((r) => r.data);

export const fileUrl = (urlOrPath) => {
  if (!urlOrPath) return null;
  if (urlOrPath.startsWith('http://') || urlOrPath.startsWith('https://')) return urlOrPath;
  if (urlOrPath.startsWith('/api/')) return `${BACKEND_URL}${urlOrPath}`;
  return `${API}/files/${urlOrPath}`;
};
