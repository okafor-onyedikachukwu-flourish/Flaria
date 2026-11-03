/**
 * Authenticated, self-healing telemetry WebSocket.
 *
 *  - Auth: the access token travels in the `bearer.<jwt>` subprotocol (never in
 *    the URL, so it stays out of proxy logs); `flaria.v1` is the negotiated protocol.
 *  - Token renewal mid-stream: whenever the auth store rotates the access token,
 *    a `reauth` control message is sent; the server closes with 4401 if it lapses.
 *  - Reconnect: exponential backoff with full jitter (0.5 s -> 15 s, 12 tries),
 *    immediate retry when the browser comes back online, token refresh on 4401.
 *  - Liveness: ping every 2 s (also measures RTT); silence for 6 s forces a reconnect.
 *  - Back-pressure: frames are dropped (and counted) when the socket buffer is
 *    congested, video before audio, so the stream never builds latency.
 */
import { useCallback, useEffect, useRef } from 'react';

import { resolveStreamUrl } from '@/config/env';
import { epochMs, parseServerMessage } from '@/lib/protocol';
import { useAuthStore } from '@/store/useAuthStore';
import { useTelemetryStore } from '@/store/useTelemetryStore';

const SUBPROTOCOL = 'flaria.v1';
const PING_INTERVAL_MS = 2_000;
const SILENCE_TIMEOUT_MS = 6_000;
const BACKOFF_BASE_MS = 500;
const BACKOFF_MAX_MS = 15_000;
const MAX_ATTEMPTS = 12;
const BUFFER_LIMIT = { audio: 512 * 1024, video: 128 * 1024 } as const;

export type FramePriority = keyof typeof BUFFER_LIMIT;

const TERMINAL: Record<number, string> = {
  1008: 'The server refused the stream (policy violation). Reload the page and try again.',
  1009: 'A frame exceeded the server size limit. Reload the page and try again.',
  4403: 'You don’t have access to stream for this patient.',
  4409: 'A session is already running for this patient in another tab or device. Stop it there, then start again.',
};

interface Options {
  enabled: boolean;
  patientId?: string | null;
}

export function useFlariaWebSocket({ enabled, patientId }: Options) {
  const wsRef = useRef<WebSocket | null>(null);
  const attemptsRef = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastMessageAt = useRef(0);
  const wantOpen = useRef(false);
  const stoppingRef = useRef(false);

  const store = useTelemetryStore.getState;

  const clearTimers = () => {
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    if (pingTimer.current) clearInterval(pingTimer.current);
    reconnectTimer.current = null;
    pingTimer.current = null;
  };

  const connect = useCallback(async () => {
    if (!wantOpen.current) return;
    const { setConnection } = store();
    setConnection(attemptsRef.current === 0 ? 'connecting' : 'reconnecting');

    const token = await useAuthStore.getState().ensureFreshToken(60_000);
    if (!wantOpen.current) return;
    if (!token) {
      setConnection('failed', 'Your session ended. Sign in again to continue streaming.');
      wantOpen.current = false;
      return;
    }

    let ws: WebSocket;
    try {
      ws = new WebSocket(resolveStreamUrl(patientId), [SUBPROTOCOL, `bearer.${token}`]);
    } catch (err) {
      setConnection('failed', `Couldn’t open the stream: ${(err as Error).message}`);
      return;
    }
    ws.binaryType = 'arraybuffer';
    wsRef.current = ws;

    ws.onopen = () => {
      lastMessageAt.current = Date.now();
      pingTimer.current = setInterval(() => {
        if (ws.readyState !== WebSocket.OPEN) return;
        if (Date.now() - lastMessageAt.current > SILENCE_TIMEOUT_MS) {
          ws.close(4000, 'heartbeat timeout');
          return;
        }
        ws.send(JSON.stringify({ type: 'ping', t: performance.now() }));
      }, PING_INTERVAL_MS);
    };

    ws.onmessage = (event: MessageEvent<string | ArrayBuffer>) => {
      lastMessageAt.current = Date.now();
      const msg = parseServerMessage(event.data);
      if (!msg) return;
      const s = store();
      switch (msg.type) {
        case 'tick':
          s.ingestTick(msg);
          break;
        case 'hello':
          attemptsRef.current = 0;
          s.setHello(msg);
          s.setConnection('open');
          break;
        case 'pong':
          s.recordRtt(performance.now() - msg.t);
          break;
        case 'reauth_ok':
          break;
      }
    };

    ws.onclose = (event) => {
      if (wsRef.current === ws) wsRef.current = null;
      clearTimers();
      const s = store();
      if (!wantOpen.current || stoppingRef.current) {
        if (s.connection !== 'failed') s.setConnection('closed');
        return;
      }
      if (event.code === 1000) {
        s.setConnection('closed', event.reason === 'token_expired' ? 'Session expired. Sign in again.' : null);
        wantOpen.current = false;
        return;
      }
      const terminal = TERMINAL[event.code];
      if (terminal) {
        s.setConnection('failed', terminal);
        wantOpen.current = false;
        return;
      }
      if (attemptsRef.current >= MAX_ATTEMPTS) {
        s.setConnection('failed', 'Lost connection to the server. Check your network, then start the session again.');
        wantOpen.current = false;
        return;
      }
      const run = async () => {
        if (event.code === 4401) {
          const fresh = await useAuthStore.getState().refresh();
          if (!fresh) {
            store().setConnection('failed', 'Your session ended. Sign in again to continue streaming.');
            wantOpen.current = false;
            return;
          }
        }
        void connect();
      };
      const delay =
        event.code === 4401 ? 0 : Math.random() * Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** attemptsRef.current);
      attemptsRef.current += 1;
      s.setConnection(
        'reconnecting',
        event.code === 1013 ? 'The analysis model is unavailable on the server. Retrying.' : 'Connection interrupted. Reconnecting.',
      );
      reconnectTimer.current = setTimeout(() => void run(), delay);
    };
  }, [patientId, store]);

  const disconnect = useCallback((graceful = true) => {
    wantOpen.current = false;
    clearTimers();
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      stoppingRef.current = true;
      if (graceful) ws.send(JSON.stringify({ type: 'stop' }));
      setTimeout(() => ws.close(1000, 'client stop'), graceful ? 250 : 0);
    } else if (ws && ws.readyState === WebSocket.CONNECTING) {
      ws.close(1000, 'client stop');
    }
    wsRef.current = null;
    if (store().connection !== 'failed') store().setConnection('closed'); // keep a failure explanation visible
  }, [store]);

  /** Send a binary frame; returns false when dropped for back-pressure or a closed socket. */
  const send = useCallback((frame: ArrayBuffer, priority: FramePriority): boolean => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount > BUFFER_LIMIT[priority]) {
      if (ws?.readyState === WebSocket.OPEN) store().recordDroppedFrame();
      return false;
    }
    ws.send(frame);
    return true;
  }, [store]);

  // Lifecycle: open while enabled.
  useEffect(() => {
    if (!enabled) return undefined;
    wantOpen.current = true;
    stoppingRef.current = false;
    attemptsRef.current = 0;
    void connect();
    return () => disconnect(true);
  }, [enabled, connect, disconnect]);

  // Forward rotated access tokens to the live stream.
  useEffect(
    () =>
      useAuthStore.subscribe((state, prev) => {
        const ws = wsRef.current;
        if (state.accessToken && state.accessToken !== prev.accessToken && ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'reauth', token: state.accessToken }));
        }
      }),
    [],
  );

  // Reconnect immediately when the network returns.
  useEffect(() => {
    const onOnline = () => {
      if (wantOpen.current && !wsRef.current) {
        if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
        attemptsRef.current = 0;
        void connect();
      }
    };
    window.addEventListener('online', onOnline);
    return () => window.removeEventListener('online', onOnline);
  }, [connect]);

  return { send, disconnect, now: epochMs };
}
