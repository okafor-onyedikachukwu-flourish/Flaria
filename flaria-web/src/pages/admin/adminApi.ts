/** Typed client for /api/v1/admin. All calls go through the authenticated axios instance. */
import { api } from '@/services/api';
import type { Role } from '@/types';

export type Triage = 'EMERGENCY' | 'SEE_DOCTOR' | 'DISCUSS' | 'NO_CHANGE' | 'BASELINE' | 'INSUFFICIENT';

export interface RuntimeInfo {
  environment: string;
  version: string;
  uptime_seconds: number;
  model: { version: string; provider: string; seq_len: number; threshold_watch: number; threshold_elevated: number } | null;
  model_error: string | null;
  live_streams: number;
  telemetry: { backlog: number; written: number; dropped: number; consecutive_failures: number };
  config: {
    tick_hz: number;
    persist_hz: number;
    audio_ring_seconds: number;
    access_token_minutes: number;
    refresh_token_days: number;
    login_max_failures: number;
    min_baseline_seconds: number;
  };
}

export interface Overview {
  users: { by_role: Record<Role, { active: number; disabled: number }>; locked: number; new_7d: number };
  sessions: { active: number; last_24h: number; last_7d: number; aborted_7d: number; patients_7d: number };
  results_7d: Partial<Record<Triage, number>>;
  daily_results: { day: string; counts: Partial<Record<Triage, number>> }[];
  open_escalations: number;
  system: RuntimeInfo;
}

export interface SystemInfo extends RuntimeInfo {
  database: {
    reachable: boolean;
    db_bytes: number;
    telemetry_rows: number;
    telemetry_partitions: number;
    live_refresh_sessions: number;
    last_telemetry_at: string | null;
    server_version: string;
  };
}

export interface AdminUser {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  locked: boolean;
  failed_login_count: number;
  created_at: string;
  last_login_at: string | null;
  session_count: number;
  last_session_at: string | null;
  care_links: number;
}

export interface UserPage { items: AdminUser[]; total: number; limit: number; offset: number }

export interface CareLink {
  clinician_id: string;
  clinician_name: string;
  clinician_email: string;
  patient_id: string;
  patient_name: string;
  patient_email: string;
  created_at: string;
}

export interface Escalation {
  session_id: string;
  patient_id: string;
  patient_name: string;
  patient_email: string;
  triage: 'EMERGENCY' | 'SEE_DOCTOR';
  headline: string | null;
  red_flags: string[];
  created_at: string;
  clinicians: string | null;
  followed_up_at: string | null;
  followed_up_by: string | null;
}

export interface AuditEvent {
  id: number;
  ts: string;
  action: string;
  ip: string | null;
  detail: Record<string, unknown>;
  actor_id: string | null;
  actor_name: string | null;
  actor_email: string | null;
  target_id: string | null;
  target_name: string | null;
  target_email: string | null;
}

export interface Person { id: string; full_name: string; email: string }

export const adminApi = {
  overview: () => api.get<Overview>('/admin/overview').then((r) => r.data),
  system: () => api.get<SystemInfo>('/admin/system').then((r) => r.data),
  users: (params: { search?: string; role?: Role | ''; state?: string; limit: number; offset: number }) =>
    api
      .get<UserPage>('/admin/users', {
        params: { ...params, role: params.role || undefined, state: params.state || undefined, search: params.search || undefined },
      })
      .then((r) => r.data),
  setRole: (id: string, role: Role) => api.put(`/admin/users/${id}/role`, { role }),
  setActive: (id: string, isActive: boolean) => api.put(`/admin/users/${id}/active`, { is_active: isActive }),
  unlock: (id: string) => api.post(`/admin/users/${id}/unlock`),
  signOut: (id: string) => api.post(`/admin/users/${id}/sign-out`),
  sendReset: (id: string) => api.post<{ detail: string }>(`/admin/users/${id}/password-reset`).then((r) => r.data),
  careTeam: () => api.get<CareLink[]>('/admin/care-team').then((r) => r.data),
  assign: (clinicianId: string, patientId: string) => api.post('/admin/care-team', { clinician_id: clinicianId, patient_id: patientId }),
  unassign: (clinicianId: string, patientId: string) =>
    api.delete('/admin/care-team', { params: { clinician_id: clinicianId, patient_id: patientId } }),
  people: (role: Role) => api.get<Person[]>('/admin/people', { params: { role } }).then((r) => r.data),
  escalations: (openOnly: boolean) =>
    api.get<Escalation[]>('/admin/escalations', { params: { open_only: openOnly, limit: 100 } }).then((r) => r.data),
  audit: (params: { category?: string; search?: string; before?: number | null }) =>
    api
      .get<{ items: AuditEvent[]; next_before: number | null }>('/admin/audit', {
        params: { category: params.category || undefined, search: params.search || undefined, before: params.before ?? undefined, limit: 50 },
      })
      .then((r) => r.data),
};
