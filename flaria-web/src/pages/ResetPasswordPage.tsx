import { CircleCheck, LoaderCircle } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { Link } from 'react-router-dom';

import { BrandMark } from '@/components/BrandMark';
import { PasswordField } from '@/components/PasswordField';
import { PASSWORD_HINT, passwordProblem } from '@/lib/password';
import { authClient, errorMessage } from '@/services/api';
import { useDocumentTitle } from '@/lib/useDocumentTitle';

/** Read the token from the URL fragment once, then strip it so it can't linger in history or bookmarks. */
function takeToken(): string | null {
  const match = /(?:^|[#&])token=([A-Za-z0-9_-]{20,200})/.exec(window.location.hash);
  if (window.location.hash) window.history.replaceState(null, '', window.location.pathname);
  return match ? match[1] : null;
}

export function ResetPasswordPage() {
  useDocumentTitle('Choose a new password: Flaria');
  const [token] = useState(takeToken);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [touched, setTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const problem = passwordProblem(password);
  const mismatch = confirm.length > 0 && confirm !== password ? 'The two passwords don’t match.' : null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (problem || mismatch || !confirm) return;
    setSaving(true);
    setError(null);
    try {
      await authClient.post('/auth/password-reset/confirm', { token, new_password: password });
      setDone(true);
      setPassword('');
      setConfirm('');
    } catch (err) {
      setError(errorMessage(err, 'Your password couldn’t be reset. Try again.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="chart-paper flex min-h-dvh flex-col">
      <header className="mx-auto w-full max-w-6xl px-5 py-5"><BrandMark /></header>
      <main className="flex flex-1 items-start justify-center px-5 pb-16 pt-6 sm:items-center sm:pt-0">
        <div className="panel w-full max-w-md bg-white p-6 shadow-sm sm:p-8">
          {done ? (
            <div role="status">
              <CircleCheck size={28} className="text-stable" aria-hidden />
              <h1 className="mt-3 text-step-3 font-semibold">Password changed</h1>
              <p className="mt-2 text-step--1 text-ink-soft">
                Sign in with your new password. For your safety, every device that was signed in has been signed out.
              </p>
              <Link to="/login" className="btn-primary mt-6">Sign in</Link>
            </div>
          ) : !token ? (
            <div>
              <h1 className="text-step-3 font-semibold">This link isn’t complete</h1>
              <p className="mt-2 text-step--1 text-ink-soft">
                Open the reset link straight from your email. If you copied it, make sure you copied all of it, or request a new one.
              </p>
              <Link to="/forgot-password" className="btn-primary mt-6">Request a new link</Link>
            </div>
          ) : (
            <>
              <h1 className="text-step-3 font-semibold">Choose a new password</h1>
              <p className="mb-6 mt-2 text-step--1 text-ink-soft">This link works once. After you save, you’ll sign in with the new password.</p>
              <form onSubmit={submit} className="space-y-5" noValidate>
                <PasswordField label="New password" autoComplete="new-password" value={password} onChange={setPassword}
                  hint={PASSWORD_HINT} error={touched ? problem : null} />
                <PasswordField label="Confirm new password" autoComplete="new-password" value={confirm} onChange={setConfirm}
                  error={touched && !confirm ? 'Type the new password again.' : mismatch} />
                {error && (
                  <div role="alert" className="rounded-md border border-elevated/30 bg-elevated-wash px-3 py-2 text-step--1 text-elevated">
                    {error} <Link to="/forgot-password" className="font-medium underline underline-offset-2">Request a new link</Link>
                  </div>
                )}
                <button type="submit" className="btn-primary w-full" disabled={saving}>
                  {saving && <LoaderCircle size={18} className="animate-spin" aria-hidden />}
                  {saving ? 'Saving password…' : 'Save new password'}
                </button>
              </form>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
