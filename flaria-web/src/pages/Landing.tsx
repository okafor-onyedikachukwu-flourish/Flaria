import type React from 'react';
import { Link } from 'react-router-dom';

import { BrandMark } from '@/components/BrandMark';
import { HeroTrace } from '@/components/HeroTrace';
import { SafeImage } from '@/components/SafeImage';
import { ASSETS } from '@/config/assets';
import { DEMO_BUILD } from '@/lib/demo';
import { useAuthStore } from '@/store/useAuthStore';
import { SITE_TITLE, useDocumentTitle } from '@/lib/useDocumentTitle';

/** On a separate landing deployment, app links go to the app's own address (VITE_APP_URL). */
const APP_URL = (import.meta.env.VITE_APP_URL ?? '').replace(/\/+$/, '');

function AppLink({ to, className, children }: { to: string; className: string; children: React.ReactNode }) {
  if (APP_URL) return <a href={`${APP_URL}${to}`} className={className}>{children}</a>;
  return <Link to={to} className={className}>{children}</Link>;
}

const VOICE_MEASURES: [string, string][] = [
  ['Spectral shape', '13 mel-frequency cepstral coefficients, spectral centroid and zero-crossing rate, averaged every 100 ms.'],
  ['Jitter and shimmer', 'Cycle-to-cycle variation in the timing and strength of vocal-fold vibration.'],
  ['Harmonics-to-noise ratio', 'How much of the voice is clear tone rather than breath noise.'],
];

const FACE_MEASURES: [string, string][] = [
  ['Lip-corner asymmetry', 'How far one mouth corner sits from the mirror image of the other across the facial midline.'],
  ['Lip aperture', 'How far the lips part while speaking, which should rise and fall with each syllable.'],
  ['Eye aspect ratio', 'Eye opening from six lid landmarks, including the timing of blinks.'],
  ['Head pitch and yaw', 'Head orientation, used to reject frames where geometry can’t be trusted.'],
];

const STEPS: [string, string][] = [
  ['Capture on the device', 'The browser turns each camera frame into 468 face landmarks and discards the image. Microphone audio streams to server memory.'],
  ['Compare with the personal baseline', 'Each measure becomes a z-score against that person’s running seven-day mean and spread, so their normal is zero.'],
  ['Fuse voice and face', 'A cross-attention model lets each moment of facial movement look for the sound that should accompany it. Loss of that coordination raises the index.'],
  ['Flag only sustained shifts', 'Watch needs 1.5 seconds above threshold and Elevated needs 3 seconds, so a cough, a smile or a turned head doesn’t trigger an alert.'],
];

function PipelineDiagram() {
  const box = 'fill-white stroke-rule';
  return (
    <svg viewBox="0 0 560 300" className="w-full" role="img" aria-labelledby="pipe-title pipe-desc">
      <title id="pipe-title">Where data goes</title>
      <desc id="pipe-desc">
        In the browser, camera frames become 468 landmarks and microphone audio becomes 100 millisecond chunks. On the server, audio sits in a one-second memory buffer, features and the model run in memory, and only one-per-second averages and scores reach the database.
      </desc>
      <g fontFamily="IBM Plex Sans, sans-serif" fontSize="12" fill="#142028">
        <rect x="10" y="20" width="200" height="260" rx="12" className="fill-paper-deep stroke-rule" />
        <text x="24" y="44" fontWeight="600" fontSize="13">This device</text>
        <rect x="24" y="60" width="172" height="44" rx="8" className={box} />
        <text x="36" y="80">Camera frame</text>
        <text x="36" y="96" fill="#4A5A66">stays in the browser</text>
        <rect x="24" y="124" width="172" height="44" rx="8" className={box} />
        <text x="36" y="144">468 landmarks</text>
        <text x="36" y="160" fill="#4A5A66">sent, then discarded</text>
        <rect x="24" y="200" width="172" height="44" rx="8" className={box} />
        <text x="36" y="220">Microphone audio</text>
        <text x="36" y="236" fill="#4A5A66">100 ms chunks</text>
        <path d="M110 104v20" stroke="#1D6B73" strokeWidth="2" markerEnd="url(#arrow)" />

        <rect x="250" y="20" width="300" height="190" rx="12" className="fill-hud" />
        <text x="266" y="44" fontWeight="600" fontSize="13" fill="#D7E4E8">Server memory only</text>
        <rect x="266" y="60" width="128" height="58" rx="8" fill="#16262F" stroke="#25404C" />
        <text x="278" y="82" fill="#D7E4E8">1 s audio ring</text>
        <text x="278" y="100" fill="#8EA6B1">zeroed on close</text>
        <rect x="406" y="60" width="128" height="58" rx="8" fill="#16262F" stroke="#25404C" />
        <text x="418" y="82" fill="#D7E4E8">15 + 5 measures</text>
        <text x="418" y="100" fill="#8EA6B1">z-scored</text>
        <rect x="266" y="134" width="268" height="58" rx="8" fill="#16262F" stroke="#25404C" />
        <text x="278" y="156" fill="#D7E4E8">Cross-attention model</text>
        <text x="278" y="174" fill="#8EA6B1">anomaly index, 30 times a second</text>

        <rect x="250" y="230" width="300" height="50" rx="12" className="fill-white stroke-scrub" />
        <text x="266" y="252" fontWeight="600">Database</text>
        <text x="266" y="268" fill="#4A5A66">1-second averages and scores, no media</text>

        <path d="M196 146h54" stroke="#1D6B73" strokeWidth="2" markerEnd="url(#arrow)" />
        <path d="M196 222h30v-133h40" fill="none" stroke="#1D6B73" strokeWidth="2" markerEnd="url(#arrow)" />
        <path d="M400 192v38" stroke="#19A7B5" strokeWidth="2" markerEnd="url(#arrow)" />
      </g>
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0L10 5L0 10z" fill="#1D6B73" />
        </marker>
      </defs>
    </svg>
  );
}

export function Landing() {
  useDocumentTitle(SITE_TITLE);
  const authed = useAuthStore((s) => s.status === 'authenticated');
  const year = new Date().getFullYear();

  return (
    <div className="min-h-dvh bg-paper">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2">
        Skip to content
      </a>
      <header className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-5">
        <BrandMark />
        <nav aria-label="Main" className="flex items-center gap-1 sm:gap-5">
          <a href="#measures" className="hidden text-step--1 text-ink-soft hover:text-ink sm:inline">What it measures</a>
          <a href="#privacy" className="hidden text-step--1 text-ink-soft hover:text-ink sm:inline">Privacy</a>
          <AppLink to={authed ? '/app' : '/login'} className="btn-quiet py-2 text-step--1">{authed ? 'Open dashboard' : 'Sign in'}</AppLink>
        </nav>
      </header>

      <main id="main">
        <section className="chart-paper border-y border-rule">
          <div className="mx-auto grid max-w-6xl items-center gap-6 px-5 py-12 lg:grid-cols-12 lg:gap-10 lg:py-20">
            <div className="lg:col-span-5">
              <h1 className="font-display text-step-4 font-semibold sm:text-step-5">
                Flaria spots early change in voice and facial movement
              </h1>
              <p className="mt-5 max-w-prose text-step-1 text-ink-soft">
                Flaria compares a person’s speech and facial movement with their own seven-day baseline and flags sustained
                shifts that can come before neurological or respiratory decline.
              </p>
              <p className="mt-3 max-w-prose text-step-0 text-ink-soft">Video never leaves the device, and no audio is stored.</p>
              <div className="mt-8 flex flex-wrap gap-3">
                <AppLink to={authed ? '/app' : '/login'} className="btn-primary">Start a session</AppLink>
                {!authed && <AppLink to="/register" className="btn-quiet">Create an account</AppLink>}
              </div>
              {DEMO_BUILD && (
                <p className="mt-4 max-w-prose text-step--1 text-ink-soft">
                  This is a demo version. Flaria isn’t a medical device, and its results aren’t for real health decisions.
                </p>
              )}
            </div>
            <div className="lg:col-span-7">
              <HeroTrace />
            </div>
          </div>
        </section>

        <section id="measures" className="mx-auto max-w-6xl px-5 py-16 lg:py-24" aria-labelledby="measures-heading">
          <h2 id="measures-heading" className="max-w-2xl text-step-3 font-semibold">Twenty measures, each read against the same person yesterday</h2>
          <p className="mt-3 max-w-prose text-ink-soft">
            Population averages miss early change because healthy people differ more from each other than from their own past selves.
            Flaria learns each person’s normal first.
          </p>
          <div className="mt-12 grid gap-14 lg:grid-cols-2">
            <article className="grid gap-6 sm:grid-cols-[1fr_11rem]">
              <div>
                <h3 className="text-step-2 font-semibold">Voice</h3>
                <dl className="mt-4 space-y-4">
                  {VOICE_MEASURES.map(([term, desc]) => (
                    <div key={term}>
                      <dt className="font-medium">{term}</dt>
                      <dd className="mt-0.5 text-step--1 text-ink-soft">{desc}</dd>
                    </div>
                  ))}
                </dl>
              </div>
              <SafeImage asset={ASSETS.laryngealMuscles} showCaption={false} className="self-start rounded-md border border-rule bg-white p-2" />
            </article>
            <article className="grid gap-6 sm:grid-cols-[1fr_11rem]">
              <div>
                <h3 className="text-step-2 font-semibold">Face</h3>
                <dl className="mt-4 space-y-4">
                  {FACE_MEASURES.map(([term, desc]) => (
                    <div key={term}>
                      <dt className="font-medium">{term}</dt>
                      <dd className="mt-0.5 text-step--1 text-ink-soft">{desc}</dd>
                    </div>
                  ))}
                </dl>
              </div>
              <SafeImage asset={ASSETS.facialMuscles} showCaption={false} className="self-start rounded-md border border-rule bg-white p-2" />
            </article>
          </div>
        </section>

        <section className="border-y border-rule bg-paper-deep" aria-labelledby="how-heading">
          <div className="mx-auto max-w-6xl px-5 py-16 lg:py-24">
            <h2 id="how-heading" className="text-step-3 font-semibold">How a reading works</h2>
            <ol className="mt-10 grid gap-8 md:grid-cols-2 lg:grid-cols-4">
              {STEPS.map(([title, body], i) => (
                <li key={title} className="relative border-t-2 border-scrub pt-4">
                  <span className="num font-display text-step-3 font-semibold text-scrub" aria-hidden>{i + 1}</span>
                  <h3 className="mt-1 font-sans text-step-0 font-semibold">{title}</h3>
                  <p className="mt-2 text-step--1 leading-relaxed text-ink-soft">{body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="privacy" className="mx-auto max-w-6xl px-5 py-16 lg:py-24" aria-labelledby="privacy-heading">
          <div className="grid items-start gap-12 lg:grid-cols-2">
            <div>
              <h2 id="privacy-heading" className="text-step-3 font-semibold">Nothing to leak, because nothing raw is kept</h2>
              <p className="mt-4 max-w-prose text-ink-soft">
                Faces and voices identify people. Flaria is built so the identifying signal is reduced to measurements as early as
                possible and the original is gone moments later.
              </p>
              <ul className="mt-6 space-y-3 text-step--1">
                <li><span className="font-medium">Camera frames</span> are analysed in the browser and never transmitted.</li>
                <li><span className="font-medium">Audio</span> lives in a one-second server memory buffer that is overwritten continuously and zeroed when the session ends.</li>
                <li><span className="font-medium">The database</span> stores one-second averages of the 20 measures and the anomaly score. They can’t be turned back into speech or a face.</li>
                <li><span className="font-medium">Access</span> is role-based: patients see their own readings, clinicians see patients assigned to them.</li>
              </ul>
            </div>
            <div className="panel p-4">
              <PipelineDiagram />
            </div>
          </div>

          <div className="mt-20 grid items-center gap-10 lg:grid-cols-12">
            <SafeImage asset={ASSETS.clinicians} className="lg:col-span-7" imgClassName="aspect-[3/2] rounded-instrument object-cover" />
            <div className="lg:col-span-5">
              <h2 className="text-step-3 font-semibold">Made for follow-up between visits</h2>
              <p className="mt-4 text-ink-soft">
                Patients run short sessions at home. Their clinician sees the trend across days, with every elevated reading tied
                to the measures that moved, and decides what happens next.
              </p>
              <AppLink to={authed ? '/app' : '/register'} className="btn-primary mt-6">{authed ? 'Open dashboard' : 'Create an account'}</AppLink>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-rule bg-paper-deep">
        <div className="mx-auto max-w-6xl space-y-4 px-5 py-10 text-step--1 text-ink-soft">
          <p className="max-w-prose">
            Flaria is a monitoring aid. It does not diagnose disease and does not replace clinical assessment. If you have sudden
            facial drooping, slurred speech or trouble breathing, call your local emergency number now.
          </p>
          <p className="max-w-prose">
            Images: anatomical plates from Henry Gray, Anatomy of the Human Body (1918), public domain, via Wikimedia Commons. Clinician
            photograph by Vitaly Gariev on Unsplash.
          </p>
          <p>© {year} Flaria</p>
        </div>
      </footer>
    </div>
  );
}
