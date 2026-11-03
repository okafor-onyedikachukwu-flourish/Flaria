import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';

import { useAuthStore } from '@/store/useAuthStore';
import type { Role } from '@/types';

interface Props {
  children: ReactNode;
  roles?: Role[];
}

export function ProtectedRoute({ children, roles }: Props) {
  const status = useAuthStore((s) => s.status);
  const user = useAuthStore((s) => s.user);
  const location = useLocation();

  if (status === 'unknown') {
    return (
      <div className="chart-paper flex min-h-dvh items-center justify-center" role="status" aria-live="polite">
        <p className="text-step-0 text-ink-soft">Checking your session…</p>
      </div>
    );
  }
  if (status !== 'authenticated' || !user) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  if (roles && !roles.includes(user.role)) {
    return (
      <div className="chart-paper flex min-h-dvh items-center justify-center p-6">
        <div className="panel max-w-md p-6">
          <h1 className="text-step-2 font-semibold">This page needs a different role</h1>
          <p className="mt-2 text-ink-soft">Your account is a {user.role.toLowerCase()} account. Ask an administrator for access.</p>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
