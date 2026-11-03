export type Role = 'PATIENT' | 'CLINICIAN' | 'ADMIN';

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
}

export interface TokenResponse {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
  expires_at: string;
  user: User;
}

export type RiskStatus = 'CALIBRATING' | 'STABLE' | 'WATCH' | 'ELEVATED' | 'SIGNAL_LOST';

export interface VoiceQuality {
  f0: number | null;
  jitter: number | null;
  shimmer: number | null;
  hnr: number | null;
  voiced: number | null;
}

export interface TickQuality {
  face: boolean;
  voiced: boolean;
  audio_dropout: boolean;
  pose_ok: boolean;
  calibration: number;
  calibrated: boolean;
}

export interface TelemetryTick {
  type: 'tick';
  seq: number;
  ts: number;
  src_ts: number;
  proc_ms: number;
  anomaly: number | null;
  raw: number | null;
  status: RiskStatus;
  audio: number[];
  vision: number[];
  vq: VoiceQuality;
  q: TickQuality;
}

export interface PrivacyAttestation {
  raw_media_persisted: boolean;
  video_frames_received: boolean;
  landmarks_persisted: boolean;
  audio_ring_seconds: number;
  audio_ring_bytes: number;
  persist_hz: number;
  persisted_fields: string[];
}

export interface HelloMessage {
  type: 'hello';
  session_id: string;
  patient_id: string;
  model: { version: string; provider: string; seq_len: number; threshold_watch: number; threshold_elevated: number };
  tick_hz: number;
  tick_encoding: 'json' | 'binary';
  audio: { sample_rate: number; max_chunk_samples: number };
  auth_exp: number;
  privacy: PrivacyAttestation;
  calibration: { audio_baseline_samples: number; vision_baseline_samples: number; using_persisted_baseline: boolean };
}

export interface PongMessage { type: 'pong'; t: number; ts: number }
export interface ReauthOk { type: 'reauth_ok'; auth_exp: number }
export type ServerMessage = TelemetryTick | HelloMessage | PongMessage | ReauthOk;

export type ConnectionState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed' | 'failed';

export interface SessionSummary {
  id: string;
  patient_id: string;
  started_by: string;
  status: 'ACTIVE' | 'COMPLETED' | 'ABORTED';
  started_at: string;
  ended_at: string | null;
  end_reason: string | null;
  model_version: string;
  execution_provider: string;
  tick_count: number;
  peak_anomaly: number | null;
  mean_anomaly: number | null;
}

export interface PatientSummary { id: string; email: string; full_name: string }

export interface BaselineSummary {
  patient_id: string;
  audio_samples: number;
  vision_samples: number;
  days_covered: number;
  ready: boolean;
}

export type Triage = 'EMERGENCY' | 'SEE_DOCTOR' | 'DISCUSS' | 'NO_CHANGE' | 'BASELINE' | 'INSUFFICIENT';

export interface FindingMeasure { name: string; z: number; usual: string }

export interface Finding {
  id: string;
  title: string;
  severity: 'moderate' | 'strong';
  summary: string;
  measures: FindingMeasure[];
  possible_causes: string[];
}

export interface SessionReport {
  session_id: string;
  patient_id: string;
  created_at: string;
  updated_at: string;
  triage: Triage;
  headline: string;
  advice: string[];
  findings: Finding[];
  red_flags: string[];
  red_flag_options: Record<string, string>;
  /** Demo deployments label measured results as demonstrations. */
  demo?: boolean;
  summary: {
    persisted_baseline: boolean;
    seconds: number;
    valid_seconds: { face: number; voice: number };
    anomaly_mean: number | null;
    anomaly_peak: number | null;
    model_version: string;
  };
  rules_version: string;
}

export interface CareNote {
  id: number;
  patient_id: string;
  body: string;
  created_at: string;
  author_name: string | null;
  author_role: Role | null;
}
