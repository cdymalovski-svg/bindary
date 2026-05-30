import { useEffect } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Toaster } from 'sonner';
import Dashboard from '@/pages/Dashboard';
import Editor from '@/pages/Editor';
import Login from '@/pages/Login';
import Admin from '@/pages/Admin';
import ErrorBoundary from '@/components/ErrorBoundary';
import { AuthProvider } from '@/auth/AuthContext';
import ProtectedRoute from '@/auth/ProtectedRoute';
import '@/App.css';

export default function App() {
  useEffect(() => {
    document.title = 'Bindery — Book Studio';
  }, []);
  return (
    <BrowserRouter>
      <Toaster
        position="bottom-right"
        toastOptions={{
          style: {
            background: '#1C1B19',
            color: '#F9F6F0',
            border: '1px solid #2E2D2B',
            borderRadius: '4px',
            fontFamily: 'Outfit, sans-serif',
          },
        }}
      />
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/"
            element={(
              <ProtectedRoute>
                <Dashboard />
              </ProtectedRoute>
            )}
          />
          <Route
            path="/editor/:id"
            element={(
              <ProtectedRoute>
                <ErrorBoundary>
                  <Editor />
                </ErrorBoundary>
              </ProtectedRoute>
            )}
          />
          <Route
            path="/admin"
            element={(
              <ProtectedRoute requireAdmin>
                <ErrorBoundary>
                  <Admin />
                </ErrorBoundary>
              </ProtectedRoute>
            )}
          />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
