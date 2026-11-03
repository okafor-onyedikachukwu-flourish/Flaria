import { FlaskConical } from 'lucide-react';

import { useDemoMode } from '@/lib/demo';

/** Shown on every app screen of a demo deployment. */
export function DemoBanner() {
  const demo = useDemoMode();
  if (!demo) return null;
  return (
    <div role="note" className="border-b border-watch/40 bg-watch-wash">
      <p className="mx-auto flex max-w-7xl items-start gap-2 px-5 py-2.5 text-step--1 text-watch-ink">
        <FlaskConical size={16} className="mt-0.5 shrink-0" aria-hidden />
        <span>
          <strong className="font-semibold">Demo version.</strong> Flaria’s analysis model hasn’t been trained or clinically
          tested, so readings and results here aren’t real health information. If you have warning signs such as face drooping,
          slurred speech or severe breathlessness, call your local emergency number.
        </span>
      </p>
    </div>
  );
}
