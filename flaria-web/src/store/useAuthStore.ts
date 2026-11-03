/**
 * Authentication state.
 *
 * Token handling:
 *  - The access token (15 min) lives only in memory, never in localStorage,
 *    so script injection cannot lift a long-lived credential from storage.
 *  - Session persistence comes from the HttpOnly, Secure, SameSite=Strict
 *    refresh cookie (7 days) which JavaScript cannot read. On load, bootstrap()
 *    exchanges it for a fresh access token.
 *  - Refresh is single-flight inside a tab and serialised across tabs with the
 *    Web Locks API; a 409 (another tab rotated first) is retried once.
 *  - Access tokens are renewed proactively 60 s before expiry.
 *  - Logout is broadcast to other tabs.
 */
import axios from 'axios';
import { create } from 'zustand';

import { api, authClient, configureApiAuth, errorMessage } from '@/services/api';
import type { TokenResponse, User } from '@/types';

type AuthStatus = 'unknown' | 'authenticated' | 'anonymous';

interface AuthState {
  user: User | null;
  accessToken: string | null;
  expiresAt: number | null;
  status: AuthStatus;
  error: string | null;
  bootstrap: () => Promise<void>;
  login: (email: string, password: string) => Promise<boolean>;
  register: (fullName: string, email: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  refresh: () => Promise<string | null>;
  ensureFreshToken: (minValidityMs?: number) => Promise<string | null>;
  clearError: () => void;
  /** Resolves to null on success, or a message explaining what to fix. */
  changePassword: (current: string, next: string) => Promise<string | null>;
}

const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('flaria-auth') : null;
let refreshInFlight: Promise<string | null> | null = null;
let renewTimer: ReturnType<typeof setTimeout> | null = null;
let bootstrapped: Promise<void> | null = null;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const SESSION_HINT = 'flaria_has_session';
const hasSessionHint = () => typeof document !== 'undefined' && document.cookie.split('; ').some((c) => c.startsWith(`${SESSION_HINT}=`));
const dropSessionHint = () => {
  if (typeof document !== 'undefined') document.cookie = `${SESSION_HINT}=; Max-Age=0; Path=/; SameSite=Strict`;
};

async function withCrossTabLock<T>(fn: () => Promise<T>): Promise<T> {
  if (typeof navigator !== 'undefined' && navigator.locks?.request) {
    return navigator.locks.request('flaria-auth-refresh', fn) as Promise<T>;
  }
  return fn();
}

export const useAuthStore = create<AuthState>()((set, get) => {
  const applySession = (data: TokenResponse) => {
    const expiresAt = Date.parse(data.expires_at) || Date.now() + data.expires_in * 1000;
    set({ user: data.user, accessToken: data.access_token, expiresAt, status: 'authenticated', error: null });
    if (renewTimer) clearTimeout(renewTimer);
    renewTimer = setTimeout(() => void get().refresh(), Math.max(5_000, expiresAt - Date.now() - 60_000));
  };

  const clearSession = (error: string | null = null) => {
    if (renewTimer) clearTimeout(renewTimer);
    renewTimer = null;
    set({ user: null, accessToken: null, expiresAt: null, status: 'anonymous', error });
  };

  const performRefresh = async (): Promise<string | null> => {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const { data } = await authClient.post<TokenResponse>('/auth/refresh');
        applySession(data);
        return data.access_token;
      } catch (err) {
        if (axios.isAxiosError(err)) {
          const status = err.response?.status;
          if (status === 409 && attempt === 0) {
            await sleep(300); // another tab rotated the cookie a moment ago
            continue;
          }
          if (status === 401 || status === 403) {
            dropSessionHint();
            clearSession(get().status === 'authenticated' ? 'Your session ended. Sign in again.' : null);
            return null;
          }
        }
        // Network or server failure: keep the current session state and let callers retry.
        if (get().status === 'unknown') clearSession(errorMessage(err));
        return null;
      }
    }
    return null;
  };

  return {
    user: null,
    accessToken: null,
    expiresAt: null,
    status: 'unknown',
    error: null,

    bootstrap: () => {
      bootstrapped ??= (async () => {
        // No hint cookie means this browser has never signed in: skip the refresh round trip.
        if (hasSessionHint()) await get().refresh();
        if (get().status === 'unknown') clearSession();
      })();
      return bootstrapped;
    },

    refresh: () => {
      refreshInFlight ??= withCrossTabLock(performRefresh).finally(() => {
        refreshInFlight = null;
      });
      return refreshInFlight;
    },

    ensureFreshToken: async (minValidityMs = 30_000) => {
      const { accessToken, expiresAt } = get();
      if (accessToken && expiresAt && expiresAt - Date.now() > minValidityMs) return accessToken;
      return get().refresh();
    },

    login: async (email, password) => {
      try {
        const { data } = await authClient.post<TokenResponse>('/auth/login', { email, password });
        applySession(data);
        return true;
      } catch (err) {
        set({ error: errorMessage(err, 'Sign-in failed. Try again.') });
        return false;
      }
    },

    register: async (fullName, email, password) => {
      try {
        const { data } = await authClient.post<TokenResponse>('/auth/register', {
          full_name: fullName,
          email,
          password,
        });
        applySession(data);
        return true;
      } catch (err) {
        set({ error: errorMessage(err, 'Account creation failed. Try again.') });
        return false;
      }
    },

    logout: async () => {
      try {
        await authClient.post('/auth/logout');
      } catch {
        // The local session is cleared regardless; the cookie expires server-side.
      }
      dropSessionHint();
      clearSession();
      channel?.postMessage({ type: 'logout' });
    },

    clearError: () => set({ error: null }),

    changePassword: async (current, next) => {
      try {
        // The server signs out every other device and returns a fresh session for this one.
        const { data } = await api.post<TokenResponse>('/auth/change-password', { current_password: current, new_password: next });
        applySession(data);
        return null;
      } catch (err) {
        return errorMessage(err, 'Your password couldn’t be changed. Try again.');
      }
    },
  };
});

channel?.addEventListener('message', (event: MessageEvent<{ type?: string }>) => {
  if (event.data?.type === 'logout' && useAuthStore.getState().status === 'authenticated') {
    if (renewTimer) clearTimeout(renewTimer);
    useAuthStore.setState({ user: null, accessToken: null, expiresAt: null, status: 'anonymous', error: null });
  }
});

configureApiAuth({
  getAccessToken: () => useAuthStore.getState().accessToken,
  refresh: () => useAuthStore.getState().refresh(),
});
