import { CircleCheck, FlaskConical, LoaderCircle } from 'lucide-react';
import { useCallback, useEffect, useId, useState } from 'react';

import { LABEL_TEXT, research, type ResearchLabel } from '@/lib/research';
import { errorMessage } from '@/services/api';

const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

/** Clinician-confirmed group for a participant: the label a research model learns from. */
export function ResearchLabelPanel({ patientId, patientName }: { patientId: string; patientName?: string }) {
  const id = useId();
  const [current, setCurrent] = useState<ResearchLabel | null>(null);
  const [consented, setConsented] = useState<boolean | null>(null);
  const [options, setOptions] = useState<{ id: string; label: string }[]>([]);
  const [label, setLabel] = useState('');
  const [severity, setSeverity] = useState('');
  const [detail, setDetail] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ tone: 'good' | 'bad'; text: string } | null>(null);

  const load = useCallback(() => {
    research
      .label(patientId)
      .then((d) => {
        setCurrent(d.label);
        setConsented(d.consented);
        setOptions(d.options);
        setLabel(d.label?.label ?? '');
        setSeverity(d.label?.severity ?? '');
        setDetail('');
      })
      .catch((err) => setMessage({ tone: 'bad', text: errorMessage(err, 'The research label couldn’t load.') }));
  }, [patientId]);
  useEffect(load, [load]);

  const save = async () => {
    setSaving(true);
    setMessage(null);
    try {
      await research.setLabel(patientId, { label, severity: severity || null, detail: detail.trim() || null });
      setMessage({ tone: 'good', text: 'Label saved. It applies to this participant’s data from now on.' });
      load();
    } catch (err) {
      setMessage({ tone: 'bad', text: errorMessage(err, 'The label couldn’t be saved.') });
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="panel bg-white p-5" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className="flex items-center gap-2 text-step-1 font-semibold">
        <FlaskConical size={18} className="text-scrub" aria-hidden /> Research label for {patientName ?? 'this participant'}
      </h2>
      <p className="mt-1 text-step--1 text-ink-soft">
        Record the group you’ve confirmed clinically. Models are trained against this label, so set it only from a confirmed diagnosis.
      </p>
      {consented === false && (
        <p className="mt-3 rounded-md border border-watch/40 bg-watch-wash px-3 py-2 text-step--1 text-watch-ink">
          This person hasn’t joined the study, so their data isn’t exported. You can still record the label.
        </p>
      )}
      {current && (
        <p className="mt-3 text-step--1">
          Current: <span className="font-medium">{LABEL_TEXT[current.label] ?? current.label}</span>
          {current.severity ? `, ${current.severity}` : ''}
          <span className="text-ink-faint"> (by {current.recorded_by_name ?? 'unknown'}, {fmt.format(new Date(current.recorded_at))})</span>
        </p>
      )}
      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className="text-step--1">
          <span className="mb-1 block font-medium">Confirmed group</span>
          <select className="field py-2" value={label} onChange={(e) => setLabel(e.target.value)}>
            <option value="">Choose…</option>
            {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
        </label>
        <label className="text-step--1">
          <span className="mb-1 block font-medium">Severity (optional)</span>
          <select className="field py-2" value={severity} onChange={(e) => setSeverity(e.target.value)} disabled={label === 'healthy_control'}>
            <option value="">Not recorded</option>
            <option value="mild">Mild</option>
            <option value="moderate">Moderate</option>
            <option value="severe">Severe</option>
          </select>
        </label>
        <label className="text-step--1 sm:col-span-2">
          <span className="mb-1 block font-medium">Clinical note (optional, internal only)</span>
          <textarea className="field min-h-[4.5rem]" maxLength={500} value={detail} onChange={(e) => setDetail(e.target.value)}
            placeholder="For example how the diagnosis was confirmed. Never included in research exports." />
        </label>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3" aria-live="polite">
        <button type="button" className="btn-primary py-2" disabled={!label || saving} onClick={() => void save()}>
          {saving && <LoaderCircle size={16} className="animate-spin" aria-hidden />} Save label
        </button>
        {message && (
          <p className={`flex items-center gap-1.5 text-step--1 ${message.tone === 'good' ? 'text-stable' : 'text-elevated'}`}>
            {message.tone === 'good' && <CircleCheck size={15} aria-hidden />} {message.text}
          </p>
        )}
      </div>
    </section>
  );
}
