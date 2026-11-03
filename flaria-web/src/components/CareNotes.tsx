import { LoaderCircle, Stethoscope } from 'lucide-react';
import { type FormEvent, useEffect, useId, useState } from 'react';

import { api, errorMessage } from '@/services/api';
import type { CareNote } from '@/types';

const dateFmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

interface Props {
  patientId: string;
  canWrite: boolean;
  patientName?: string;
}

/**
 * Advice written by the patient's own care team. This is where medication
 * guidance belongs: Flaria never generates it.
 */
export function CareNotes({ patientId, canWrite, patientName }: Props) {
  const [notes, setNotes] = useState<CareNote[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const fieldId = useId();

  useEffect(() => {
    const controller = new AbortController();
    setNotes(null);
    api
      .get<CareNote[]>(`/patients/${patientId}/care-notes`, { signal: controller.signal })
      .then(({ data }) => {
        setNotes(data);
        setError(null);
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(errorMessage(err, 'Care team notes couldn’t load.'));
      });
    return () => controller.abort();
  }, [patientId]);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!draft.trim()) return;
    setSaving(true);
    try {
      const { data } = await api.post<CareNote>(`/patients/${patientId}/care-notes`, { body: draft });
      setNotes((prev) => [data, ...(prev ?? [])]);
      setDraft('');
      setError(null);
    } catch (err) {
      setError(errorMessage(err, 'The note couldn’t be saved.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      {canWrite && (
        <form onSubmit={save} className="space-y-2">
          <label htmlFor={fieldId} className="block text-step--1 font-medium">
            Advice for {patientName ?? 'this patient'}
          </label>
          <textarea
            id={fieldId}
            className="field min-h-[6rem] resize-y"
            maxLength={4000}
            placeholder="For example: medicine changes, what to do if a reading is raised, when to book a review."
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button type="submit" className="btn-primary py-2 text-step--1" disabled={saving || !draft.trim()}>
            {saving && <LoaderCircle size={16} className="animate-spin" aria-hidden />}
            {saving ? 'Saving note…' : 'Save note'}
          </button>
        </form>
      )}
      {error && <p className="text-step--1 text-elevated" role="alert">{error}</p>}
      {!error && notes === null && <p className="text-step--1 text-ink-soft">Loading notes…</p>}
      {notes?.length === 0 && (
        <p className="text-step--1 text-ink-soft">
          {canWrite ? 'No notes yet. Notes appear in the patient’s results.' : 'No advice from your care team yet. Ask your clinician at your next appointment.'}
        </p>
      )}
      {notes && notes.length > 0 && (
        <ul className="space-y-3">
          {notes.map((n) => (
            <li key={n.id} className="rounded-md border border-rule bg-white p-3">
              <p className="whitespace-pre-line text-step--1 text-ink">{n.body}</p>
              <p className="mt-2 flex items-center gap-1.5 text-step--1 text-ink-soft">
                <Stethoscope size={14} aria-hidden />
                {n.author_name ?? 'Care team'}, {dateFmt.format(new Date(n.created_at))}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
