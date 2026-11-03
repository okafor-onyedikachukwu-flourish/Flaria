import { Eye, EyeOff, LoaderCircle } from 'lucide-react';
import { type FormEvent, useEffect, useId, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { PASSWORD_HINT, passwordProblem } from '@/lib/password';
import { useAuthStore } from '@/store/useAuthStore';

type Mode = 'login' | 'register';


export function LoginForm({ mode }: { mode: Mode }) {
  const login = useAuthStore((s) => s.login);
  const register = useAuthStore((s) => s.register);
  const error = useAuthStore((s) => s.error);
  const clearError = useAuthStore((s) => s.clearError);
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? '/app';

  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [touched, setTouched] = useState(false);
  const ids = { name: useId(), email: useId(), password: useId(), hint: useId(), error: useId() };

  useEffect(() => clearError(), [mode, clearError]);

  const localProblem = useMemo(() => (mode === 'register' ? passwordProblem(password) : null), [mode, password]);
  const isRegister = mode === 'register';
  const submitLabel = isRegister ? 'Create account' : 'Sign in';

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (localProblem) return;
    setSubmitting(true);
    const ok = isRegister ? await register(fullName.trim(), email.trim(), password) : await login(email.trim(), password);
    setSubmitting(false);
    if (ok) {
      setPassword('');
      // Admins land in the console unless they were sent here from a specific page.
      const explicit = (location.state as { from?: string } | null)?.from;
      const isAdmin = useAuthStore.getState().user?.role === 'ADMIN';
      navigate(explicit ?? (isAdmin ? '/admin' : from), { replace: true });
    }
  };

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-5" aria-describedby={error ? ids.error : undefined}>
      {isRegister && (
        <div>
          <label htmlFor={ids.name} className="mb-1.5 block text-step--1 font-medium">Full name</label>
          <input id={ids.name} className="field" autoComplete="name" required maxLength={200}
            value={fullName} onChange={(e) => setFullName(e.target.value)} />
        </div>
      )}
      <div>
        <label htmlFor={ids.email} className="mb-1.5 block text-step--1 font-medium">Email</label>
        <input id={ids.email} className="field" type="email" autoComplete="email" inputMode="email" required
          value={email} onChange={(e) => setEmail(e.target.value)} />
      </div>
      <div>
        <div className="mb-1.5 flex items-baseline justify-between gap-2">
          <label htmlFor={ids.password} className="block text-step--1 font-medium">Password</label>
          {!isRegister && (
            <Link to="/forgot-password" className="text-step--1 font-medium text-scrub-deep underline-offset-2 hover:underline">
              Forgot your password?
            </Link>
          )}
        </div>
        <div className="relative">
          <input
            id={ids.password}
            className="field pr-11"
            type={showPassword ? 'text' : 'password'}
            autoComplete={isRegister ? 'new-password' : 'current-password'}
            required
            maxLength={128}
            value={password}
            aria-invalid={touched && !!localProblem}
            aria-describedby={isRegister ? ids.hint : undefined}
            onChange={(e) => setPassword(e.target.value)}
          />
          <button
            type="button"
            onClick={() => setShowPassword((v) => !v)}
            className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-ink-soft hover:text-ink"
            aria-label={showPassword ? 'Hide password' : 'Show password'}
          >
            {showPassword ? <EyeOff size={18} aria-hidden /> : <Eye size={18} aria-hidden />}
          </button>
        </div>
        {isRegister && (
          <p id={ids.hint} className={`mt-1.5 text-step--1 ${touched && localProblem ? 'text-elevated' : 'text-ink-soft'}`}>
            {touched && localProblem ? localProblem : PASSWORD_HINT}
          </p>
        )}
      </div>

      <div aria-live="assertive">
        {error && (
          <p id={ids.error} className="rounded-md border border-elevated/30 bg-elevated-wash px-3 py-2 text-step--1 text-elevated">
            {error}
          </p>
        )}
      </div>

      <button type="submit" className="btn-primary w-full" disabled={submitting}>
        {submitting && <LoaderCircle size={18} className="animate-spin" aria-hidden />}
        {submitting ? (isRegister ? 'Creating account…' : 'Signing in…') : submitLabel}
      </button>

      <p className="text-center text-step--1 text-ink-soft">
        {isRegister ? 'Already have an account? ' : 'New to Flaria? '}
        <Link to={isRegister ? '/login' : '/register'} state={location.state} className="font-medium text-scrub-deep underline underline-offset-2">
          {isRegister ? 'Sign in' : 'Create an account'}
        </Link>
      </p>
    </form>
  );
}
