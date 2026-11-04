/** Client for /api/v1/research (see flaria-core/app/api/research.py). */
import { api } from '@/services/api';

export interface StudySection { heading: string; body: string }
export interface Study {
  research_mode: boolean;
  title: string;
  institution: string;
  investigator: string;
  contact_email: string;
  ethics_reference: string;
  approved: boolean;
  sections: StudySection[];
  agreements: string[];
  version: string;
}
export interface MyParticipation {
  eligible: boolean;
  consented: boolean;
  needs_reconsent: boolean;
  consented_at: string | null;
  study_code: string | null;
  current_version: string;
}
export interface ResearchLabel {
  label: string;
  severity: 'mild' | 'moderate' | 'severe' | null;
  detail: string | null;
  recorded_at: string;
  recorded_by_name: string | null;
}
export interface ParticipantRow {
  id: string;
  full_name: string;
  email: string;
  study_code: string | null;
  consented_at: string | null;
  label: string | null;
  severity: string | null;
  label_recorded_at: string | null;
  sessions: number;
  state: 'consented' | 'needs_reconsent' | 'withdrawn' | 'not_joined';
}

export const LABEL_TEXT: Record<string, string> = {
  healthy_control: 'Healthy control',
  stroke: 'Stroke',
  als: 'ALS (motor neurone disease)',
  parkinsons: "Parkinson's disease",
  bells_palsy: "Bell's palsy / facial nerve palsy",
  respiratory: 'Respiratory condition',
  other: 'Other confirmed condition',
};

export const research = {
  study: () => api.get<Study>('/research/study').then((r) => r.data),
  me: () => api.get<MyParticipation>('/research/me').then((r) => r.data),
  consent: (version: string) => api.post('/research/consent', { consent_version: version, agreements_accepted: true }),
  withdraw: () => api.post('/research/withdraw'),
  deleteMyData: () => api.delete('/research/me/data'),
  label: (patientId: string) =>
    api
      .get<{ label: ResearchLabel | null; consented: boolean; options: { id: string; label: string }[] }>(`/research/participants/${patientId}/label`)
      .then((r) => r.data),
  setLabel: (patientId: string, body: { label: string; severity: string | null; detail: string | null }) =>
    api.put(`/research/participants/${patientId}/label`, body),
  participants: () => api.get<{ study: Study; participants: ParticipantRow[] }>('/research/participants').then((r) => r.data),
  exportZip: () => api.get<Blob>('/research/export', { responseType: 'blob' }),
};

/** Save a Blob as a file through a temporary link (works in mobile browsers too). */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
