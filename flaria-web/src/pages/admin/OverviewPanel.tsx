import { BarController, BarElement, CategoryScale, Chart as ChartJS, Legend, LinearScale, Tooltip, type ChartOptions } from 'chart.js';
import { ArrowRight, Cpu, Database, Radio } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Bar } from 'react-chartjs-2';

import { errorMessage } from '@/services/api';

import { adminApi, type Escalation, type Overview, type Triage } from './adminApi';
import { duration, relative, shortDay, TRIAGE_LABEL } from './format';
import { Badge, LoadState, SectionHeader } from './ui';

ChartJS.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip, Legend);

const SERIES: { key: Triage; color: string }[] = [
  { key: 'NO_CHANGE', color: '#2F7A4A' },
  { key: 'BASELINE', color: '#B9C6CB' },
  { key: 'DISCUSS', color: '#E8C27A' },
  { key: 'SEE_DOCTOR', color: '#C27C0E' },
  { key: 'EMERGENCY', color: '#B4344A' },
  { key: 'INSUFFICIENT', color: '#71818C' },
];

function Stat({ label, value, note, tone }: { label: string; value: string | number; note?: string; tone?: 'bad' | 'warn' }) {
  const color = tone === 'bad' ? 'text-elevated' : tone === 'warn' ? 'text-watch-ink' : 'text-ink';
  return (
    <div className="bg-white px-5 py-4">
      <dt className="text-step--1 text-ink-soft">{label}</dt>
      <dd className={`num mt-1 font-display text-step-4 font-semibold leading-none ${color}`}>{value}</dd>
      {note && <dd className="mt-1.5 text-step--1 text-ink-faint">{note}</dd>}
    </div>
  );
}

export function OverviewPanel({ onNavigate }: { onNavigate: (section: string) => void }) {
  const [data, setData] = useState<Overview | null>(null);
  const [open, setOpen] = useState<Escalation[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      Promise.all([adminApi.overview(), adminApi.escalations(true)])
        .then(([o, e]) => {
          if (!alive) return;
          setData(o);
          setOpen(e.slice(0, 5));
          setError(null);
        })
        .catch((err) => alive && setError(errorMessage(err, 'The overview couldn’t load.')));
    void load();
    const timer = setInterval(load, 30_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const chart = useMemo(() => {
    if (!data) return null;
    return {
      labels: data.daily_results.map((d) => shortDay(d.day)),
      datasets: SERIES.map((s) => ({
        label: TRIAGE_LABEL[s.key],
        data: data.daily_results.map((d) => d.counts[s.key] ?? 0),
        backgroundColor: s.color,
        borderRadius: 2,
        maxBarThickness: 28,
        stack: 'results',
      })),
    };
  }, [data]);

  const options = useMemo<ChartOptions<'bar'>>(() => ({
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    plugins: {
      legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, color: '#4A5A66', font: { family: 'IBM Plex Sans', size: 12 } } },
      tooltip: { mode: 'index', intersect: false },
    },
    scales: {
      x: { stacked: true, grid: { display: false }, ticks: { color: '#71818C', font: { family: 'IBM Plex Sans', size: 11 } } },
      y: { stacked: true, beginAtZero: true, ticks: { precision: 0, color: '#71818C' }, grid: { color: 'rgba(201, 212, 216, 0.6)' } },
    },
  }), []);

  const u = data?.users.by_role;
  const results = data ? Object.values(data.results_7d).reduce((a, b) => a + (b ?? 0), 0) : 0;
  const sys = data?.system;

  return (
    <section aria-labelledby="overview-title">
      <SectionHeader title="Overview" description="Activity across Flaria over the last 7 days. Refreshes every 30 seconds." />
      <LoadState loading={!data && !error} error={error}>
        {data && u && sys && (
          <div className="space-y-6">
            <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-instrument border border-rule bg-rule md:grid-cols-3 xl:grid-cols-6">
              <Stat label="Patients" value={u.PATIENT.active} note={`${data.sessions.patients_7d} ran a session this week`} />
              <Stat label="Clinicians" value={u.CLINICIAN.active} note={`${u.ADMIN.active} admin${u.ADMIN.active === 1 ? '' : 's'}`} />
              <Stat label="Sessions, 7 days" value={data.sessions.last_7d} note={`${data.sessions.last_24h} in the last 24 hours`} />
              <Stat label="Live now" value={data.sessions.active} note={`${sys.live_streams} stream${sys.live_streams === 1 ? '' : 's'} on this server`} />
              <Stat label="Awaiting follow-up" value={data.open_escalations} note="Urgent results without a care note" tone={data.open_escalations ? 'bad' : undefined} />
              <Stat label="Locked accounts" value={data.users.locked} note={`${data.users.new_7d} new account${data.users.new_7d === 1 ? '' : 's'} this week`} tone={data.users.locked ? 'warn' : undefined} />
            </dl>

            <div className="grid grid-cols-1 gap-6 xl:grid-cols-12">
              <section className="panel bg-white p-5 xl:col-span-8" aria-labelledby="results-chart-title">
                <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
                  <h2 id="results-chart-title" className="text-step-1 font-semibold">Session results, last 14 days</h2>
                  <p className="text-step--1 text-ink-soft">{results} results this week</p>
                </div>
                <div className="h-72">{chart && <Bar data={chart} options={options} aria-label="Stacked bar chart of session results per day by outcome" role="img" />}</div>
              </section>

              <section className="panel bg-white p-5 xl:col-span-4" aria-labelledby="open-title">
                <div className="mb-3 flex items-baseline justify-between gap-2">
                  <h2 id="open-title" className="text-step-1 font-semibold">Needs follow-up</h2>
                  <button type="button" onClick={() => onNavigate('escalations')} className="inline-flex items-center gap-1 text-step--1 font-medium text-scrub-deep hover:underline">
                    All escalations <ArrowRight size={14} aria-hidden />
                  </button>
                </div>
                {open.length === 0 ? (
                  <p className="py-6 text-step--1 text-ink-soft">Every urgent result has a care note. Nothing is waiting.</p>
                ) : (
                  <ul className="divide-y divide-rule">
                    {open.map((e) => (
                      <li key={e.session_id} className="py-3">
                        <div className="flex items-center justify-between gap-2">
                          <p className="font-medium">{e.patient_name}</p>
                          <Badge tone={e.triage === 'EMERGENCY' ? 'bad' : 'warn'}>{TRIAGE_LABEL[e.triage]}</Badge>
                        </div>
                        <p className="mt-0.5 text-step--1 text-ink-soft">
                          {relative(e.created_at)}. {e.clinicians ? `Care team: ${e.clinicians}` : 'No clinician assigned'}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>

            <section className="panel bg-white p-5" aria-labelledby="health-title">
              <div className="mb-4 flex items-baseline justify-between gap-2">
                <h2 id="health-title" className="text-step-1 font-semibold">Service health</h2>
                <button type="button" onClick={() => onNavigate('system')} className="inline-flex items-center gap-1 text-step--1 font-medium text-scrub-deep hover:underline">
                  System details <ArrowRight size={14} aria-hidden />
                </button>
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                <div className="flex items-start gap-3">
                  <Cpu size={18} className="mt-0.5 text-scrub" aria-hidden />
                  <div>
                    <p className="font-medium">Analysis model</p>
                    {sys.model ? (
                      <p className="text-step--1 text-ink-soft">{sys.model.version} on {sys.model.provider.replace('ExecutionProvider', '')}</p>
                    ) : (
                      <p className="text-step--1 text-elevated">Not loaded: {sys.model_error}</p>
                    )}
                    {sys.model?.version.startsWith('STUB') && <p className="mt-1"><Badge tone="warn">Test stub, not for patients</Badge></p>}
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <Database size={18} className="mt-0.5 text-scrub" aria-hidden />
                  <div>
                    <p className="font-medium">Telemetry storage</p>
                    <p className="text-step--1 text-ink-soft">
                      {sys.telemetry.written.toLocaleString()} rows written since start, {sys.telemetry.backlog} waiting, {sys.telemetry.dropped} dropped
                    </p>
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <Radio size={18} className="mt-0.5 text-scrub" aria-hidden />
                  <div>
                    <p className="font-medium">API server</p>
                    <p className="text-step--1 text-ink-soft">Version {sys.version}, up {duration(sys.uptime_seconds)}, {sys.environment}</p>
                  </div>
                </div>
              </div>
            </section>
          </div>
        )}
      </LoadState>
    </section>
  );
}
