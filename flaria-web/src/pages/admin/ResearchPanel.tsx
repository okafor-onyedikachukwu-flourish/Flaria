import axios from 'axios';
import { Download, LoaderCircle, ShieldAlert, ShieldCheck } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { LABEL_TEXT, research, saveBlob, type ParticipantRow, type Study } from '@/lib/research';
import { errorMessage } from '@/services/api';

import { relative } from './format';
import { Badge, LoadState, SectionHeader, useToast } from './ui';

const STATE: Record<ParticipantRow['state'], { text: string; tone: 'good' | 'warn' | 'bad' | 'neutral' }> = {
  consented: { text: 'Taking part', tone: 'good' },
  needs_reconsent: { text: 'Must re-consent', tone: 'warn' },
  withdrawn: { text: 'Withdrawn', tone: 'bad' },
  not_joined: { text: 'Not joined', tone: 'neutral' },
};

export function ResearchPanel() {
  const toast = useToast();
  const [study, setStudy] = useState<Study | null>(null);
  const [rows, setRows] = useState<ParticipantRow[] | null>(null);
  const [off, setOff] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    research
      .participants()
      .then((d) => {
        setStudy(d.study);
        setRows(d.participants);
      })
      .catch((err) => {
        if (axios.isAxiosError(err) && err.response?.status === 404) setOff(true);
        else setError(errorMessage(err, 'Research data couldn’t load.'));
      });
  }, []);

  const stats = useMemo(() => {
    const r = rows ?? [];
    const taking = r.filter((p) => p.state === 'consented');
    return {
      taking: taking.length,
      labelled: taking.filter((p) => p.label).length,
      withdrawn: r.filter((p) => p.state === 'withdrawn').length,
      sessions: taking.reduce((n, p) => n + p.sessions, 0),
    };
  }, [rows]);

  const doExport = async () => {
    setExporting(true);
    try {
      const res = await research.exportZip();
      const name = /filename="([^"]+)"/.exec(String(res.headers['content-disposition'] ?? ''))?.[1] ?? 'flaria-research-export.zip';
      saveBlob(res.data, name);
      toast('Export downloaded. It contains study codes only, no names or emails.');
    } catch (err) {
      toast(errorMessage(err, 'The export failed.'), 'bad');
    } finally {
      setExporting(false);
    }
  };

  if (off) {
    return (
      <section aria-labelledby="research-title">
        <SectionHeader title="Research" description="Consent, clinician labels and de-identified exports for a research study." />
        <div className="panel bg-white p-5 text-step--1">
          <p className="font-medium">Research mode is off.</p>
          <p className="mt-2 text-ink-soft">
            To turn it on, set <code className="num">FLARIA_RESEARCH_MODE=true</code> on the flaria-app service in Render, plus the study details:
            <code className="num"> FLARIA_STUDY_TITLE</code>, <code className="num">FLARIA_STUDY_INSTITUTION</code>,
            <code className="num"> FLARIA_STUDY_INVESTIGATOR</code>, <code className="num">FLARIA_STUDY_CONTACT_EMAIL</code> and, once approved,
            <code className="num"> FLARIA_STUDY_ETHICS_REFERENCE</code>.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section aria-labelledby="research-title">
      <SectionHeader
        title="Research"
        description="Participants who consented, the groups their clinicians confirmed, and a de-identified export for training and analysis."
        actions={
          <button type="button" className="btn-primary py-2" onClick={() => void doExport()} disabled={exporting || !rows}>
            {exporting ? <LoaderCircle size={16} className="animate-spin" aria-hidden /> : <Download size={16} aria-hidden />}
            Download export
          </button>
        }
      />
      <LoadState loading={!rows && !error} error={error}>
        {study && rows && (
          <div className="space-y-6">
            <div className="panel bg-white p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="font-display text-step-1 font-semibold">{study.title}</p>
                  <p className="mt-1 text-step--1 text-ink-soft">
                    {[study.investigator, study.institution].filter(Boolean).join(', ') || 'Investigator and institution not set'}
                    {study.contact_email ? `, ${study.contact_email}` : ''}
                  </p>
                </div>
                {study.approved ? (
                  <Badge tone="good"><ShieldCheck size={13} aria-hidden /> Ethics approval {study.ethics_reference}</Badge>
                ) : (
                  <Badge tone="warn"><ShieldAlert size={13} aria-hidden /> No ethics approval yet</Badge>
                )}
              </div>
              {!study.approved && (
                <p className="mt-3 text-step--1 text-watch-ink">
                  Data collected before approval must not be used for research or publication. Add the approval number as
                  FLARIA_STUDY_ETHICS_REFERENCE when you have it; participants will then be asked to consent again.
                </p>
              )}
              <p className="mt-3 text-step--1 text-ink-faint">Consent version {study.version}</p>
            </div>

            <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-instrument border border-rule bg-rule md:grid-cols-4">
              {[
                ['Taking part', stats.taking],
                ['Labelled by a clinician', stats.labelled],
                ['Sessions recorded', stats.sessions],
                ['Withdrawn', stats.withdrawn],
              ].map(([k, v]) => (
                <div key={k} className="bg-white px-5 py-4">
                  <dt className="text-step--1 text-ink-soft">{k}</dt>
                  <dd className="num mt-1 font-display text-step-3 font-semibold">{v}</dd>
                </div>
              ))}
            </dl>

            <div className="panel bg-white">
              <LoadState loading={false} error={null} empty={rows.length === 0} emptyText="No participant accounts yet.">
                <div className="relative overflow-x-auto">
                  <table className="w-full min-w-[46rem] text-left text-step--1">
                    <thead className="border-b border-rule bg-paper-card text-ink-soft">
                      <tr>
                        <th scope="col" className="px-4 py-2.5 font-medium">Study code</th>
                        <th scope="col" className="px-4 py-2.5 font-medium">Person</th>
                        <th scope="col" className="px-4 py-2.5 font-medium">Status</th>
                        <th scope="col" className="px-4 py-2.5 font-medium">Confirmed group</th>
                        <th scope="col" className="px-4 py-2.5 text-right font-medium">Sessions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((p) => (
                        <tr key={p.id} className="border-b border-rule/70 last:border-0">
                          <td className="num px-4 py-3 font-medium">{p.study_code ?? '–'}</td>
                          <td className="px-4 py-3">
                            <p className="font-medium">{p.full_name}</p>
                            <p className="text-ink-soft">{p.email}</p>
                          </td>
                          <td className="px-4 py-3">
                            <Badge tone={STATE[p.state].tone}>{STATE[p.state].text}</Badge>
                            {p.consented_at && p.state === 'consented' && <p className="mt-1 text-ink-faint">since {relative(p.consented_at).toLowerCase()}</p>}
                          </td>
                          <td className="px-4 py-3">
                            {p.label ? `${LABEL_TEXT[p.label] ?? p.label}${p.severity ? `, ${p.severity}` : ''}` : <span className="text-ink-faint">Not labelled</span>}
                          </td>
                          <td className="num px-4 py-3 text-right">{p.sessions}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </LoadState>
            </div>
            <p className="text-step--1 text-ink-faint">
              The export includes only people currently taking part, under their study codes, with day numbers instead of dates.
              Clinicians’ notes are never included.
            </p>
          </div>
        )}
      </LoadState>
    </section>
  );
}
