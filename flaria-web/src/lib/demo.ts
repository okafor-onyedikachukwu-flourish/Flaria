import { useEffect, useState } from 'react';

import { env } from '@/config/env';

/** Set at build time for the static landing page, which has no API to ask. */
export const DEMO_BUILD = import.meta.env.VITE_DEMO_MODE === 'true';

let configPromise: Promise<{ demo_mode: boolean }> | null = null;

function loadConfig(): Promise<{ demo_mode: boolean }> {
  configPromise ??= fetch(`${env.apiBase}/config`, { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : { demo_mode: false }))
    .catch(() => ({ demo_mode: false }));
  return configPromise;
}

/** True when either the build or the server says this deployment is a demo. */
export function useDemoMode(): boolean {
  const [demo, setDemo] = useState(DEMO_BUILD);
  useEffect(() => {
    let alive = true;
    void loadConfig().then((c) => {
      if (alive && c.demo_mode) setDemo(true);
    });
    return () => {
      alive = false;
    };
  }, []);
  return demo;
}
