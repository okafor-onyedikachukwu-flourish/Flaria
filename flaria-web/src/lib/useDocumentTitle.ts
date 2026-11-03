import { useEffect } from 'react';

export const SITE_TITLE = 'Flaria: Voice and Face Health Monitoring for Early Change';

/** Keep the tab title right during client-side navigation (the server sets it for the first load). */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title;
  }, [title]);
}
