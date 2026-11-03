import { History } from 'lucide-react';
import { useEffect, useState } from 'react';

import { api, errorMessage } from '@/services/api';
import type { BaselineSummary, SessionSummary } from '@/types';

const dateFmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

function duration(s: SessionSummary): string {
  if (!s.ended_at) return 'Running';
  const secs = Math.max(0, (Date.parse(s.ended_at) - Date.parse(s.started_at)) / 1000);
  return secs < 90 ? `${secs.toFixed(0)} s` : `${(secs / 60).toFixed(1)} min`;
}

const STATUS_TEXT: Record<SessionSummary['status'], string> = { ACTIVE: 'Running', COMPLETED: 'Completed', ABORTED: 'Interrupted' };

interface Props {
  patientId: string | null;
  refreshKey: number;
  onView?: (sessionId: string) => void;
}

export function SessionHistory({ patientId, refreshKey, onView }: Props) {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [baseline, setBaseline] = useState<BaselineSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const params = patientId ? { patient_id: patientId } : {};
    Promise.all([
      api.get<SessionSummary[]>('/sessions', { params: { ...params, limit: 10 }, signal: controller.signal }),
      api.get<BaselineSummary>('/baseline', { params, signal: controller.signal }),
    ])
      .then(([s, b]) => {
        setSessions(s.data);
        setBaseline(b.data);
        setError(null);
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(errorMessage(err, 'Session history couldn’t load.'));
      });
    return () => controller.abort();
  }, [patientId, refreshKey]);

  return (
    <section className="panel p-5" aria-labelledby="history-heading">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="history-heading" className="flex items-center gap-2 text-step-1 font-semibold">
          <History size={18} aria-hidden /> Recent sessions
        </h2>
        {baseline && (
          <p className="text-step--1 text-ink-soft">
            {baseline.ready
              ? `Baseline ready from ${baseline.days_covered} day${baseline.days_covered === 1 ? '' : 's'} of readings`
              : 'Baseline still building. About one minute of steady speech completes it.'}
          </p>
        )}
      </div>
      {error && <p className="text-step--1 text-elevated" role="alert">{error}</p>}
      {!error && sessions === null && <p className="text-step--1 text-ink-soft">Loading sessions…</p>}
      {sessions?.length === 0 && (
        <p className="text-step--1 text-ink-soft">No sessions yet. Each session adds to the personal baseline that readings are compared with.</p>
      )}
      {sessions && sessions.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[40rem] text-left text-step--1">
            <thead className="border-b border-rule text-ink-soft">
              <tr>
                <th scope="col" className="py-2 pr-4 font-medium">Started</th>
                <th scope="col" className="py-2 pr-4 font-medium">Length</th>
                <th scope="col" className="py-2 pr-4 font-medium">Outcome</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">Mean index</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">Peak index</th>
                <th scope="col" className="py-2 font-medium"><span className="sr-only">Result</span></th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id} className="border-b border-rule/60 last:border-0">
                  <td className="py-2 pr-4">{dateFmt.format(new Date(s.started_at))}</td>
                  <td className="num py-2 pr-4">{duration(s)}</td>
                  <td className="py-2 pr-4">{STATUS_TEXT[s.status]}</td>
                  <td className="num py-2 pr-4 text-right">{s.mean_anomaly?.toFixed(2) ?? '–'}</td>
                  <td className="num py-2 pr-4 text-right">{s.peak_anomaly?.toFixed(2) ?? '–'}</td>
                  <td className="py-2 text-right">
                    {onView && s.status !== 'ACTIVE' && (
                      <button type="button" onClick={() => onView(s.id)} className="text-step--1 font-medium text-scrub-deep underline underline-offset-2 hover:text-ink">
                        View result
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
