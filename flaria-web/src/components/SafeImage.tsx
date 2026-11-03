import { useState } from 'react';

import type { ImageAsset } from '@/config/assets';

interface Props {
  asset: ImageAsset;
  className?: string;
  imgClassName?: string;
  showCaption?: boolean;
  priority?: boolean;
}

/**
 * Self-hosted copy first, original source second, and a captioned placeholder
 * last, so a missing or blocked image never leaves a broken icon in a clinical view.
 */
export function SafeImage({ asset, className = '', imgClassName = '', showCaption = true, priority = false }: Props) {
  const sources = [asset.local, asset.remote];
  const [index, setIndex] = useState(0);
  const failed = index >= sources.length;

  return (
    <figure className={className}>
      {failed ? (
        <div
          role="img"
          aria-label={asset.alt}
          className="flex w-full items-center justify-center rounded-md border border-dashed border-rule bg-paper-deep p-6 text-center text-step--1 text-ink-soft"
          style={{ aspectRatio: `${asset.width} / ${asset.height}` }}
        >
          Image unavailable. {asset.alt}
        </div>
      ) : (
        <img
          src={sources[index]}
          alt={asset.alt}
          width={asset.width}
          height={asset.height}
          loading={priority ? 'eager' : 'lazy'}
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setIndex((i) => i + 1)}
          className={`h-auto w-full ${imgClassName}`}
        />
      )}
      {showCaption && (
        <figcaption className="mt-2 text-step--1 text-ink-soft">
          {asset.caption}{' '}
          <a href={asset.sourcePage} target="_blank" rel="noreferrer noopener" className="underline decoration-rule underline-offset-2 hover:text-scrub-deep">
            {asset.credit}
          </a>
          , {asset.license}.
        </figcaption>
      )}
    </figure>
  );
}
