/**
 * External image assets. Every entry is either public domain or under a licence
 * that permits reuse with attribution, and records its source page.
 * `npm run assets` downloads each one into /public/assets for self-hosting;
 * SafeImage tries the local copy first, then the remote original.
 */
export interface ImageAsset {
  local: string;
  remote: string;
  alt: string;
  caption: string;
  credit: string;
  license: string;
  sourcePage: string;
  width: number;
  height: number;
}

export const ASSETS = {
  // Source: https://commons.wikimedia.org/wiki/File:Gray956.png (public domain, Gray's Anatomy 1918, plate 956)
  larynx: {
    local: '/assets/gray956-larynx.png',
    remote: 'https://commons.wikimedia.org/wiki/Special:FilePath/Gray956.png',
    alt: 'Laryngoscopic drawing of the larynx seen from above, showing the paired vocal folds and the opening between them.',
    caption: 'The vocal folds seen through a laryngoscope. Their cycle-to-cycle regularity is what jitter, shimmer and harmonics-to-noise ratio describe.',
    credit: "Henry Gray, Anatomy of the Human Body (1918), plate 956",
    license: 'Public domain',
    sourcePage: 'https://commons.wikimedia.org/wiki/File:Gray956.png',
    width: 450,
    height: 354,
  },
  // Source: https://commons.wikimedia.org/wiki/File:Gray960.png (public domain, Gray's Anatomy 1918, plate 960)
  laryngealMuscles: {
    local: '/assets/gray960-laryngeal-muscles.png',
    remote: 'https://commons.wikimedia.org/wiki/Special:FilePath/Gray960.png',
    alt: 'Anatomical drawing of the intrinsic muscles of the larynx viewed from above.',
    caption: 'Intrinsic laryngeal muscles. Weakness or poor breath support changes voice stability long before speech sounds abnormal to a listener.',
    credit: "Henry Gray, Anatomy of the Human Body (1918), plate 960",
    license: 'Public domain',
    sourcePage: 'https://commons.wikimedia.org/wiki/File:Gray960.png',
    width: 489,
    height: 600,
  },
  // Source: https://commons.wikimedia.org/wiki/File:Gray378.png (public domain, Gray's Anatomy 1918, plate 378)
  facialMuscles: {
    local: '/assets/gray378-head-muscles.png',
    remote: 'https://commons.wikimedia.org/wiki/Special:FilePath/Gray378.png',
    alt: 'Anatomical drawing of the muscles of the head and face from the side, including the muscles around the mouth and eye.',
    caption: 'Muscles of facial expression, all driven by the facial nerve. One-sided weakness appears as lip-corner droop and reduced eye closure.',
    credit: "Henry Gray, Anatomy of the Human Body (1918), plate 378",
    license: 'Public domain',
    sourcePage: 'https://commons.wikimedia.org/wiki/File:Gray378.png',
    width: 620,
    height: 700,
  },
  // Source: https://unsplash.com/photos/two-doctors-looking-at-a-tablet-together-2kopEHjST-g (Unsplash License)
  clinicians: {
    local: '/assets/unsplash-clinicians-tablet.jpg',
    remote: 'https://images.unsplash.com/photo-1758691463203-cce9d415b2b5?fm=jpg&q=72&w=1600&auto=format&fit=crop',
    alt: 'Two clinicians in white coats reviewing results together on a tablet.',
    caption: 'Readings are written for clinical review, not for self-diagnosis.',
    credit: 'Photo by Vitaly Gariev on Unsplash',
    license: 'Unsplash License',
    sourcePage: 'https://unsplash.com/photos/two-doctors-looking-at-a-tablet-together-2kopEHjST-g',
    width: 1600,
    height: 1067,
  },
} satisfies Record<string, ImageAsset>;
