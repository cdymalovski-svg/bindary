import axios from 'axios';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

const TOKEN_KEY = 'bindery_token';

export const getStoredToken = () => {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
};
export const setStoredToken = (t) => {
  try { if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY); } catch {}
};

export const api = axios.create({ baseURL: API });

// Attach Bearer token to every outbound request when present.
api.interceptors.request.use((config) => {
  const t = getStoredToken();
  if (t) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${t}`;
  }
  return config;
});

// Drop the token on 401 so the UI bumps the user back to the login screen.
// We dispatch a CustomEvent so the AuthContext can react without coupling
// to axios. (Plain reload would lose unsaved edits.)
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      const path = err?.config?.url || '';
      // Don't nuke the token on the login endpoint itself — its 401 is
      // "wrong password", not an expired session.
      if (!path.includes('/auth/login')) {
        setStoredToken(null);
        window.dispatchEvent(new CustomEvent('bindery:auth-expired'));
      }
    }
    return Promise.reject(err);
  },
);

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
export const replaceAsset = async (id, file) => {
  const form = new FormData();
  form.append('file', file);
  const res = await api.post(`/assets/${id}/replace`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
};

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

// --- Auth ---
export const authLogin = async (email, password) => {
  const { data } = await api.post('/auth/login', { email, password });
  if (data?.access_token) setStoredToken(data.access_token);
  return data;
};
export const authMe = () => api.get('/auth/me').then((r) => r.data);
export const authLogout = async () => {
  try { await api.post('/auth/logout'); } catch {}
  setStoredToken(null);
};

// --- Admin user management ---
export const listUsers = () => api.get('/auth/users').then((r) => r.data);
export const createUser = (payload) => api.post('/auth/users', payload).then((r) => r.data);
export const deleteUser = (userId) => api.delete(`/auth/users/${userId}`).then((r) => r.data);
export const adminResetPassword = (userId, newPassword) =>
  api.post(`/auth/users/${userId}/reset-password`, { new_password: newPassword }).then((r) => r.data);
export const changeMyPassword = (currentPassword, newPassword) =>
  api.post('/auth/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  }).then((r) => r.data);
