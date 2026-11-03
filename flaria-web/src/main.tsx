import { StrictMode } from 'react';
import { createRoot, hydrateRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

// Fonts are self-hosted (no third-party font CDN sees who opens a clinical app).
import '@fontsource/ibm-plex-sans/400.css';
import '@fontsource/ibm-plex-sans/400-italic.css';
import '@fontsource/ibm-plex-sans/500.css';
import '@fontsource/ibm-plex-sans/600.css';
import '@fontsource/ibm-plex-sans-condensed/500.css';
import '@fontsource/ibm-plex-sans-condensed/600.css';
import '@fontsource/ibm-plex-sans-condensed/700.css';

import App from './App';
import './index.css';

const root = document.getElementById('root');
if (!root) throw new Error('Root element #root is missing from index.html');

const app = (
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>
);

// The landing page ships prerendered (scripts/prerender.mjs): hydrate it in place.
// Every other route gets an empty shell and renders from scratch.
const prerendered = window.location.pathname === '/' && root.firstElementChild !== null;
if (prerendered) hydrateRoot(root, app);
else createRoot(root).render(app);
