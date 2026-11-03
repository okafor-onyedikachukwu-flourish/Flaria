import { Activity, AlertTriangle, CircleCheck, Gauge, Radio, ScanFace, ShieldAlert, Timer, WifiOff } from 'lucide-react';
import type { ReactNode } from 'react';

import { useTelemetryStore } from '@/store/useTelemetryStore';
import type { RiskStatus } from '@/types';

const LATENCY_TARGET_MS = 20;

const STATUS_COPY: Record<RiskStatus, { title: string; body: string; tone: string; icon: ReactNode }> = {
  CALIBRATING: {
    title: 'Calibrating',
    body: 'Learning this person’s baseline. Keep talking naturally while facing the camera.',
    tone: 'border-rule bg-paper-card text-ink',
    icon: <Activity size={22} aria-hidden />,
  },
  STABLE: {
    title: 'Stable',
    body: 'Voice and facial movement match the personal baseline.',
    tone: 'border-stable/30 bg-stable-wash text-stable',
    icon: <CircleCheck size={22} aria-hidden />,
  },
  WATCH: {
    title: 'Watch',
    body: 'A sustained shift from baseline. Keep the session running and repeat the reading passage.',
    tone: 'border-watch/40 bg-watch-wash text-watch-ink',
    icon: <AlertTriangle size={22} aria-hidden />,
  },
  ELEVATED: {
    title: 'Elevated',
    body: 'A strong, sustained shift from baseline. Review with a clinician. This reading is not a diagnosis.',
    tone: 'border-elevated/40 bg-elevated-wash text-elevated',
    icon: <ShieldAlert size={22} aria-hidden />,
  },
  SIGNAL_LOST: {
    title: 'Signal lost',
    body: 'No face or voice detected. Check the camera, microphone and lighting.',
    tone: 'border-rule bg-paper-deep text-ink-soft',
    icon: <WifiOff size={22} aria-hidden />,
  },
};

function fmt(ms: number | null): string {
  if (ms === null) return '–';
  return ms < 10 ? ms.toFixed(1) : ms.toFixed(0);
}

function Chip({ ok, label, warn }: { ok: boolean; label: string; warn?: boolean }) {
  const cls = ok ? 'border-stable/30 bg-stable-wash text-stable' : warn ? 'border-watch/40 bg-watch-wash text-watch-ink' : 'border-rule bg-paper-deep text-ink-soft';
  return (
    <li className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-step--1 ${cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-stable' : warn ? 'bg-watch' : 'bg-ink-faint'}`} aria-hidden />
      {label}
      <span className="sr-only">{ok ? 'ok' : 'not available'}</span>
    </li>
  );
}

export function StatusConsole({ streaming }: { streaming: boolean }) {
  const ui = useTelemetryStore((s) => s.ui);
  const hello = useTelemetryStore((s) => s.hello);
  const connection = useTelemetryStore((s) => s.connection);
  const copy = STATUS_COPY[ui.status];
  const live = streaming && connection === 'open';
  const { latency } = ui;
  const withinTarget = latency.pipelineMs !== null && latency.pipelineMs <= LATENCY_TARGET_MS;
  const stub = hello?.model.version.startsWith('STUB');

  return (
    <section className="panel space-y-5 p-5" aria-labelledby="status-heading">
      <h2 id="status-heading" className="sr-only">Current status</h2>

      <div className={`rounded-lg border p-4 ${live ? copy.tone : 'border-rule bg-paper-deep text-ink-soft'}`} aria-live="polite">
        <div className="flex items-center gap-2.5">
          {live ? copy.icon : <Radio size={22} aria-hidden />}
          <p className="font-display text-step-2 font-semibold">{live ? copy.title : 'Not streaming'}</p>
        </div>
        <p className="mt-1.5 text-step--1 leading-relaxed">
          {live ? copy.body : 'Start a session to see a live reading.'}
        </p>
        {live && ui.status === 'CALIBRATING' && (
          <div className="mt-3">
            <div className="h-1.5 overflow-hidden rounded-full bg-rule" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(ui.quality.calibration * 100)} aria-label="Calibration progress">
              <div className="h-full bg-scrub transition-[width]" style={{ width: `${ui.quality.calibration * 100}%` }} />
            </div>
          </div>
        )}
      </div>

      <div className="flex items-end justify-between gap-4">
        <div>
          <p className="text-step--1 text-ink-soft">Anomaly index</p>
          <p className="num font-display text-step-5 font-semibold leading-none">{live && ui.anomaly !== null ? ui.anomaly.toFixed(2) : '–'}</p>
        </div>
        {hello && (
          <p className="text-right text-step--1 text-ink-soft">
            Watch at {hello.model.threshold_watch.toFixed(2)}
            <br />
            Elevated at {hello.model.threshold_elevated.toFixed(2)}
          </p>
        )}
      </div>

      <div>
        <h3 className="mb-2 font-sans text-step--1 font-medium text-ink-soft">Signal quality</h3>
        <ul className="flex flex-wrap gap-2">
          <Chip ok={live && ui.quality.face} label="Face" />
          <Chip ok={live && ui.quality.pose_ok} label="Head pose" warn={live && ui.quality.face && !ui.quality.pose_ok} />
          <Chip ok={live && ui.quality.voiced} label="Voice" />
          <Chip ok={live && !ui.quality.audio_dropout} label="Audio link" warn={live && ui.quality.audio_dropout} />
        </ul>
      </div>

      <div>
        <h3 className="mb-2 flex items-center gap-1.5 font-sans text-step--1 font-medium text-ink-soft">
          <Timer size={15} aria-hidden /> Latency
        </h3>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-step--1">
          <dt className="text-ink-soft">Pipeline, target under {LATENCY_TARGET_MS} ms</dt>
          <dd className={`num text-right font-medium ${latency.pipelineMs === null ? '' : withinTarget ? 'text-stable' : 'text-watch-ink'}`}>
            {fmt(latency.pipelineMs)} ms
          </dd>
          <dt className="text-ink-soft">Server processing</dt>
          <dd className="num text-right">{fmt(latency.procMs)} ms</dd>
          <dt className="text-ink-soft">Network round trip</dt>
          <dd className="num text-right">{fmt(latency.rttMs)} ms</dd>
          <dt className="text-ink-soft">Camera to screen</dt>
          <dd className="num text-right">{fmt(latency.captureToDisplayMs)} ms</dd>
          <dt className="text-ink-soft">Update rate</dt>
          <dd className="num text-right">{latency.tickHz ? `${latency.tickHz.toFixed(0)} Hz` : '–'}</dd>
        </dl>
        <p className="mt-2 text-step--1 text-ink-faint">
          Pipeline is server processing plus half the round trip. Camera to screen also includes waiting for the next update.
        </p>
      </div>

      {hello && (
        <div className="flex items-start gap-2 border-t border-rule pt-4 text-step--1 text-ink-soft">
          {stub ? <AlertTriangle size={16} className="mt-0.5 shrink-0 text-watch-ink" aria-hidden /> : <Gauge size={16} className="mt-0.5 shrink-0" aria-hidden />}
          <p>
            {stub ? (
              <span className="font-medium text-watch-ink">Test stub model loaded. Readings are not meaningful. </span>
            ) : null}
            Model {hello.model.version} on {hello.model.provider.replace('ExecutionProvider', '')}.
            {hello.calibration.using_persisted_baseline ? ' Using the 7-day baseline.' : ' Building today’s baseline from this session.'}
          </p>
        </div>
      )}
      {!hello && live && (
        <p className="flex items-center gap-2 text-step--1 text-ink-soft"><ScanFace size={16} aria-hidden /> Waiting for the server…</p>
      )}
    </section>
  );
}
