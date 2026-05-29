import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { authLogin, authLogout, authMe, getStoredToken } from '@/lib/api';

/**
 * Three-state auth model:
 *   user === undefined  → still checking (e.g. validating the stored token)
 *   user === null       → confirmed signed out
 *   user === <object>   → signed in
 *
 * The "undefined while checking" state matters: routing decisions made
 * before /auth/me responds would otherwise flash the login screen even for
 * already-signed-in users.
 */
const AuthCtx = createContext({
  user: undefined,
  signIn: async () => null,
  signOut: async () => null,
});

export function AuthProvider({ children }) {
  const [user, setUser] = useState(undefined);

  // Initial token validation: ask the server "is this token still good?"
  // — survives a backend restart, token expiry, or revoked admin.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!getStoredToken()) {
        if (!cancelled) setUser(null);
        return;
      }
      try {
        const me = await authMe();
        if (!cancelled) setUser(me);
      } catch {
        // 401 → interceptor already wiped the token. Just confirm signed out.
        if (!cancelled) setUser(null);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Any 401 from a protected endpoint flips us back to signed-out without
  // needing a full reload. The axios interceptor in `/lib/api.js` raises
  // this event.
  useEffect(() => {
    const onExpired = () => setUser(null);
    window.addEventListener('bindery:auth-expired', onExpired);
    return () => window.removeEventListener('bindery:auth-expired', onExpired);
  }, []);

  const signIn = useCallback(async (email, password) => {
    const u = await authLogin(email, password);
    setUser({
      id: u.id, email: u.email, name: u.name, role: u.role,
    });
    return u;
  }, []);

  const signOut = useCallback(async () => {
    await authLogout();
    setUser(null);
  }, []);

  return (
    <AuthCtx.Provider value={{ user, signIn, signOut }}>
      {children}
    </AuthCtx.Provider>
  );
}

export const useAuth = () => useContext(AuthCtx);
