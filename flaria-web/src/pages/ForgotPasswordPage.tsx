import { LoaderCircle, MailCheck } from 'lucide-react';
import { type FormEvent, useId, useState } from 'react';
import { Link } from 'react-router-dom';

import { BrandMark } from '@/components/BrandMark';
import { authClient, errorMessage } from '@/services/api';
import { useDocumentTitle } from '@/lib/useDocumentTitle';

export function ForgotPasswordPage() {
  useDocumentTitle('Reset your password: Flaria');
  const emailId = useId();
  const [email, setEmail] = useState('');
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSending(true);
    setError(null);
    try {
      await authClient.post('/auth/password-reset/request', { email: email.trim() });
      setSentTo(email.trim());
    } catch (err) {
      setError(errorMessage(err, 'The reset link couldn’t be sent. Try again.'));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="chart-paper flex min-h-dvh flex-col">
      <header className="mx-auto w-full max-w-6xl px-5 py-5"><BrandMark /></header>
      <main className="flex flex-1 items-start justify-center px-5 pb-16 pt-6 sm:items-center sm:pt-0">
        <div className="panel w-full max-w-md bg-white p-6 shadow-sm sm:p-8">
          {sentTo ? (
            <div role="status">
              <MailCheck size={28} className="text-scrub" aria-hidden />
              <h1 className="mt-3 text-step-3 font-semibold">Check your email</h1>
              <p className="mt-2 text-step--1 text-ink-soft">
                If there’s a Flaria account for <span className="font-medium text-ink">{sentTo}</span>, we’ve sent it a link to choose
                a new password. The link works once and expires in 30 minutes.
              </p>
              <p className="mt-3 text-step--1 text-ink-soft">Nothing arrived after a few minutes? Check your spam folder, then send another link.</p>
              <div className="mt-6 flex flex-wrap gap-3">
                <Link to="/login" className="btn-primary">Back to sign in</Link>
                <button type="button" className="btn-quiet" onClick={() => setSentTo(null)}>Send another link</button>
              </div>
            </div>
          ) : (
            <>
              <h1 className="text-step-3 font-semibold">Reset your password</h1>
              <p className="mb-6 mt-2 text-step--1 text-ink-soft">
                Enter the email address for your account. We’ll send you a link to choose a new password.
              </p>
              <form onSubmit={submit} className="space-y-5" noValidate>
                <div>
                  <label htmlFor={emailId} className="mb-1.5 block text-step--1 font-medium">Email</label>
                  <input id={emailId} className="field" type="email" autoComplete="email" inputMode="email" required
                    value={email} onChange={(e) => setEmail(e.target.value)} />
                </div>
                {error && <p role="alert" className="rounded-md border border-elevated/30 bg-elevated-wash px-3 py-2 text-step--1 text-elevated">{error}</p>}
                <button type="submit" className="btn-primary w-full" disabled={sending || !email.includes('@')}>
                  {sending && <LoaderCircle size={18} className="animate-spin" aria-hidden />}
                  {sending ? 'Sending link…' : 'Send reset link'}
                </button>
                <p className="text-center text-step--1 text-ink-soft">
                  Remembered it? <Link to="/login" className="font-medium text-scrub-deep underline underline-offset-2">Sign in</Link>
                </p>
              </form>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
