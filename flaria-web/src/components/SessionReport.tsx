import { AlertOctagon, AlertTriangle, CheckCircle2, Clock3, FlaskConical, Info, LoaderCircle, Pill, Stethoscope } from 'lucide-react';
import { type FormEvent, type ReactNode, useEffect, useState } from 'react';

import { api, errorMessage } from '@/services/api';
import type { SessionReport as Report, Triage } from '@/types';

import { CareNotes } from './CareNotes';

const dateFmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

const DEMO_STYLE = {
  box: 'border-b border-rule bg-paper-deep text-ink',
  icon: <FlaskConical size={28} aria-hidden />,
  listMarker: 'marker:text-ink-soft',
};

const TRIAGE_STYLE: Record<Triage, { box: string; icon: ReactNode; listMarker: string }> = {
  EMERGENCY: { box: 'bg-elevated text-white', icon: <AlertOctagon size={30} aria-hidden />, listMarker: 'marker:text-white' },
  SEE_DOCTOR: { box: 'border-b border-watch/40 bg-watch-wash text-watch-ink', icon: <AlertTriangle size={28} aria-hidden />, listMarker: 'marker:text-watch' },
  DISCUSS: { box: 'border-b border-scrub/30 bg-scrub-wash text-scrub-deep', icon: <Info size={28} aria-hidden />, listMarker: 'marker:text-scrub' },
  NO_CHANGE: { box: 'border-b border-stable/30 bg-stable-wash text-stable', icon: <CheckCircle2 size={28} aria-hidden />, listMarker: 'marker:text-stable' },
  BASELINE: { box: 'border-b border-rule bg-paper-deep text-ink', icon: <Clock3 size={28} aria-hidden />, listMarker: 'marker:text-ink-faint' },
  INSUFFICIENT: { box: 'border-b border-rule bg-paper-deep text-ink', icon: <Info size={28} aria-hidden />, listMarker: 'marker:text-ink-faint' },
};

function sd(z: number): string {
  const v = Math.abs(z);
  return `${v >= 6 ? '6+' : v.toFixed(1)} SD`;
}

interface Props {
  report: Report;
  onChange: (report: Report) => void;
  canWriteNotes: boolean;
  patientName?: string;
}

export function SessionReport({ report, onChange, canWriteNotes, patientName }: Props) {
  // A demo result is neutral unless the person reported warning signs, which are always real.
  const demoResult = !!report.demo && report.red_flags.length === 0;
  const style = demoResult ? DEMO_STYLE : TRIAGE_STYLE[report.triage];
  const [flags, setFlags] = useState<string[]>(report.red_flags);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setFlags(report.red_flags), [report]);

  const submitFlags = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const { data } = await api.put<Report>(`/sessions/${report.session_id}/report/red-flags`, { red_flags: flags });
      onChange(data);
    } catch (err) {
      setError(errorMessage(err, 'Your answers couldn’t be saved. If you have any of these signs, call emergency services now.'));
    } finally {
      setSaving(false);
    }
  };

  const changed = flags.length !== report.red_flags.length || flags.some((f) => !report.red_flags.includes(f));

  return (
    <section className="panel overflow-hidden" aria-labelledby="result-heading">
      <div className={`p-5 sm:p-6 ${style.box}`} role={report.triage === 'EMERGENCY' && !demoResult ? 'alert' : undefined}>
        <div className="flex items-start gap-3">
          <span className="mt-1 shrink-0">{style.icon}</span>
          <div className="min-w-0">
            <p className="text-step--1 opacity-90">Result of the reading on {dateFmt.format(new Date(report.created_at))}</p>
            <h2 id="result-heading" className="mt-0.5 text-step-3 font-semibold">{report.headline}</h2>
            <ul className={`mt-3 list-disc space-y-1.5 pl-5 text-step-0 ${style.listMarker}`}>
              {report.advice.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </div>
        </div>
      </div>

      <div className="grid gap-8 p-5 sm:p-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <div>
          <h3 className="font-sans text-step-1 font-semibold">{report.demo ? 'What the demo measured' : 'What changed'}</h3>
          {report.findings.length === 0 ? (
            <p className="mt-2 text-step--1 text-ink-soft">
              {report.triage === 'BASELINE'
                ? 'Nothing to compare yet. Each reading adds to your personal baseline.'
                : report.triage === 'INSUFFICIENT'
                  ? 'The camera or microphone didn’t pick up enough to compare with your usual.'
                  : report.red_flags.length > 0
                    ? 'The measurements didn’t show a change, but the warning signs you reported need urgent attention.'
                    : 'Every measure stayed within your usual range.'}
            </p>
          ) : (
            <ul className="mt-3 space-y-5">
              {report.findings.map((f) => (
                <li key={f.id} className="border-l-2 border-scrub pl-4">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <h4 className="font-sans text-step-0 font-semibold">{f.title}</h4>
                    <span className={`rounded-full px-2 py-0.5 text-step--1 ${f.severity === 'strong' ? 'bg-watch-wash text-watch-ink' : 'bg-paper-deep text-ink-soft'}`}>
                      {f.severity === 'strong' ? 'Large change' : 'Moderate change'}
                    </span>
                  </div>
                  <p className="mt-1 text-step--1 text-ink-soft">{f.summary}</p>
                  {f.measures.length > 0 && (
                    <p className="num mt-1 text-step--1 text-ink-soft">
                      {f.measures.map((m) => `${m.name}: ${sd(m.z)} ${m.usual}`).join('; ')}
                    </p>
                  )}
                  {/* Causes stay hidden in demo mode: a test model shouldn't put "stroke" in front of a healthy visitor. */}
                  {!report.demo && (
                    <>
                      <p className="mt-2 text-step--1 font-medium">This can be caused by</p>
                      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-step--1 text-ink-soft">
                        {f.possible_causes.map((c) => (
                          <li key={c}>{c}</li>
                        ))}
                      </ul>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
          {report.findings.length > 0 && !report.demo && (
            <p className="mt-5 rounded-md bg-paper-deep p-3 text-step--1 text-ink-soft">
              These are possible explanations, not a diagnosis. A voice and face reading can’t tell these causes apart; a clinician
              who examines you can.
            </p>
          )}
        </div>

        <div className="space-y-8">
          <form onSubmit={submitFlags} aria-labelledby="flags-heading">
            <h3 id="flags-heading" className="font-sans text-step-1 font-semibold">Do you have any of these right now?</h3>
            <p className="mt-1 text-step--1 text-ink-soft">Tick any that apply. They matter more than any measurement.</p>
            <fieldset className="mt-3 space-y-2">
              <legend className="sr-only">Warning signs</legend>
              {Object.entries(report.red_flag_options).map(([key, label]) => (
                <label key={key} className="flex cursor-pointer items-start gap-2.5 text-step--1">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 shrink-0 accent-elevated"
                    checked={flags.includes(key)}
                    onChange={(e) => setFlags((prev) => (e.target.checked ? [...prev, key] : prev.filter((k) => k !== key)))}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </fieldset>
            {error && <p className="mt-2 text-step--1 text-elevated" role="alert">{error}</p>}
            <button type="submit" className="btn-quiet mt-3 py-2 text-step--1" disabled={saving || !changed}>
              {saving && <LoaderCircle size={16} className="animate-spin" aria-hidden />}
              {saving ? 'Updating result…' : 'Update result'}
            </button>
          </form>

          <div aria-labelledby="medicines-heading">
            <h3 id="medicines-heading" className="flex items-center gap-2 font-sans text-step-1 font-semibold">
              <Pill size={18} aria-hidden /> Medicines
            </h3>
            <p className="mt-1 text-step--1 text-ink-soft">
              Flaria doesn’t suggest medicines. The same reading can come from conditions that need very different treatment, so only
              a clinician who knows your history and current medicines should advise you.
            </p>
            <h4 className="mt-4 flex items-center gap-1.5 font-sans text-step--1 font-semibold">
              <Stethoscope size={15} aria-hidden /> From your care team
            </h4>
            <div className="mt-2">
              <CareNotes patientId={report.patient_id} canWrite={canWriteNotes} patientName={patientName} />
            </div>
          </div>
        </div>
      </div>

      <p className="border-t border-rule px-5 py-3 text-step--1 text-ink-faint sm:px-6">
        Compared with your own earlier readings, not with other people. Model {report.summary.model_version}; rules {report.rules_version}.
      </p>
    </section>
  );
}
