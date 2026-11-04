const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
const dayFmt = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });

export function relative(iso: string | null | undefined): string {
  if (!iso) return 'Never';
  const seconds = (Date.parse(iso) - Date.now()) / 1000;
  const abs = Math.abs(seconds);
  if (abs < 45) return 'Just now';
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), 'minute');
  if (abs < 86_400) return rtf.format(Math.round(seconds / 3600), 'hour');
  if (abs < 86_400 * 30) return rtf.format(Math.round(seconds / 86_400), 'day');
  return dateTime.format(new Date(iso));
}

export const absolute = (iso: string | null | undefined) => (iso ? dateTime.format(new Date(iso)) : '–');
export const shortDay = (isoDay: string) => dayFmt.format(new Date(`${isoDay}T12:00:00Z`));

export function duration(seconds: number): string {
  const d = Math.floor(seconds / 86_400);
  const h = Math.floor((seconds % 86_400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${m} min`;
  return `${m} min`;
}

export function bytes(n: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v < 10 && i ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

export const roleLabel = (role: string) => role.charAt(0) + role.slice(1).toLowerCase();

export const TRIAGE_LABEL: Record<string, string> = {
  EMERGENCY: 'Emergency',
  SEE_DOCTOR: 'See a doctor',
  DISCUSS: 'Check again',
  NO_CHANGE: 'No change',
  BASELINE: 'Building baseline',
  INSUFFICIENT: 'Not enough signal',
};

export const ACTION_LABEL: Record<string, string> = {
  'auth.login': 'Signed in',
  'auth.login_failed': 'Failed sign-in',
  'auth.register': 'Created an account',
  'auth.logout': 'Signed out',
  'auth.logout_all': 'Signed out of all devices',
  'auth.refresh_reuse': 'Blocked a reused refresh token',
  'auth.password_changed': 'Changed their password',
  'auth.password_change_failed': 'Wrong current password',
  'auth.password_reset_requested': 'Requested a reset link',
  'auth.password_reset_throttled': 'Reset link limit reached',
  'auth.password_reset': 'Reset their password',
  'session.start': 'Started a session',
  'session.end': 'Ended a session',
  'report.red_flags': 'Reported warning symptoms',
  'care_note.create': 'Wrote a care note',
  'admin.set_role': 'Changed a role',
  'admin.disable_user': 'Disabled an account',
  'admin.enable_user': 'Enabled an account',
  'admin.unlock_user': 'Unlocked an account',
  'admin.sign_out_user': 'Signed a user out everywhere',
  'admin.password_reset_sent': 'Sent a reset link',
  'admin.care_team_assign': 'Assigned a clinician',
  'admin.care_team_remove': 'Removed a clinician',
  'research.consent_given': 'Joined the study',
  'research.consent_withdrawn': 'Withdrew from the study',
  'research.data_deleted': 'Deleted their research data',
  'research.label_set': 'Recorded a research label',
  'research.export': 'Downloaded a research export',
  'cli.create_admin': 'Created an admin (command line)',
  'cli.promote_admin': 'Promoted to admin (command line)',
};

export const RED_FLAG_LABEL: Record<string, string> = {
  face_droop: 'Face drooping',
  arm_weakness: 'Limb weakness',
  speech_difficulty: 'Speech difficulty',
  confusion: 'Confusion, headache or vision loss',
  breathing: 'Severe breathlessness',
  chest_pain: 'Chest pain',
};
