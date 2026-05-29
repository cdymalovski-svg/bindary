import { useState } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { Loader2, BookOpen, AlertCircle } from 'lucide-react';
import { useAuth } from '@/auth/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

/**
 * FastAPI's 422 validation responses use `detail: [{msg, ...}]` (a list of
 * objects) rather than a string. Rendering that directly in JSX crashes
 * React. This normaliser is the same one used in the auth playbook —
 * keeps the login screen resilient to ANY shape the backend returns.
 */
function formatErrorDetail(detail) {
  if (detail == null) return 'Something went wrong. Please try again.';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => (e && typeof e.msg === 'string' ? e.msg : JSON.stringify(e)))
      .filter(Boolean)
      .join(' · ');
  }
  if (detail && typeof detail.msg === 'string') return detail.msg;
  return String(detail);
}

export default function Login() {
  const { user, signIn } = useAuth();
  const location = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  // Already signed in? Bounce wherever we were headed (default: dashboard).
  if (user) {
    const dest = location.state?.from?.pathname || '/';
    return <Navigate to={dest} replace />;
  }

  const onSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      await signIn(email.trim().toLowerCase(), password);
      // Navigation handled by the `if (user)` branch above on next render.
    } catch (err) {
      setError(formatErrorDetail(err?.response?.data?.detail) || err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-desk px-6">
      <div
        className="w-full max-w-md bg-paper border border-rule rounded-sm shadow-xl px-8 py-10"
        data-testid="login-card"
      >
        <div className="flex items-center justify-center mb-6">
          <div className="w-12 h-12 rounded-full bg-terracotta/10 border border-terracotta/40 flex items-center justify-center">
            <BookOpen className="w-6 h-6 text-terracotta" strokeWidth={1.5} />
          </div>
        </div>
        <h1 className="font-serif text-3xl text-ink text-center mb-1">Bindery</h1>
        <p className="text-sm text-ink-mute text-center mb-8">
          Sign in to open the studio.
        </p>

        <form onSubmit={onSubmit} className="space-y-4" data-testid="login-form">
          <div className="space-y-1.5">
            <Label htmlFor="login-email" className="text-xs text-ink-soft uppercase tracking-wider">
              Email
            </Label>
            <Input
              id="login-email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              data-testid="login-email-input"
              className="bg-white border-rule rounded-sm h-10"
              placeholder="you@example.com"
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="login-password" className="text-xs text-ink-soft uppercase tracking-wider">
              Password
            </Label>
            <Input
              id="login-password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              data-testid="login-password-input"
              className="bg-white border-rule rounded-sm h-10"
              placeholder="••••••••"
            />
          </div>

          {error && (
            <div
              className="flex items-start gap-2 px-3 py-2 bg-terracotta/10 border border-terracotta/40 rounded-sm text-sm text-terracotta"
              data-testid="login-error"
            >
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <span className="leading-relaxed">{error}</span>
            </div>
          )}

          <Button
            type="submit"
            disabled={submitting || !email || !password}
            data-testid="login-submit-button"
            className="w-full h-10 bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm"
          >
            {submitting ? (
              <span className="inline-flex items-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin" /> Signing in…
              </span>
            ) : (
              'Sign in'
            )}
          </Button>
        </form>

        <p className="text-[11px] text-ink-mute text-center mt-8 leading-relaxed">
          Bindery is a private studio. If you've forgotten your password, ask the
          administrator to update it from the server configuration.
        </p>
      </div>
    </div>
  );
}
