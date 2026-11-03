/**
 * Build-time prerender of the public landing page (scripts/prerender.mjs).
 * Crawlers and link previews get the full page as HTML; the browser then
 * hydrates it in place. Only public, unauthenticated content is rendered here.
 */
import { renderToString } from 'react-dom/server';
import { StaticRouter } from 'react-router-dom/server';

import { Landing } from '@/pages/Landing';

export function render(url: string): string {
  return renderToString(
    <StaticRouter location={url}>
      <Landing />
    </StaticRouter>,
  );
}
