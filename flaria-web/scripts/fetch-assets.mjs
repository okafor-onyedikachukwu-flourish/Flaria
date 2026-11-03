// Downloads the face landmark model and reference images for self-hosting.
// Each file is verified (HTTP 200, expected content type, minimum size) before
// it is written, so a broken upstream never ships a corrupt asset.
//   npm run assets            fetch anything missing
//   npm run assets -- --force re-download everything
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'public');
const force = process.argv.includes('--force');

const ASSETS = [
  {
    // Google MediaPipe Face Landmarker (Apache-2.0)
    url: 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task',
    out: 'mediapipe/face_landmarker.task',
    types: ['application/octet-stream', 'binary/octet-stream'],
    minBytes: 1_000_000,
  },
  {
    // Henry Gray, Anatomy of the Human Body (1918), plate 956. Public domain. https://commons.wikimedia.org/wiki/File:Gray956.png
    url: 'https://commons.wikimedia.org/wiki/Special:FilePath/Gray956.png',
    out: 'assets/gray956-larynx.png',
    types: ['image/png'],
    minBytes: 10_000,
  },
  {
    // Henry Gray (1918), plate 960. Public domain. https://commons.wikimedia.org/wiki/File:Gray960.png
    url: 'https://commons.wikimedia.org/wiki/Special:FilePath/Gray960.png',
    out: 'assets/gray960-laryngeal-muscles.png',
    types: ['image/png'],
    minBytes: 10_000,
  },
  {
    // Henry Gray (1918), plate 378. Public domain. https://commons.wikimedia.org/wiki/File:Gray378.png
    url: 'https://commons.wikimedia.org/wiki/Special:FilePath/Gray378.png',
    out: 'assets/gray378-head-muscles.png',
    types: ['image/png'],
    minBytes: 10_000,
  },
  {
    // Photo by Vitaly Gariev on Unsplash (Unsplash License). https://unsplash.com/photos/2kopEHjST-g
    url: 'https://images.unsplash.com/photo-1758691463203-cce9d415b2b5?fm=jpg&q=72&w=1600&auto=format&fit=crop',
    out: 'assets/unsplash-clinicians-tablet.jpg',
    types: ['image/jpeg'],
    minBytes: 30_000,
  },
];

let failures = 0;
for (const asset of ASSETS) {
  const target = join(root, asset.out);
  if (existsSync(target) && !force) {
    console.log(`[assets] ok      ${asset.out}`);
    continue;
  }
  try {
    const res = await fetch(asset.url, { redirect: 'follow', headers: { 'User-Agent': 'flaria-asset-fetch/1.0' } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const type = (res.headers.get('content-type') || '').split(';')[0].trim();
    if (!asset.types.includes(type)) throw new Error(`unexpected content-type ${type}`);
    const body = Buffer.from(await res.arrayBuffer());
    if (body.length < asset.minBytes) throw new Error(`file too small (${body.length} bytes)`);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, body);
    const sha = createHash('sha256').update(body).digest('hex').slice(0, 12);
    console.log(`[assets] fetched ${asset.out} (${(body.length / 1024).toFixed(0)} KiB, sha256 ${sha})`);
  } catch (err) {
    failures += 1;
    console.warn(`[assets] FAILED  ${asset.out}: ${err.message}`);
  }
}
if (failures) {
  console.warn(`[assets] ${failures} asset(s) missing. Images fall back to their remote source; the face model is required for the camera HUD.`);
  process.exitCode = 1;
}
