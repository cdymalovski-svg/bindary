import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '@/auth/AuthContext';

/**
 * Gates a subtree behind an authenticated session.
 *  - While the session is still being validated → render nothing (no flash).
 *  - Signed out → redirect to /login, preserving where we wanted to go.
 *  - Signed in → render the protected subtree.
 */
export default function ProtectedRoute({ children }) {
  const { user } = useAuth();
  const location = useLocation();

  if (user === undefined) {
    return (
      <div
        className="min-h-screen flex items-center justify-center bg-desk"
        data-testid="auth-loading"
      >
        <p className="font-serif italic text-2xl text-ink-mute">Loading…</p>
      </div>
    );
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return children;
}
