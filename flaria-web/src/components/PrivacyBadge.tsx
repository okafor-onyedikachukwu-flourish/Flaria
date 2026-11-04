import { ShieldCheck } from 'lucide-react';

import { useTelemetryStore } from '@/store/useTelemetryStore';

/**
 * States what is and isn't kept, and whether the server's privacy attestation
 * (sent in the stream handshake) has been received for the current session.
 */
export function PrivacyBadge() {
  const privacy = useTelemetryStore((s) => s.hello?.privacy);
  const attested = privacy !== undefined && privacy.raw_media_persisted === false;

  return (
    <details className="group relative">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-full border border-scrub/30 bg-scrub-wash px-3 py-1.5 text-step--1 font-medium text-scrub-deep marker:hidden hover:border-scrub">
        <ShieldCheck size={16} aria-hidden />
        Raw media never stored
      </summary>
      <div className="absolute left-0 z-30 sm:left-auto sm:right-0 mt-2 w-[min(22rem,calc(100vw-2rem))] rounded-lg border border-rule bg-white p-4 text-step--1 shadow-lg">
        <p className="font-medium text-ink">What stays private</p>
        <ul className="mt-2 list-disc space-y-1.5 pl-4 text-ink-soft">
          <li>Video frames never leave this device. The face mesh runs in your browser and only landmark positions are sent.</li>
          <li>
            Audio is held in a {privacy ? `${privacy.audio_ring_seconds.toFixed(1)} s (${Math.round(privacy.audio_ring_bytes / 1024)} KB)` : 'one-second'} server
            memory buffer, overwritten continuously and zeroed when the session ends. It is never written to disk.
          </li>
          <li>
            Stored: {privacy ? `${privacy.persist_hz} per second` : 'once per second'} averages of 15 voice measures and 5 face measures, plus anomaly scores.
            These can’t be turned back into audio or images.
          </li>
        </ul>
        <p className={`mt-3 border-t border-rule pt-2 ${attested ? 'text-stable' : 'text-ink-faint'}`}>
          {attested ? 'Server attestation received for this session.' : 'Server attestation arrives when a session starts.'}
        </p>
      </div>
    </details>
  );
}
