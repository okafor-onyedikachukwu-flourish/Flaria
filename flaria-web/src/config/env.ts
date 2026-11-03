export const env = {
  apiBase: import.meta.env.VITE_API_BASE ?? '/api/v1',
  wsUrl: import.meta.env.VITE_WS_URL ?? '',
  mediapipeWasmBase: import.meta.env.VITE_MEDIAPIPE_WASM_BASE ?? '/mediapipe/wasm',
  faceModelUrl: import.meta.env.VITE_FACE_MODEL_URL ?? '/mediapipe/face_landmarker.task',
} as const;

/** Same-origin WebSocket URL unless VITE_WS_URL overrides it. */
export function resolveStreamUrl(patientId?: string | null): string {
  const base = env.wsUrl || `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/stream`;
  if (!patientId) return base;
  const url = new URL(base);
  url.searchParams.set('patient_id', patientId);
  return url.toString();
}
