// Post-build SEO step (runs after `vite build`):
//   dist/index.html  landing page prerendered to real HTML + JSON-LD structured data
//   dist/app.html    empty shell with noindex, served for every private/app route
//   dist/sitemap.xml and dist/robots.txt for the public site
//
// Environment:
//   VITE_SITE_URL                   required, e.g. https://flaria.health (no trailing slash)
//   FLARIA_SAME_AS                  optional, comma-separated official profiles (GitHub, LinkedIn, ...)
//   FLARIA_GOOGLE_SITE_VERIFICATION optional, the content value from Google Search Console
//   FLARIA_BING_SITE_VERIFICATION   optional, the content value from Bing Webmaster Tools
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { createServer, loadEnv } from 'vite';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
const env = { ...loadEnv('production', root, ''), ...process.env };

const site = (env.VITE_SITE_URL || env.RENDER_EXTERNAL_URL || '').replace(/\/+$/, '');
if (!/^https?:\/\/[^/]+$/.test(site)) {
  console.error('[prerender] VITE_SITE_URL must be set to the site origin, e.g. VITE_SITE_URL=https://flaria.health');
  process.exit(1);
}
if (!site.startsWith('https://') && !/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(site)) {
  console.error('[prerender] VITE_SITE_URL must use https outside local testing');
  process.exit(1);
}
const template = readFileSync(join(dist, 'index.html'), 'utf8');
if (template.includes('%VITE_SITE_URL%')) {
  console.error('[prerender] index.html still contains %VITE_SITE_URL%: run vite build with VITE_SITE_URL set');
  process.exit(1);
}

const escapeAttr = (v) => v.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
const sameAs = (env.FLARIA_SAME_AS || '').split(',').map((s) => s.trim()).filter((s) => /^https:\/\//.test(s));
const description =
  'Flaria compares voice and facial movement with each person’s own 7-day baseline to flag early neurological and respiratory change, keeping video on the device.';

const jsonLd = {
  '@context': 'https://schema.org',
  '@graph': [
    {
      '@type': 'Organization',
      '@id': `${site}/#organization`,
      name: 'Flaria',
      url: `${site}/`,
      logo: { '@type': 'ImageObject', url: `${site}/icon-512.png`, width: 512, height: 512 },
      ...(sameAs.length ? { sameAs } : {}),
    },
    {
      '@type': 'WebSite',
      '@id': `${site}/#website`,
      name: 'Flaria',
      url: `${site}/`,
      inLanguage: 'en',
      publisher: { '@id': `${site}/#organization` },
    },
    {
      '@type': 'WebApplication',
      '@id': `${site}/#app`,
      name: 'Flaria',
      url: `${site}/`,
      applicationCategory: 'HealthApplication',
      operatingSystem: 'Any modern web browser',
      browserRequirements: 'Requires a camera, a microphone and JavaScript',
      description,
      image: `${site}/og-image.png`,
      publisher: { '@id': `${site}/#organization` },
    },
  ],
};

const headExtras = [
  `<script type="application/ld+json">${JSON.stringify(jsonLd).replace(/</g, '\\u003c')}</script>`,
  env.FLARIA_GOOGLE_SITE_VERIFICATION &&
    `<meta name="google-site-verification" content="${escapeAttr(env.FLARIA_GOOGLE_SITE_VERIFICATION)}" />`,
  env.FLARIA_BING_SITE_VERIFICATION && `<meta name="msvalidate.01" content="${escapeAttr(env.FLARIA_BING_SITE_VERIFICATION)}" />`,
].filter(Boolean);

// ---------------------------------------------------------------------------- render the landing page
const vite = await createServer({ root, mode: 'production', server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' });
let appHtml;
try {
  const { render } = await vite.ssrLoadModule('/src/entry-server.tsx');
  appHtml = render('/');
} finally {
  await vite.close();
}
if (!appHtml.includes('Flaria spots early change')) {
  console.error('[prerender] rendered landing page is missing its headline; refusing to ship an empty page');
  process.exit(1);
}

const landing = template
  .replace('<!--flaria:head-->', headExtras.join('\n    '))
  .replace('<div id="root"><!--flaria:app--></div>', `<div id="root">${appHtml}</div>`);
writeFileSync(join(dist, 'index.html'), landing);

// ---------------------------------------------------------------------------- shell for private routes
const shell = template
  .replace(/<title>[^<]*<\/title>/, '<title>Flaria</title>')
  .replace(/<meta name="robots" content="[^"]*" \/>/, '<meta name="robots" content="noindex, nofollow" />')
  .replace(/\s*<link rel="canonical"[^>]*>/, '')
  .replace('<!--flaria:head-->', '')
  .replace('<!--flaria:app-->', '');
writeFileSync(join(dist, 'app.html'), shell);

// ---------------------------------------------------------------------------- crawler files
const today = new Date().toISOString().slice(0, 10);
writeFileSync(
  join(dist, 'sitemap.xml'),
  `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  <url>
    <loc>${site}/</loc>
    <lastmod>${today}</lastmod>
    <image:image><image:loc>${site}/og-image.png</image:loc></image:image>
  </url>
</urlset>
`,
);
writeFileSync(
  join(dist, 'robots.txt'),
  `# Flaria: the public site is the landing page. App screens are sign-in only and marked noindex.
User-agent: *
Allow: /
Disallow: /api/
Disallow: /ws/

Sitemap: ${site}/sitemap.xml
`,
);

for (const f of ['og-image.png', 'icon-512.png', 'apple-touch-icon.png', 'site.webmanifest']) {
  if (!existsSync(join(dist, f))) console.warn(`[prerender] warning: ${f} missing from dist (run tools/ui_screenshots/brand_assets.py)`);
}
console.log(`[prerender] landing prerendered (${(appHtml.length / 1024).toFixed(1)} KiB HTML) for ${site}; app shell, sitemap and robots written`);
process.exit(0);
