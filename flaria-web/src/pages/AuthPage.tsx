import { Navigate } from 'react-router-dom';

import { BrandMark } from '@/components/BrandMark';
import { LoginForm } from '@/components/LoginForm';
import { useAuthStore } from '@/store/useAuthStore';
import { useDocumentTitle } from '@/lib/useDocumentTitle';

export function AuthPage({ mode }: { mode: 'login' | 'register' }) {
  useDocumentTitle(mode === 'register' ? 'Create a Flaria account' : 'Sign in to Flaria');
  const status = useAuthStore((s) => s.status);
  const role = useAuthStore((s) => s.user?.role);
  if (status === 'authenticated') return <Navigate to={role === 'ADMIN' ? '/admin' : '/app'} replace />;
  const isRegister = mode === 'register';

  return (
    <div className="chart-paper flex min-h-dvh flex-col">
      <header className="mx-auto w-full max-w-6xl px-5 py-5">
        <BrandMark />
      </header>
      <main className="flex flex-1 items-start justify-center px-5 pb-16 pt-6 sm:items-center sm:pt-0">
        <div className="panel w-full max-w-md bg-white p-6 shadow-sm sm:p-8">
          <h1 className="text-step-3 font-semibold">{isRegister ? 'Create your account' : 'Sign in'}</h1>
          <p className="mb-6 mt-2 text-step--1 text-ink-soft">
            {isRegister
              ? 'New accounts are patient accounts. An administrator grants clinician access.'
              : 'Use the email and password for your Flaria account.'}
          </p>
          {status === 'unknown' ? <p className="text-ink-soft" role="status">Checking your session…</p> : <LoginForm mode={mode} />}
        </div>
      </main>
    </div>
  );
}
