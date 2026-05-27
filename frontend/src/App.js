import { useEffect } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Toaster } from 'sonner';
import Dashboard from '@/pages/Dashboard';
import Editor from '@/pages/Editor';
import ErrorBoundary from '@/components/ErrorBoundary';
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
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route
          path="/editor/:id"
          element={(
            <ErrorBoundary>
              <Editor />
            </ErrorBoundary>
          )}
        />
      </Routes>
    </BrowserRouter>
  );
}
