import { Link } from 'react-router-dom';

import { useDocumentTitle } from '@/lib/useDocumentTitle';

export function NotFound() {
  useDocumentTitle('Page not found: Flaria');
  return (
    <main className="chart-paper flex min-h-dvh flex-col items-center justify-center gap-4 p-6 text-center">
      <h1 className="text-step-4 font-semibold">This page doesn’t exist</h1>
      <p className="text-ink-soft">The link may be out of date.</p>
      <Link to="/" className="btn-primary">Go to the home page</Link>
    </main>
  );
}
