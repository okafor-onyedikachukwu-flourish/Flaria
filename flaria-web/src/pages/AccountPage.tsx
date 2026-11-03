import { ArrowLeft, CircleCheck, LoaderCircle, LogOut, ShieldCheck } from 'lucide-react';
import { type FormEvent, useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { BrandMark } from '@/components/BrandMark';
import { PasswordField } from '@/components/PasswordField';
import { PASSWORD_HINT, passwordProblem } from '@/lib/password';
import { api, errorMessage } from '@/services/api';
import { useAuthStore } from '@/store/useAuthStore';
import type { Role } from '@/types';
import { useDocumentTitle } from '@/lib/useDocumentTitle';

interface Account {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  created_at: string;
  last_login_at: string | null;
  password_changed_at: string | null;
  signed_in_devices: number;
}

const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'long', timeStyle: 'short' });
const when = (iso: string | null, fallback: string) => (iso ? fmt.format(new Date(iso)) : fallback);

export function AccountPage() {
  useDocumentTitle('Account: Flaria');
  const user = useAuthStore((s) => s.user)!;
  const changePassword = useAuthStore((s) => s.changePassword);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const home = user.role === 'ADMIN' ? '/admin' : '/app';

  const [account, setAccount] = useState<Account | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [touched, setTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [confirmingSignOut, setConfirmingSignOut] = useState(false);

  const load = useCallback(() => {
    api.get<Account>('/auth/account').then(({ data }) => setAccount(data)).catch((err) => setLoadError(errorMessage(err)));
  }, []);
  useEffect(load, [load]);

  const problem = passwordProblem(next);
  const mismatch = confirm.length > 0 && confirm !== next ? 'The two new passwords don’t match.' : null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    setSaved(false);
    if (!current || problem || mismatch || !confirm) return;
    setSaving(true);
    const message = await changePassword(current, next);
    setSaving(false);
    if (message) {
      setError(message);
      return;
    }
    setError(null);
    setSaved(true);
    setTouched(false);
    setCurrent('');
    setNext('');
    setConfirm('');
    load();
  };

  const signOutEverywhere = async () => {
    try {
      await api.post('/auth/logout-all');
    } finally {
      await logout();
      navigate('/login', { replace: true });
    }
  };

  return (
    <div className="min-h-dvh bg-paper">
      <header className="border-b border-rule bg-paper/90">
        <div className="mx-auto flex max-w-4xl items-center justify-between gap-3 px-5 py-3">
          <BrandMark to={home} />
          <Link to={home} className="btn-quiet px-3 py-2 text-step--1">
            <ArrowLeft size={16} aria-hidden /> {user.role === 'ADMIN' ? 'Admin console' : 'Live session'}
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-4xl space-y-6 px-5 py-8">
        <div>
          <h1 className="text-step-3 font-semibold">Account</h1>
          <p className="mt-1 text-step--1 text-ink-soft">Your sign-in details and password.</p>
        </div>

        <section className="panel bg-white" aria-labelledby="profile-heading">
          <h2 id="profile-heading" className="border-b border-rule px-5 py-3 font-sans text-step-0 font-semibold">Profile</h2>
          {loadError && <p role="alert" className="px-5 py-4 text-step--1 text-elevated">{loadError}</p>}
          <dl className="grid gap-x-8 gap-y-4 px-5 py-5 text-step--1 sm:grid-cols-2">
            {[
              ['Name', user.full_name],
              ['Email', user.email],
              ['Role', user.role.charAt(0) + user.role.slice(1).toLowerCase()],
              ['Member since', account ? when(account.created_at, '–') : '…'],
              ['Last sign-in', account ? when(account.last_login_at, 'Not recorded') : '…'],
              ['Password last changed', account ? when(account.password_changed_at, 'Not changed since the account was created') : '…'],
            ].map(([k, v]) => (
              <div key={k}>
                <dt className="text-ink-soft">{k}</dt>
                <dd className="mt-0.5 font-medium text-ink">{v}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section className="panel bg-white" aria-labelledby="password-heading">
          <div className="border-b border-rule px-5 py-3">
            <h2 id="password-heading" className="font-sans text-step-0 font-semibold">Change password</h2>
            <p className="text-step--1 text-ink-soft">Changing it signs you out on every other device. You stay signed in here.</p>
          </div>
          <form onSubmit={submit} className="grid gap-5 px-5 py-5 md:grid-cols-2" noValidate>
            <div className="md:col-span-2 md:max-w-sm">
              <PasswordField label="Current password" autoComplete="current-password" value={current} onChange={setCurrent}
                error={touched && !current ? 'Enter your current password.' : null} />
            </div>
            <PasswordField label="New password" autoComplete="new-password" value={next} onChange={setNext}
              hint={PASSWORD_HINT} error={touched ? problem : null} />
            <PasswordField label="Confirm new password" autoComplete="new-password" value={confirm} onChange={setConfirm}
              error={touched && !confirm ? 'Type the new password again.' : mismatch} />
            <div className="space-y-3 md:col-span-2" aria-live="polite">
              {error && <p role="alert" className="rounded-md border border-elevated/30 bg-elevated-wash px-3 py-2 text-step--1 text-elevated">{error}</p>}
              {saved && (
                <p className="flex items-center gap-2 rounded-md border border-stable/30 bg-stable-wash px-3 py-2 text-step--1 text-stable">
                  <CircleCheck size={16} aria-hidden /> Password changed. Other devices were signed out, and we emailed you a confirmation.
                </p>
              )}
              <div className="flex flex-wrap items-center gap-4">
                <button type="submit" className="btn-primary" disabled={saving}>
                  {saving && <LoaderCircle size={18} className="animate-spin" aria-hidden />}
                  {saving ? 'Changing password…' : 'Change password'}
                </button>
                <Link to="/forgot-password" className="text-step--1 font-medium text-scrub-deep underline-offset-2 hover:underline">
                  Forgot your current password?
                </Link>
              </div>
            </div>
          </form>
        </section>

        <section className="panel bg-white" aria-labelledby="devices-heading">
          <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
            <div className="flex items-start gap-3">
              <ShieldCheck size={20} className="mt-0.5 text-scrub" aria-hidden />
              <div>
                <h2 id="devices-heading" className="font-sans text-step-0 font-semibold">Signed-in devices</h2>
                <p className="text-step--1 text-ink-soft">
                  {account ? `${account.signed_in_devices} device${account.signed_in_devices === 1 ? '' : 's'} currently signed in, including this one.` : 'Loading…'}
                </p>
              </div>
            </div>
            {confirmingSignOut ? (
              <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Confirm signing out everywhere">
                <span className="text-step--1 text-ink-soft">Sign out of every device, including this one?</span>
                <button type="button" className="btn-quiet py-2 text-step--1" onClick={() => setConfirmingSignOut(false)}>Cancel</button>
                <button type="button" className="btn-danger py-2 text-step--1" onClick={() => void signOutEverywhere()}>Sign out everywhere</button>
              </div>
            ) : (
              <button type="button" className="btn-quiet py-2 text-step--1" onClick={() => setConfirmingSignOut(true)}>
                <LogOut size={16} aria-hidden /> Sign out everywhere
              </button>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}
