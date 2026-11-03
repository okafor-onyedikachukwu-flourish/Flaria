import { Link } from 'react-router-dom';

interface Props {
  to?: string;
  tone?: 'ink' | 'light';
}

/** Wordmark: a single trace stroke that reads as both a waveform and a pulse. */
export function BrandMark({ to = '/', tone = 'ink' }: Props) {
  const color = tone === 'ink' ? 'text-ink' : 'text-hud-text';
  return (
    <Link to={to} className={`inline-flex items-center gap-2.5 ${color}`} aria-label="Flaria home">
      <svg viewBox="0 0 40 24" className="h-6 w-10" aria-hidden="true">
        <path
          d="M1 14h6l3-8 4 14 4-18 4 16 3-4h14"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="text-trace"
        />
      </svg>
      <span className="font-display text-step-1 font-semibold tracking-tight">Flaria</span>
    </Link>
  );
}
