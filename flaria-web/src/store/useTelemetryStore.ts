/**
 * High-frequency telemetry state.
 *
 * Two layers, so 30-60 Hz ticks never re-render React:
 *  1. `buffers`: a stable object of typed-array ring buffers mutated in place on
 *     every tick. Charts and the HUD read it from the shared frame loop.
 *  2. `ui`: a small summary published to Zustand at most 8 times per second for
 *     components that render text (status badge, latency monitor).
 */
import { create } from 'zustand';

import { SeriesBuffer } from '@/lib/ringBuffer';
import { epochMs } from '@/lib/protocol';
import type { ConnectionState, HelloMessage, RiskStatus, TelemetryTick, TickQuality, VoiceQuality } from '@/types';

export const HISTORY_SECONDS = 30;
const CAPACITY = 2048; // > 60 Hz x 30 s
const UI_PUBLISH_INTERVAL_MS = 125;

export interface TelemetryBuffers {
  anomaly: SeriesBuffer;
  f0: SeriesBuffer;
  jitter: SeriesBuffer;
  shimmer: SeriesBuffer;
  hnr: SeriesBuffer;
  centroid: SeriesBuffer;
  asym: SeriesBuffer;
  lips: SeriesBuffer;
  ear: SeriesBuffer;
  latestAudio: Float32Array;
  latestVision: Float32Array;
  faceVisible: boolean;
  version: number;
}

export type SeriesKey = Exclude<keyof TelemetryBuffers, 'latestAudio' | 'latestVision' | 'faceVisible' | 'version'>;

export interface LatencySummary {
  procMs: number | null;
  rttMs: number | null;
  /** Server processing + one-way network estimate: the part of the pipeline the <20 ms target applies to. */
  pipelineMs: number | null;
  /** Camera capture to tick arrival, including waiting for the next server tick. */
  captureToDisplayMs: number | null;
  tickHz: number | null;
}

export interface TelemetryUi {
  status: RiskStatus;
  anomaly: number | null;
  quality: TickQuality;
  vq: VoiceQuality;
  vision: number[];
  latency: LatencySummary;
  ticks: number;
  droppedFrames: number;
}

interface TelemetryState {
  buffers: TelemetryBuffers;
  connection: ConnectionState;
  connectionDetail: string | null;
  hello: HelloMessage | null;
  ui: TelemetryUi;
  ingestTick: (tick: TelemetryTick) => void;
  setConnection: (state: ConnectionState, detail?: string | null) => void;
  setHello: (hello: HelloMessage) => void;
  recordRtt: (ms: number) => void;
  recordDroppedFrame: () => void;
  reset: () => void;
}

const createBuffers = (): TelemetryBuffers => ({
  anomaly: new SeriesBuffer(CAPACITY),
  f0: new SeriesBuffer(CAPACITY),
  jitter: new SeriesBuffer(CAPACITY),
  shimmer: new SeriesBuffer(CAPACITY),
  hnr: new SeriesBuffer(CAPACITY),
  centroid: new SeriesBuffer(CAPACITY),
  asym: new SeriesBuffer(CAPACITY),
  lips: new SeriesBuffer(CAPACITY),
  ear: new SeriesBuffer(CAPACITY),
  latestAudio: new Float32Array(15),
  latestVision: new Float32Array(5),
  faceVisible: false,
  version: 0,
});

const emptyUi = (): TelemetryUi => ({
  status: 'CALIBRATING',
  anomaly: null,
  quality: { face: false, voiced: false, audio_dropout: false, pose_ok: false, calibration: 0, calibrated: false },
  vq: { f0: null, jitter: null, shimmer: null, hnr: null, voiced: null },
  vision: [0, 0, 0, 0, 0],
  latency: { procMs: null, rttMs: null, pipelineMs: null, captureToDisplayMs: null, tickHz: null },
  ticks: 0,
  droppedFrames: 0,
});

// Mutable accumulators outside React.
let lastPublish = 0;
let publishTimer: ReturnType<typeof setTimeout> | null = null;
let procEma: number | null = null;
let rttEma: number | null = null;
let e2eEma: number | null = null;
let tickCount = 0;
let dropped = 0;
let latestTick: TelemetryTick | null = null;
let tickTimes: number[] = [];

const ema = (prev: number | null, next: number, alpha = 0.15) => (prev === null ? next : prev + alpha * (next - prev));

export const useTelemetryStore = create<TelemetryState>()((set, get) => {
  const publish = () => {
    publishTimer = null;
    lastPublish = performance.now();
    const t = latestTick;
    if (!t) return;
    const span = tickTimes.length > 1 ? (tickTimes[tickTimes.length - 1] - tickTimes[0]) / 1000 : 0;
    set({
      ui: {
        status: t.status,
        anomaly: t.anomaly,
        quality: t.q,
        vq: t.vq,
        vision: t.vision,
        latency: {
          procMs: procEma,
          rttMs: rttEma,
          pipelineMs: procEma !== null && rttEma !== null ? procEma + rttEma / 2 : null,
          captureToDisplayMs: e2eEma,
          tickHz: span > 0 ? (tickTimes.length - 1) / span : null,
        },
        ticks: tickCount,
        droppedFrames: dropped,
      },
    });
  };

  const schedulePublish = () => {
    const since = performance.now() - lastPublish;
    if (since >= UI_PUBLISH_INTERVAL_MS) publish();
    else publishTimer ??= setTimeout(publish, UI_PUBLISH_INTERVAL_MS - since);
  };

  return {
    buffers: createBuffers(),
    connection: 'idle',
    connectionDetail: null,
    hello: null,
    ui: emptyUi(),

    ingestTick: (tick) => {
      const b = get().buffers;
      const now = epochMs();
      const t = now / 1000;
      b.anomaly.push(t, tick.anomaly);
      b.f0.push(t, tick.vq.f0);
      b.jitter.push(t, tick.vq.jitter === null ? null : tick.vq.jitter * 100);
      b.shimmer.push(t, tick.vq.shimmer === null ? null : tick.vq.shimmer * 100);
      b.hnr.push(t, tick.vq.hnr);
      const voiced = tick.q.voiced || tick.vq.f0 !== null;
      b.centroid.push(t, voiced ? tick.audio[13] : null);
      const face = tick.q.face;
      b.asym.push(t, face ? tick.vision[0] : null);
      b.lips.push(t, face ? tick.vision[1] : null);
      b.ear.push(t, face ? tick.vision[2] : null);
      b.latestAudio.set(tick.audio);
      b.latestVision.set(tick.vision);
      b.faceVisible = face;
      b.version += 1;

      procEma = ema(procEma, tick.proc_ms);
      if (tick.src_ts > 0) {
        const e2e = now - tick.src_ts;
        if (e2e >= 0 && e2e < 10_000) e2eEma = ema(e2eEma, e2e);
      }
      tickCount += 1;
      tickTimes.push(now);
      if (tickTimes.length > 61) tickTimes = tickTimes.slice(-61);
      latestTick = tick;
      schedulePublish();
    },

    setConnection: (connection, detail = null) => set({ connection, connectionDetail: detail }),
    setHello: (hello) => set({ hello }),
    recordRtt: (ms) => {
      if (ms >= 0 && ms < 30_000) rttEma = ema(rttEma, ms, 0.3);
    },
    recordDroppedFrame: () => {
      dropped += 1;
    },

    reset: () => {
      const b = get().buffers;
      for (const key of ['anomaly', 'f0', 'jitter', 'shimmer', 'hnr', 'centroid', 'asym', 'lips', 'ear'] as const) b[key].clear();
      b.latestAudio.fill(0);
      b.latestVision.fill(0);
      b.faceVisible = false;
      b.version += 1;
      if (publishTimer) clearTimeout(publishTimer);
      publishTimer = null;
      procEma = rttEma = e2eEma = null;
      tickCount = dropped = 0;
      latestTick = null;
      tickTimes = [];
      set({ hello: null, ui: emptyUi(), connectionDetail: null });
    },
  };
});
