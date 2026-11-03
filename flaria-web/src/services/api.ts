/**
 * HTTP clients.
 *  - authClient: bare client for /auth endpoints (cookie-bearing, no interceptors).
 *  - api: authenticated client. Attaches the in-memory access token and, on a
 *    401, performs ONE single-flight refresh and replays the request.
 * The auth store registers itself through configureApiAuth(), which avoids a
 * circular import between the store and this module.
 */
import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios';

import { env } from '@/config/env';

const common = {
  baseURL: env.apiBase,
  withCredentials: true,
  timeout: 15_000,
  headers: { 'Content-Type': 'application/json' },
};

export const authClient = axios.create(common);
export const api = axios.create(common);

export interface ApiAuthBridge {
  getAccessToken: () => string | null;
  /** Resolves to a fresh access token, or null if the session is gone. */
  refresh: () => Promise<string | null>;
}

let bridge: ApiAuthBridge | null = null;

export function configureApiAuth(next: ApiAuthBridge): void {
  bridge = next;
}

function requestId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

api.interceptors.request.use((config) => {
  const token = bridge?.getAccessToken();
  if (token) config.headers.set('Authorization', `Bearer ${token}`);
  config.headers.set('X-Request-ID', requestId());
  return config;
});

type RetriableConfig = InternalAxiosRequestConfig & { _flariaRetried?: boolean };

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as RetriableConfig | undefined;
    if (!original || error.response?.status !== 401 || original._flariaRetried || !bridge) {
      return Promise.reject(error);
    }
    original._flariaRetried = true;
    const token = await bridge.refresh();
    if (!token) return Promise.reject(error);
    original.headers.set('Authorization', `Bearer ${token}`);
    return api(original);
  },
);

/** Human-readable message from a FastAPI error response or network failure. */
export function errorMessage(err: unknown, fallback = 'Something went wrong. Try again.'): string {
  if (axios.isAxiosError(err)) {
    if (!err.response) return 'Flaria can’t reach the server. Check your connection and try again.';
    const detail = (err.response.data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { loc?: unknown[]; msg?: string };
      const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1]) : '';
      const msg = (first.msg ?? '').replace(/^Value error, /, '');
      return field && field !== 'body' ? `${field.replace(/_/g, ' ')}: ${msg}` : msg || fallback;
    }
  }
  return fallback;
}
