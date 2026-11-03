import { ASSETS } from '@/config/assets';

import { SafeImage } from './SafeImage';

/** Eye Aspect Ratio landmarks, drawn here rather than sourced so the indices match the code exactly. */
function EarDiagram() {
  return (
    <figure>
      <svg viewBox="0 0 320 150" className="w-full" role="img" aria-labelledby="ear-title ear-desc">
        <title id="ear-title">Eye aspect ratio landmarks</title>
        <desc id="ear-desc">Six points around the eye: two corners and two pairs on the upper and lower lid. The ratio of vertical to horizontal distances falls toward zero as the eye closes.</desc>
        <path d="M40 75 Q160 10 280 75 Q160 140 40 75 Z" fill="#E2E8EA" stroke="#1D6B73" strokeWidth="2" />
        <circle cx="160" cy="75" r="24" fill="#4A5A66" />
        <line x1="40" y1="75" x2="280" y2="75" stroke="#71818C" strokeDasharray="5 4" />
        <line x1="115" y1="40" x2="115" y2="110" stroke="#C27C0E" strokeWidth="2" />
        <line x1="205" y1="40" x2="205" y2="110" stroke="#C27C0E" strokeWidth="2" />
        {[
          [40, 75, 'p1'], [115, 40, 'p2'], [205, 40, 'p3'], [280, 75, 'p4'], [205, 110, 'p5'], [115, 110, 'p6'],
        ].map(([x, y, label]) => (
          <g key={label as string}>
            <circle cx={x as number} cy={y as number} r="5" fill="#19A7B5" />
            <text x={(x as number) + (x === 40 ? -30 : x === 280 ? 10 : -8)} y={(y as number) + ((y as number) < 75 ? -10 : (y as number) > 75 ? 22 : 5)} fontSize="13" fill="#142028" fontFamily="IBM Plex Sans, sans-serif">{label}</text>
          </g>
        ))}
      </svg>
      <figcaption className="mt-2 text-step--1 text-ink-soft">
        EAR = (|p2 − p6| + |p3 − p5|) ÷ (2 |p1 − p4|). Open eyes sit near 0.25 to 0.35; blinks dip below 0.15. A lasting drop on one side can reflect lid weakness.
      </figcaption>
    </figure>
  );
}

export function ReferencePanel() {
  return (
    <details className="panel group p-5">
      <summary className="cursor-pointer list-none font-display text-step-1 font-semibold marker:hidden">
        <span className="inline-flex items-center gap-2">
          Clinical reference
          <span className="text-step--1 font-sans font-normal text-ink-soft group-open:hidden">Show anatomy and measure definitions</span>
        </span>
      </summary>
      <div className="mt-5 grid gap-8 md:grid-cols-3">
        <div className="space-y-3">
          <h3 className="font-sans text-step-0 font-semibold">Voice source</h3>
          <SafeImage asset={ASSETS.larynx} className="rounded-md bg-white p-3" />
          <p className="text-step--1 text-ink-soft">
            Jitter and shimmer measure how regular consecutive vocal-fold cycles are in timing and strength. Harmonics-to-noise
            ratio falls when air escapes through incompletely closing folds, which sounds breathy.
          </p>
        </div>
        <div className="space-y-3">
          <h3 className="font-sans text-step-0 font-semibold">Facial movement</h3>
          <SafeImage asset={ASSETS.facialMuscles} className="rounded-md bg-white p-3" />
          <p className="text-step--1 text-ink-soft">
            Lip-corner asymmetry reflects the right corner across the facial midline and measures the gap to the left corner, scaled by eye width so camera distance doesn’t matter.
          </p>
        </div>
        <div className="space-y-3">
          <h3 className="font-sans text-step-0 font-semibold">Eye closure</h3>
          <div className="rounded-md bg-white p-3"><EarDiagram /></div>
          <p className="text-step--1 text-ink-soft">
            Every measure is compared with this person’s own baseline, so a naturally uneven smile or a low voice is their normal, not a finding.
          </p>
        </div>
      </div>
    </details>
  );
}
