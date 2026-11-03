import { lazy, Suspense, useEffect } from 'react';
import { Route, Routes } from 'react-router-dom';

import { ProtectedRoute } from '@/components/ProtectedRoute';
import { AccountPage } from '@/pages/AccountPage';
import { AuthPage } from '@/pages/AuthPage';
import { ForgotPasswordPage } from '@/pages/ForgotPasswordPage';
import { Landing } from '@/pages/Landing';
import { NotFound } from '@/pages/NotFound';
import { ResetPasswordPage } from '@/pages/ResetPasswordPage';
import { useAuthStore } from '@/store/useAuthStore';

// The dashboard carries Chart.js and MediaPipe; keep them out of the landing bundle.
const Dashboard = lazy(() => import('@/pages/Dashboard'));
const AdminDashboard = lazy(() => import('@/pages/admin/AdminDashboard'));

function DashboardFallback() {
  return (
    <div className="chart-paper flex min-h-dvh items-center justify-center" role="status">
      <p className="text-ink-soft">Loading the workstation…</p>
    </div>
  );
}

export default function App() {
  const bootstrap = useAuthStore((s) => s.bootstrap);
  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/register" element={<AuthPage mode="register" />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route
        path="/account"
        element={
          <ProtectedRoute>
            <AccountPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/app"
        element={
          <ProtectedRoute>
            <Suspense fallback={<DashboardFallback />}>
              <Dashboard />
            </Suspense>
          </ProtectedRoute>
        }
      />
      <Route
        path="/admin"
        element={
          <ProtectedRoute roles={['ADMIN']}>
            <Suspense fallback={<DashboardFallback />}>
              <AdminDashboard />
            </Suspense>
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
