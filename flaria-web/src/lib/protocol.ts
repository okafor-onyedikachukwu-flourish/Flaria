/**
 * Flaria stream wire protocol v1 (mirrors flaria-core/app/services/protocol.py).
 * 20-byte little-endian header: u8 type, u8 version, u16 flags, u32 seq, f64 t_ms, u16 width, u16 height.
 */
import type { RiskStatus, ServerMessage, TelemetryTick } from '@/types';

export const PROTOCOL_VERSION = 1;
export const HEADER_SIZE = 20;
export const MSG_AUDIO = 0x01;
export const MSG_LANDMARKS = 0x02;
export const MSG_FACE_ABSENT = 0x03;
export const MSG_TICK = 0x10;
export const LANDMARK_COUNT = 468;
const TICK_SIZE = 140;
const STATUS_BY_CODE: RiskStatus[] = ['CALIBRATING', 'STABLE', 'WATCH', 'ELEVATED', 'SIGNAL_LOST'];
const LITTLE_ENDIAN = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

/** Wall-clock epoch milliseconds with sub-millisecond resolution. */
export function epochMs(): number {
  return performance.timeOrigin + performance.now();
}

function writeHeader(view: DataView, type: number, seq: number, tMs: number, width = 0, height = 0): void {
  view.setUint8(0, type);
  view.setUint8(1, PROTOCOL_VERSION);
  view.setUint16(2, 0, true);
  view.setUint32(4, seq >>> 0, true);
  view.setFloat64(8, tMs, true);
  view.setUint16(16, width, true);
  view.setUint16(18, height, true);
}

function writeFloats(buffer: ArrayBuffer, offset: number, values: ArrayLike<number>): void {
  if (LITTLE_ENDIAN) {
    new Float32Array(buffer, offset, values.length).set(values);
    return;
  }
  const view = new DataView(buffer);
  for (let i = 0; i < values.length; i += 1) view.setFloat32(offset + i * 4, values[i], true);
}

export function encodeAudio(pcm: Float32Array, seq: number, tMs: number): ArrayBuffer {
  const buffer = new ArrayBuffer(HEADER_SIZE + pcm.length * 4);
  writeHeader(new DataView(buffer), MSG_AUDIO, seq, tMs);
  writeFloats(buffer, HEADER_SIZE, pcm);
  return buffer;
}

export interface LandmarkPoint { x: number; y: number; z: number }

const landmarkScratch = new Float32Array(LANDMARK_COUNT * 3);

export function encodeLandmarks(points: ArrayLike<LandmarkPoint>, seq: number, tMs: number, width: number, height: number): ArrayBuffer {
  if (points.length < LANDMARK_COUNT) throw new Error(`expected ${LANDMARK_COUNT} landmarks, got ${points.length}`);
  for (let i = 0; i < LANDMARK_COUNT; i += 1) {
    const p = points[i];
    landmarkScratch[i * 3] = p.x;
    landmarkScratch[i * 3 + 1] = p.y;
    landmarkScratch[i * 3 + 2] = p.z;
  }
  const buffer = new ArrayBuffer(HEADER_SIZE + landmarkScratch.byteLength);
  writeHeader(new DataView(buffer), MSG_LANDMARKS, seq, tMs, Math.min(width, 8192), Math.min(height, 8192));
  writeFloats(buffer, HEADER_SIZE, landmarkScratch);
  landmarkScratch.fill(0);
  return buffer;
}

export function encodeFaceAbsent(seq: number, tMs: number): ArrayBuffer {
  const buffer = new ArrayBuffer(HEADER_SIZE);
  writeHeader(new DataView(buffer), MSG_FACE_ABSENT, seq, tMs);
  return buffer;
}

const nullable = (v: number): number | null => (Number.isFinite(v) ? v : null);

/** Decode the 140-byte binary tick (server tick_encoding=binary). */
export function decodeBinaryTick(buffer: ArrayBuffer): TelemetryTick | null {
  if (buffer.byteLength < TICK_SIZE) return null;
  const v = new DataView(buffer);
  if (v.getUint8(0) !== MSG_TICK || v.getUint8(1) !== PROTOCOL_VERSION) return null;
  const flags = v.getUint16(2, true);
  const status = STATUS_BY_CODE[v.getUint8(36)];
  if (!status) return null;
  const audio = Array.from({ length: 15 }, (_, i) => v.getFloat32(40 + i * 4, true));
  const vision = Array.from({ length: 5 }, (_, i) => v.getFloat32(100 + i * 4, true));
  return {
    type: 'tick',
    seq: v.getUint32(4, true),
    ts: v.getFloat64(8, true),
    src_ts: v.getFloat64(16, true),
    proc_ms: v.getFloat32(24, true),
    anomaly: nullable(v.getFloat32(28, true)),
    raw: nullable(v.getFloat32(32, true)),
    status,
    audio,
    vision,
    vq: {
      f0: nullable(v.getFloat32(120, true)),
      jitter: nullable(v.getFloat32(124, true)),
      shimmer: nullable(v.getFloat32(128, true)),
      hnr: nullable(v.getFloat32(132, true)),
      voiced: null,
    },
    q: {
      face: (flags & 1) !== 0,
      voiced: (flags & 2) !== 0,
      audio_dropout: (flags & 4) !== 0,
      pose_ok: (flags & 8) !== 0,
      calibrated: (flags & 16) !== 0,
      calibration: v.getFloat32(136, true),
    },
  };
}

function isTick(m: Record<string, unknown>): m is TelemetryTick & Record<string, unknown> {
  return (
    m.type === 'tick' && typeof m.seq === 'number' && Array.isArray(m.audio) && m.audio.length === 15 &&
    Array.isArray(m.vision) && m.vision.length === 5 && typeof m.status === 'string' &&
    STATUS_BY_CODE.includes(m.status as RiskStatus) && typeof m.q === 'object' && m.q !== null &&
    typeof m.vq === 'object' && m.vq !== null
  );
}

/** Parse any server frame; returns null for malformed or unknown messages instead of throwing. */
export function parseServerMessage(data: unknown): ServerMessage | null {
  if (data instanceof ArrayBuffer) return decodeBinaryTick(data);
  if (typeof data !== 'string' || data.length > 65_536) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null) return null;
  const m = parsed as Record<string, unknown>;
  switch (m.type) {
    case 'tick':
      return isTick(m) ? (m as unknown as TelemetryTick) : null;
    case 'hello':
      return typeof m.session_id === 'string' && typeof m.model === 'object' ? (m as unknown as ServerMessage) : null;
    case 'pong':
      return typeof m.t === 'number' ? (m as unknown as ServerMessage) : null;
    case 'reauth_ok':
      return typeof m.auth_exp === 'number' ? (m as unknown as ServerMessage) : null;
    default:
      return null;
  }
}
