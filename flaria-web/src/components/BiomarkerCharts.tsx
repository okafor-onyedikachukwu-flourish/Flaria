/**
 * Streaming charts. Each chart is created once by React and then fed
 * imperatively from the telemetry ring buffers through the shared frame loop
 * (anomaly curve at 30 fps, sparklines at 10 fps) with chart.update('none'):
 * no React re-render, no animation, and point objects are pooled so steady
 * state allocates nothing.
 */
import {
  Chart as ChartJS,
  Filler,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
  type ChartData,
  type ChartOptions,
  type Plugin,
} from 'chart.js';
import { useEffect, useMemo, useRef } from 'react';
import { Line } from 'react-chartjs-2';

import { subscribeFrame } from '@/lib/frameLoop';
import type { SeriesBuffer } from '@/lib/ringBuffer';
import { epochMs } from '@/lib/protocol';
import { COLORS } from '@/lib/theme';
import { HISTORY_SECONDS, type SeriesKey, useTelemetryStore } from '@/store/useTelemetryStore';

ChartJS.register(LineController, LineElement, PointElement, LinearScale, Filler, Tooltip);

type XY = { x: number; y: number };
type LineChart = ChartJS<'line', XY[], unknown>;

function fillWindow(buffer: SeriesBuffer, nowSeconds: number, pool: XY[], target: XY[]): number | null {
  target.length = 0;
  let last: number | null = null;
  buffer.forEachSince(nowSeconds - HISTORY_SECONDS, (t, v, i) => {
    let p = pool[i];
    if (!p) {
      p = { x: 0, y: 0 };
      pool[i] = p;
    }
    p.x = t - nowSeconds;
    p.y = v;
    target.push(p);
    if (Number.isFinite(v)) last = v;
  });
  return last;
}

// ----------------------------------------------------------------------------- anomaly index
export function AnomalyChart() {
  const chartRef = useRef<LineChart | null | undefined>(null);
  const pool = useRef<XY[]>([]);
  const hello = useTelemetryStore((s) => s.hello);
  const thresholds = useRef({ watch: 0.6, elevated: 0.8 });
  thresholds.current = {
    watch: hello?.model.threshold_watch ?? 0.6,
    elevated: hello?.model.threshold_elevated ?? 0.8,
  };

  const thresholdPlugin = useMemo<Plugin<'line'>>(
    () => ({
      id: 'flariaThresholds',
      beforeDatasetsDraw(chart) {
        const { ctx, chartArea, scales } = chart;
        const lines = [
          { value: thresholds.current.elevated, color: COLORS.elevatedHud, label: 'Elevated' },
          { value: thresholds.current.watch, color: COLORS.watchHud, label: 'Watch' },
        ];
        ctx.save();
        ctx.font = '500 11px "IBM Plex Sans", system-ui, sans-serif';
        for (const line of lines) {
          const y = scales.y.getPixelForValue(line.value);
          ctx.strokeStyle = line.color;
          ctx.globalAlpha = 0.85;
          ctx.setLineDash([5, 4]);
          ctx.beginPath();
          ctx.moveTo(chartArea.left, y);
          ctx.lineTo(chartArea.right, y);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.fillStyle = line.color;
          ctx.fillText(line.label, chartArea.left + 6, y - 5);
        }
        ctx.restore();
      },
    }),
    [],
  );

  const data = useMemo<ChartData<'line', XY[], unknown>>(
    () => ({
      datasets: [
        {
          data: [],
          borderColor: COLORS.trace,
          backgroundColor: 'rgba(25, 167, 181, 0.14)',
          fill: 'origin',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0,
          spanGaps: false,
        },
      ],
    }),
    [],
  );

  const options = useMemo<ChartOptions<'line'>>(
    () => ({
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      parsing: false,
      normalized: true,
      interaction: { mode: 'nearest', axis: 'x', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          displayColors: false,
          callbacks: {
            title: (items) => `${Math.abs(items[0]?.parsed.x ?? 0).toFixed(1)} s ago`,
            label: (item) => `Anomaly index ${item.parsed.y.toFixed(2)}`,
          },
        },
      },
      scales: {
        x: {
          type: 'linear',
          min: -HISTORY_SECONDS,
          max: 0,
          grid: { color: 'rgba(142, 166, 177, 0.12)' },
          border: { color: COLORS.hudLine },
          ticks: { color: COLORS.hudDim, stepSize: 5, callback: (v) => (Number(v) === 0 ? 'now' : `${v} s`) },
        },
        y: {
          type: 'linear',
          min: 0,
          max: 1,
          grid: { color: 'rgba(142, 166, 177, 0.12)' },
          border: { color: COLORS.hudLine },
          ticks: { color: COLORS.hudDim, stepSize: 0.25 },
        },
      },
    }),
    [],
  );

  useEffect(() => {
    const buffers = useTelemetryStore.getState().buffers;
    let lastVersion = -1;
    return subscribeFrame(() => {
      const chart = chartRef.current;
      if (!chart) return;
      // Keep scrolling while connected even without new data, so gaps show as gaps.
      if (buffers.version === lastVersion && buffers.anomaly.size === 0) return;
      lastVersion = buffers.version;
      fillWindow(buffers.anomaly, epochMs() / 1000, pool.current, chart.data.datasets[0].data);
      chart.update('none');
    }, 30);
  }, []);

  return (
    <section className="hud hud-panel p-4" aria-labelledby="anomaly-heading">
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h2 id="anomaly-heading" className="text-step-1 font-semibold text-hud-text">Anomaly index, last 30 seconds</h2>
        <p className="text-step--1 text-hud-dim">0 matches baseline, 1 is a strong shift</p>
      </div>
      <div className="h-48 sm:h-56">
        <Line ref={chartRef} data={data} options={options} plugins={[thresholdPlugin]} aria-label="Anomaly index over the last 30 seconds" role="img" />
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------------- sparklines
interface SparkSpec {
  key: SeriesKey;
  label: string;
  unit: string;
  digits: number;
  description: string;
}

export const SPARKLINES: SparkSpec[] = [
  { key: 'f0', label: 'Pitch (f0)', unit: 'Hz', digits: 0, description: 'Fundamental frequency of voiced speech' },
  { key: 'jitter', label: 'Jitter', unit: '%', digits: 2, description: 'Cycle-to-cycle variation in pitch period' },
  { key: 'shimmer', label: 'Shimmer', unit: '%', digits: 1, description: 'Cycle-to-cycle variation in loudness' },
  { key: 'hnr', label: 'Harmonics-to-noise', unit: 'dB', digits: 1, description: 'Voice clarity versus breathiness' },
  { key: 'centroid', label: 'Spectral centroid', unit: 'Hz', digits: 0, description: 'Brightness of the voice spectrum' },
  { key: 'asym', label: 'Lip-corner asymmetry', unit: '', digits: 3, description: 'Corner offset across the midline, per eye width' },
  { key: 'lips', label: 'Lip aperture', unit: '', digits: 3, description: 'Mouth opening, per eye width' },
  { key: 'ear', label: 'Eye aspect ratio', unit: '', digits: 2, description: 'Eye opening; dips are blinks' },
];

function Sparkline({ spec }: { spec: SparkSpec }) {
  const chartRef = useRef<LineChart | null | undefined>(null);
  const valueRef = useRef<HTMLSpanElement>(null);
  const pool = useRef<XY[]>([]);

  const data = useMemo<ChartData<'line', XY[], unknown>>(
    () => ({ datasets: [{ data: [], borderColor: COLORS.scrub, borderWidth: 1.5, pointRadius: 0, tension: 0, spanGaps: false }] }),
    [],
  );
  const options = useMemo<ChartOptions<'line'>>(
    () => ({
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      parsing: false,
      normalized: true,
      events: [],
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { type: 'linear', min: -HISTORY_SECONDS, max: 0, display: false },
        y: { type: 'linear', display: false, grace: '10%' },
      },
    }),
    [],
  );

  useEffect(() => {
    const buffer = useTelemetryStore.getState().buffers[spec.key];
    return subscribeFrame(() => {
      const chart = chartRef.current;
      if (!chart) return;
      const last = fillWindow(buffer, epochMs() / 1000, pool.current, chart.data.datasets[0].data);
      chart.update('none');
      if (valueRef.current) valueRef.current.textContent = last === null ? '–' : last.toFixed(spec.digits);
    }, 10);
  }, [spec]);

  return (
    <div className="panel flex flex-col gap-1 p-3">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="font-sans text-step--1 font-medium text-ink" title={spec.description}>{spec.label}</h3>
        <p className="num text-step-1 font-medium text-ink">
          <span ref={valueRef}>–</span>
          {spec.unit && <span className="ml-1 text-step--1 font-normal text-ink-soft">{spec.unit}</span>}
        </p>
      </div>
      <div className="h-10">
        <Line ref={chartRef} data={data} options={options} aria-label={`${spec.label}, last 30 seconds`} role="img" />
      </div>
      <p className="text-step--1 text-ink-faint">{spec.description}</p>
    </div>
  );
}

export function BiomarkerSparklines() {
  return (
    <section aria-labelledby="biomarkers-heading">
      <h2 id="biomarkers-heading" className="mb-3 text-step-1 font-semibold">Biomarkers</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {SPARKLINES.map((spec) => (
          <Sparkline key={spec.key} spec={spec} />
        ))}
      </div>
    </section>
  );
}
