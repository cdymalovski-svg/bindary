import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft, ShieldCheck, UserPlus, Trash2, KeyRound, Loader2, AlertCircle, Lock, Check,
} from 'lucide-react';
import { toast } from 'sonner';
import { useAuth } from '@/auth/AuthContext';
import {
  listUsers, createUser, deleteUser, adminResetPassword, changeMyPassword,
} from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog, DialogTrigger, DialogContent, DialogHeader, DialogTitle,
  DialogDescription, DialogFooter,
} from '@/components/ui/dialog';
import {
  AlertDialog, AlertDialogTrigger, AlertDialogContent, AlertDialogHeader,
  AlertDialogTitle, AlertDialogDescription, AlertDialogFooter,
  AlertDialogAction, AlertDialogCancel,
} from '@/components/ui/alert-dialog';
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from '@/components/ui/select';

/**
 * Admin panel: change-my-password card + user-management table.
 * Only reachable when the signed-in user has role === "admin"; the
 * routing layer enforces that.
 */
export default function Admin() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshTick, setRefreshTick] = useState(0);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setUsers(await listUsers());
    } catch (e) {
      toast.error(`Could not load users: ${e?.response?.data?.detail || e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh, refreshTick]);

  return (
    <div className="min-h-screen bg-desk" data-testid="admin-page">
      <header className="h-14 border-b border-rule bg-paper flex items-center px-4 gap-3">
        <Button
          variant="ghost"
          onClick={() => navigate('/')}
          className="rounded-sm text-ink hover:bg-desk px-2"
          data-testid="admin-back-to-library"
        >
          <ArrowLeft className="w-4 h-4 mr-1" /> Library
        </Button>
        <div className="flex items-center gap-2 ml-2">
          <ShieldCheck className="w-5 h-5 text-terracotta" strokeWidth={1.5} />
          <h1 className="font-serif text-2xl text-ink">Admin</h1>
        </div>
        <div className="ml-auto text-xs text-ink-mute">
          Signed in as <span className="text-ink">{user?.email}</span>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-10 space-y-10">
        <ChangeMyPasswordCard />
        <UsersTable
          users={users}
          loading={loading}
          currentUserId={user?.id}
          onChange={() => setRefreshTick((n) => n + 1)}
        />
      </main>
    </div>
  );
}

// -------------------------------------------------------------------------- //
// Change-my-password card
// -------------------------------------------------------------------------- //

function ChangeMyPasswordCard() {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  const onSubmit = async (e) => {
    e.preventDefault();
    if (next.length < 6) {
      toast.error('New password must be at least 6 characters');
      return;
    }
    if (next !== confirmPw) {
      toast.error('Confirmation does not match');
      return;
    }
    setSubmitting(true);
    try {
      await changeMyPassword(current, next);
      setCurrent(''); setNext(''); setConfirmPw('');
      setDone(true);
      setTimeout(() => setDone(false), 4000);
      toast.success('Password updated');
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Could not change password');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section
      className="bg-paper border border-rule rounded-sm p-6 shadow-sm"
      data-testid="change-password-card"
    >
      <header className="flex items-center gap-2 mb-4">
        <KeyRound className="w-4 h-4 text-ink-soft" strokeWidth={1.5} />
        <h2 className="font-serif text-xl text-ink">Change my password</h2>
      </header>
      <form onSubmit={onSubmit} className="grid sm:grid-cols-3 gap-4 max-w-2xl">
        <div className="sm:col-span-3 space-y-1.5">
          <Label htmlFor="cp-current" className="text-xs uppercase tracking-wider text-ink-soft">
            Current password
          </Label>
          <Input
            id="cp-current"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            data-testid="change-password-current"
            className="bg-white border-rule rounded-sm h-10"
            required
          />
        </div>
        <div className="space-y-1.5 sm:col-span-1">
          <Label htmlFor="cp-new" className="text-xs uppercase tracking-wider text-ink-soft">
            New password
          </Label>
          <Input
            id="cp-new"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            data-testid="change-password-new"
            className="bg-white border-rule rounded-sm h-10"
            minLength={6}
            required
          />
        </div>
        <div className="space-y-1.5 sm:col-span-1">
          <Label htmlFor="cp-confirm" className="text-xs uppercase tracking-wider text-ink-soft">
            Confirm new password
          </Label>
          <Input
            id="cp-confirm"
            type="password"
            autoComplete="new-password"
            value={confirmPw}
            onChange={(e) => setConfirmPw(e.target.value)}
            data-testid="change-password-confirm"
            className="bg-white border-rule rounded-sm h-10"
            minLength={6}
            required
          />
        </div>
        <div className="sm:col-span-1 flex items-end">
          <Button
            type="submit"
            disabled={submitting || !current || !next || !confirmPw}
            data-testid="change-password-submit"
            className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm h-10 w-full"
          >
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : done ? (
              <span className="inline-flex items-center gap-1.5"><Check className="w-4 h-4" /> Updated</span>
            ) : 'Update password'}
          </Button>
        </div>
      </form>
    </section>
  );
}

// -------------------------------------------------------------------------- //
// User table
// -------------------------------------------------------------------------- //

function UsersTable({ users, loading, currentUserId, onChange }) {
  return (
    <section
      className="bg-paper border border-rule rounded-sm shadow-sm"
      data-testid="users-section"
    >
      <header className="flex items-center justify-between px-6 py-4 border-b border-rule">
        <div>
          <h2 className="font-serif text-xl text-ink">Users</h2>
          <p className="text-xs text-ink-mute">
            {loading ? 'Loading…' : `${users.length} account${users.length === 1 ? '' : 's'}`}
          </p>
        </div>
        <CreateUserDialog onCreated={onChange} />
      </header>
      <div className="divide-y divide-rule" data-testid="users-list">
        {users.map((u) => (
          <UserRow
            key={u.id}
            user={u}
            isSelf={u.id === currentUserId}
            onChanged={onChange}
          />
        ))}
        {!loading && users.length === 0 && (
          <p className="px-6 py-8 text-sm text-ink-mute italic">No users yet.</p>
        )}
      </div>
    </section>
  );
}

function UserRow({ user, isSelf, onChanged }) {
  const isAdmin = user.role === 'admin';
  return (
    <div
      className="flex items-center gap-3 px-6 py-3"
      data-testid={`user-row-${user.id}`}
    >
      <div className="w-10 h-10 rounded-full bg-desk border border-rule flex items-center justify-center shrink-0">
        {isAdmin
          ? <ShieldCheck className="w-4 h-4 text-terracotta" strokeWidth={1.5} />
          : <Lock className="w-4 h-4 text-ink-soft" strokeWidth={1.5} />}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="font-medium text-ink truncate">{user.email}</p>
          {isAdmin && (
            <span className="text-[10px] px-2 py-0.5 bg-terracotta/10 border border-terracotta/40 rounded-sm text-terracotta uppercase tracking-wider">
              Admin
            </span>
          )}
          {isSelf && (
            <span className="text-[10px] px-2 py-0.5 bg-ink/5 border border-rule rounded-sm text-ink-soft uppercase tracking-wider">
              You
            </span>
          )}
        </div>
        {user.name && <p className="text-xs text-ink-mute truncate">{user.name}</p>}
      </div>
      <ResetPasswordDialog user={user} />
      <DeleteUserButton user={user} isSelf={isSelf} onDeleted={onChanged} />
    </div>
  );
}

function CreateUserDialog({ onCreated }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('user');
  const [submitting, setSubmitting] = useState(false);

  const reset = () => { setEmail(''); setName(''); setPassword(''); setRole('user'); };

  const onSubmit = async (e) => {
    e.preventDefault();
    if (password.length < 6) {
      toast.error('Password must be at least 6 characters'); return;
    }
    setSubmitting(true);
    try {
      await createUser({ email: email.trim().toLowerCase(), name: name || undefined, password, role });
      toast.success(`Created ${email}`);
      setOpen(false);
      reset();
      onCreated?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Could not create user');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) reset(); }}>
      <DialogTrigger asChild>
        <Button
          data-testid="create-user-button"
          className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm"
        >
          <UserPlus className="w-4 h-4 mr-2" /> Add user
        </Button>
      </DialogTrigger>
      <DialogContent className="bg-paper border-rule rounded-sm" data-testid="create-user-dialog">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">Add a new user</DialogTitle>
          <DialogDescription>
            They'll be able to sign in immediately with the password you set here.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="cu-email">Email</Label>
            <Input
              id="cu-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              data-testid="create-user-email"
              className="bg-white border-rule rounded-sm"
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="cu-name">Name <span className="text-ink-mute text-xs">(optional)</span></Label>
            <Input
              id="cu-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="create-user-name"
              className="bg-white border-rule rounded-sm"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="cu-password">Password</Label>
              <Input
                id="cu-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                data-testid="create-user-password"
                className="bg-white border-rule rounded-sm"
                minLength={6}
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cu-role">Role</Label>
              <Select value={role} onValueChange={setRole}>
                <SelectTrigger
                  id="cu-role"
                  data-testid="create-user-role"
                  className="bg-white border-rule rounded-sm h-10"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="user" data-testid="create-user-role-user">User</SelectItem>
                  <SelectItem value="admin" data-testid="create-user-role-admin">Admin</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button type="submit" disabled={submitting} data-testid="create-user-submit" className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm">
              {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Create user'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ResetPasswordDialog({ user }) {
  const [open, setOpen] = useState(false);
  const [newPw, setNewPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (newPw.length < 6) { toast.error('Min 6 characters'); return; }
    if (newPw !== confirmPw) { toast.error('Confirmation does not match'); return; }
    setSubmitting(true);
    try {
      await adminResetPassword(user.id, newPw);
      toast.success(`Reset password for ${user.email}`);
      setOpen(false);
      setNewPw(''); setConfirmPw('');
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Could not reset password');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) { setNewPw(''); setConfirmPw(''); } }}>
      <DialogTrigger asChild>
        <button
          type="button"
          data-testid={`reset-password-${user.id}`}
          title={`Reset ${user.email}'s password`}
          className="p-2 text-ink-mute hover:text-ink rounded-sm"
        >
          <KeyRound className="w-4 h-4" />
        </button>
      </DialogTrigger>
      <DialogContent className="bg-paper border-rule rounded-sm" data-testid={`reset-password-dialog-${user.id}`}>
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">Reset password</DialogTitle>
          <DialogDescription>
            Set a new password for <span className="text-ink font-medium">{user.email}</span>.
            They won't need to enter the old one.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor={`rp-new-${user.id}`}>New password</Label>
            <Input
              id={`rp-new-${user.id}`}
              type="password"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              data-testid={`reset-password-new-${user.id}`}
              className="bg-white border-rule rounded-sm"
              minLength={6}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`rp-conf-${user.id}`}>Confirm</Label>
            <Input
              id={`rp-conf-${user.id}`}
              type="password"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              data-testid={`reset-password-confirm-${user.id}`}
              className="bg-white border-rule rounded-sm"
              minLength={6}
              required
            />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={submitting} data-testid={`reset-password-submit-${user.id}`} className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm">
              {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Reset password'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteUserButton({ user, isSelf, onDeleted }) {
  const [working, setWorking] = useState(false);
  const handleDelete = async () => {
    setWorking(true);
    try {
      await deleteUser(user.id);
      toast.success(`Removed ${user.email}`);
      onDeleted?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Could not delete user');
    } finally {
      setWorking(false);
    }
  };
  // Self-delete is impossible — disable the button rather than show an error.
  if (isSelf) {
    return (
      <button
        disabled
        title="You can't delete your own account"
        data-testid={`delete-user-disabled-${user.id}`}
        className="p-2 text-ink-mute/40 rounded-sm cursor-not-allowed"
      >
        <Trash2 className="w-4 h-4" />
      </button>
    );
  }
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <button
          type="button"
          data-testid={`delete-user-${user.id}`}
          title={`Delete ${user.email}`}
          className="p-2 text-ink-mute hover:text-terracotta rounded-sm"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </AlertDialogTrigger>
      <AlertDialogContent className="bg-paper border-rule rounded-sm" data-testid={`delete-user-confirm-${user.id}`}>
        <AlertDialogHeader>
          <AlertDialogTitle className="font-serif text-2xl">Delete user?</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="inline-flex items-center gap-2 text-terracotta">
              <AlertCircle className="w-4 h-4" /> This is permanent.
            </span>
            <br />
            They'll be signed out everywhere and their account record removed.
            Their books and assets stay in the database.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleDelete}
            disabled={working}
            data-testid={`delete-user-confirm-action-${user.id}`}
            className="bg-terracotta hover:bg-terracotta-dark text-paper rounded-sm"
          >
            {working ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Delete'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
