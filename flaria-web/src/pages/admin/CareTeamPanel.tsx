import { UserMinus, UserPlus } from 'lucide-react';
import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react';

import { errorMessage } from '@/services/api';

import { adminApi, type CareLink, type Person } from './adminApi';
import { relative } from './format';
import { LoadState, SectionHeader, useConfirm, useToast } from './ui';

export function CareTeamPanel() {
  const confirm = useConfirm();
  const toast = useToast();
  const [links, setLinks] = useState<CareLink[] | null>(null);
  const [clinicians, setClinicians] = useState<Person[]>([]);
  const [patients, setPatients] = useState<Person[]>([]);
  const [clinicianId, setClinicianId] = useState('');
  const [patientId, setPatientId] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    Promise.all([adminApi.careTeam(), adminApi.people('CLINICIAN'), adminApi.people('PATIENT')])
      .then(([l, c, p]) => {
        setLinks(l);
        setClinicians(c);
        setPatients(p);
        setError(null);
      })
      .catch((err) => setError(errorMessage(err, 'Care teams couldn’t load.')));
  }, []);
  useEffect(load, [load]);

  const grouped = useMemo(() => {
    const map = new Map<string, { name: string; email: string; patients: CareLink[] }>();
    for (const c of clinicians) map.set(c.id, { name: c.full_name, email: c.email, patients: [] });
    for (const l of links ?? []) {
      const entry = map.get(l.clinician_id) ?? { name: l.clinician_name, email: l.clinician_email, patients: [] };
      entry.patients.push(l);
      map.set(l.clinician_id, entry);
    }
    return [...map.entries()];
  }, [links, clinicians]);

  const unassigned = useMemo(() => {
    const covered = new Set((links ?? []).map((l) => l.patient_id));
    return patients.filter((p) => !covered.has(p.id));
  }, [links, patients]);

  const assign = async (e: FormEvent) => {
    e.preventDefault();
    if (!clinicianId || !patientId) return;
    setSaving(true);
    try {
      await adminApi.assign(clinicianId, patientId);
      toast('Clinician assigned. They can now see this patient’s readings and write care notes.');
      setPatientId('');
      load();
    } catch (err) {
      toast(errorMessage(err, 'The assignment didn’t save.'), 'bad');
    } finally {
      setSaving(false);
    }
  };

  const remove = async (l: CareLink) => {
    const ok = await confirm({
      title: `Remove ${l.patient_name} from ${l.clinician_name}’s care team?`,
      body: `${l.clinician_name} will no longer see ${l.patient_name}’s readings or be able to write care notes for them. Existing notes stay on the record.`,
      confirm: 'Remove from care team',
      danger: true,
    });
    if (!ok) return;
    try {
      await adminApi.unassign(l.clinician_id, l.patient_id);
      toast('Removed from care team.');
      load();
    } catch (err) {
      toast(errorMessage(err, 'That change didn’t save.'), 'bad');
    }
  };

  return (
    <section aria-labelledby="care-title">
      <SectionHeader title="Care teams" description="Clinicians only see patients assigned to them. Each assignment lets the clinician read results and write the care notes patients see." />
      <form onSubmit={assign} className="panel mb-6 flex flex-wrap items-end gap-3 bg-white p-4">
        <label className="min-w-[14rem] flex-1">
          <span className="mb-1 block text-step--1 font-medium">Clinician</span>
          <select className="field py-2" value={clinicianId} onChange={(e) => setClinicianId(e.target.value)} required>
            <option value="">Choose a clinician</option>
            {clinicians.map((c) => <option key={c.id} value={c.id}>{c.full_name}</option>)}
          </select>
        </label>
        <label className="min-w-[14rem] flex-1">
          <span className="mb-1 block text-step--1 font-medium">Patient</span>
          <select className="field py-2" value={patientId} onChange={(e) => setPatientId(e.target.value)} required>
            <option value="">Choose a patient</option>
            {patients.map((p) => <option key={p.id} value={p.id}>{p.full_name} ({p.email})</option>)}
          </select>
        </label>
        <button type="submit" className="btn-primary py-2" disabled={saving || !clinicianId || !patientId}>
          <UserPlus size={16} aria-hidden /> Assign
        </button>
      </form>

      {unassigned.length > 0 && (
        <p className="mb-6 rounded-md border border-watch/40 bg-watch-wash px-4 py-3 text-step--1 text-watch-ink" role="status">
          {unassigned.length} patient{unassigned.length === 1 ? ' has' : 's have'} no clinician, so nobody will see their urgent results:{' '}
          {unassigned.map((p) => p.full_name).join(', ')}.
        </p>
      )}

      <LoadState loading={!links && !error} error={error} empty={grouped.length === 0} emptyText="No clinicians yet. Change a user’s role to Clinician first.">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {grouped.map(([id, g]) => (
            <section key={id} className="panel bg-white" aria-labelledby={`clin-${id}`}>
              <div className="flex items-baseline justify-between gap-2 border-b border-rule px-4 py-3">
                <div>
                  <h2 id={`clin-${id}`} className="font-sans text-step-0 font-semibold">{g.name}</h2>
                  <p className="text-step--1 text-ink-soft">{g.email}</p>
                </div>
                <p className="num text-step--1 text-ink-soft">{g.patients.length} patient{g.patients.length === 1 ? '' : 's'}</p>
              </div>
              {g.patients.length === 0 ? (
                <p className="px-4 py-4 text-step--1 text-ink-soft">No patients assigned.</p>
              ) : (
                <ul className="divide-y divide-rule/70">
                  {g.patients.map((l) => (
                    <li key={l.patient_id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                      <div>
                        <p className="text-step--1 font-medium">{l.patient_name}</p>
                        <p className="text-step--1 text-ink-faint">Assigned {relative(l.created_at).toLowerCase()}</p>
                      </div>
                      <button type="button" className="btn-quiet px-2.5 py-1.5 text-step--1" onClick={() => void remove(l)}>
                        <UserMinus size={14} aria-hidden /> Remove
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ))}
        </div>
      </LoadState>
    </section>
  );
}
