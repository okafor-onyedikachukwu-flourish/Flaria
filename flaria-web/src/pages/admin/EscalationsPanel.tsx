import { useEffect, useState } from 'react';

import { errorMessage } from '@/services/api';

import { adminApi, type Escalation } from './adminApi';
import { absolute, RED_FLAG_LABEL, relative, TRIAGE_LABEL } from './format';
import { Badge, LoadState, SectionHeader } from './ui';

export function EscalationsPanel() {
  const [openOnly, setOpenOnly] = useState(false);
  const [rows, setRows] = useState<Escalation[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRows(null);
    adminApi
      .escalations(openOnly)
      .then((r) => {
        setRows(r);
        setError(null);
      })
      .catch((err) => setError(errorMessage(err, 'Escalations couldn’t load.')));
  }, [openOnly]);

  return (
    <section aria-labelledby="esc-title">
      <SectionHeader
        title="Escalations"
        description="Results that told a patient to get emergency help or see a doctor. A result counts as followed up once a clinician writes a care note for that patient afterwards."
        actions={
          <label className="flex cursor-pointer items-center gap-2 text-step--1">
            <input type="checkbox" className="h-4 w-4 accent-scrub" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} />
            Only results awaiting follow-up
          </label>
        }
      />
      <div className="panel bg-white">
        <LoadState loading={!rows && !error} error={error} empty={rows?.length === 0} emptyText={openOnly ? 'Nothing is awaiting follow-up.' : 'No urgent results yet.'}>
          {rows && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[58rem] text-left text-step--1">
                <thead className="border-b border-rule bg-paper-card text-ink-soft">
                  <tr>
                    <th scope="col" className="px-4 py-2.5 font-medium">Result</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Patient</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Reported symptoms</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Care team</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Follow-up</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((e) => (
                    <tr key={e.session_id} className="border-b border-rule/70 align-top last:border-0">
                      <td className="px-4 py-3">
                        <Badge tone={e.triage === 'EMERGENCY' ? 'bad' : 'warn'}>{TRIAGE_LABEL[e.triage]}</Badge>
                        <p className="mt-1 font-medium">{e.headline}</p>
                        <p className="text-ink-faint" title={absolute(e.created_at)}>{relative(e.created_at)}</p>
                      </td>
                      <td className="px-4 py-3">
                        <p className="font-medium">{e.patient_name}</p>
                        <p className="text-ink-soft">{e.patient_email}</p>
                      </td>
                      <td className="px-4 py-3">
                        {e.red_flags.length ? e.red_flags.map((f) => RED_FLAG_LABEL[f] ?? f).join(', ') : <span className="text-ink-faint">None, measured change only</span>}
                      </td>
                      <td className="px-4 py-3">{e.clinicians ?? <Badge tone="warn">No clinician assigned</Badge>}</td>
                      <td className="px-4 py-3">
                        {e.followed_up_at ? (
                          <>
                            <Badge tone="good">Followed up</Badge>
                            <p className="mt-1 text-ink-soft">{e.followed_up_by}, {relative(e.followed_up_at).toLowerCase()}</p>
                          </>
                        ) : (
                          <Badge tone="bad">Awaiting follow-up</Badge>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </LoadState>
      </div>
    </section>
  );
}
