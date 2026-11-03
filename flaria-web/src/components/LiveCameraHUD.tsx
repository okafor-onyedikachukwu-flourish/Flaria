/**
 * Live camera HUD.
 *
 * Privacy: the face mesh runs in this browser (MediaPipe WASM). Video frames
 * never leave the device; only the 468 landmark coordinates are sent, and the
 * server reduces them to five scalars on arrival. The only pixels read back
 * are a 32x24 luminance thumbnail once per second for the low-light hint,
 * computed in memory and immediately overwritten.
 *
 * Rendering: the overlay is drawn imperatively on a canvas in the video frame
 * callback; numeric readouts are written straight to DOM nodes. React only
 * re-renders for phase changes and hints, never per frame.
 */
import { FaceLandmarker, FilesetResolver, type NormalizedLandmark } from '@mediapipe/tasks-vision';
import { Camera, CameraOff, ScanFace } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { env } from '@/config/env';
import { LIP_CORNER_L, LIP_CORNER_R, NASAL_BRIDGE, SUBNASALE } from '@/lib/faceMesh';
import { encodeFaceAbsent, encodeLandmarks, epochMs } from '@/lib/protocol';
import { STATUS_HUD_COLOR, STATUS_LABEL } from '@/lib/theme';
import { useTelemetryStore } from '@/store/useTelemetryStore';

type Phase = 'idle' | 'starting' | 'running' | 'error';
type FrameVideo = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: (now: number) => void) => number;
  cancelVideoFrameCallback?: (id: number) => void;
};

const MAX_FPS = 30;
/** Keep face detection under ~40% of main-thread time so audio, the socket and the UI stay responsive on slow devices. */
const DETECT_BUDGET = 0.4;
let landmarkerPromise: Promise<FaceLandmarker> | null = null;

/**
 * True when WebGL runs on a real GPU. Software rasterisers (SwiftShader, llvmpipe), used by VMs,
 * remote desktops and machines whose GPU is blocklisted, make MediaPipe's GPU delegate many times
 * slower than its CPU (XNNPACK) path, so they are treated as "no GPU".
 */
function hasHardwareWebGL(): boolean {
  try {
    const gl = document.createElement('canvas').getContext('webgl2');
    if (!gl) return false;
    const info = gl.getExtension('WEBGL_debug_renderer_info');
    const renderer = String(info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return !/swiftshader|llvmpipe|softpipe|software|basic render/i.test(renderer);
  } catch {
    return false;
  }
}

/** Loaded once per page and shared across mounts (React StrictMode mounts twice in development). */
function getFaceLandmarker(): Promise<FaceLandmarker> {
  landmarkerPromise ??= (async () => {
    const fileset = await FilesetResolver.forVisionTasks(env.mediapipeWasmBase);
    const common = {
      runningMode: 'VIDEO' as const,
      numFaces: 1,
      minFaceDetectionConfidence: 0.5,
      minFacePresenceConfidence: 0.5,
      minTrackingConfidence: 0.5,
      outputFaceBlendshapes: false,
      outputFacialTransformationMatrixes: false,
    };
    const delegates: ('GPU' | 'CPU')[] = hasHardwareWebGL() ? ['GPU', 'CPU'] : ['CPU'];
    let lastError: unknown;
    for (const delegate of delegates) {
      try {
        return await FaceLandmarker.createFromOptions(fileset, {
          ...common,
          baseOptions: { modelAssetPath: env.faceModelUrl, delegate },
        });
      } catch (err) {
        lastError = err;
      }
    }
    throw lastError;
  })().catch((err: unknown) => {
    landmarkerPromise = null;
    throw err;
  });
  return landmarkerPromise;
}

function describeCameraError(err: unknown): string {
  const name = (err as { name?: string })?.name;
  if (name === 'NotAllowedError') return 'Camera access is blocked. Allow it in your browser’s site settings, then start the session again.';
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'No usable camera was found. Connect one, then start the session again.';
  if (name === 'NotReadableError') return 'The camera is in use by another application. Close it, then start the session again.';
  const msg = (err as Error)?.message ?? String(err);
  if (/face_landmarker|fetch|Failed to load|404/i.test(msg)) {
    return `The face model couldn’t load from ${env.faceModelUrl}. Run “npm run assets” when building the web app.`;
  }
  return `The camera couldn’t start: ${msg}`;
}

function strokeConnections(
  ctx: CanvasRenderingContext2D,
  pts: NormalizedLandmark[],
  connections: { start: number; end: number }[],
  w: number,
  h: number,
): void {
  for (const c of connections) {
    const a = pts[c.start];
    const b = pts[c.end];
    if (!a || !b) continue;
    ctx.moveTo(a.x * w, a.y * h);
    ctx.lineTo(b.x * w, b.y * h);
  }
}

interface Props {
  active: boolean;
  onFrame: (frame: ArrayBuffer) => void;
}

export function LiveCameraHUD({ active, onFrame }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sectionRef = useRef<HTMLElement>(null);
  const trackingRef = useRef<HTMLSpanElement>(null);
  const readouts = useRef<Record<'asym' | 'lips' | 'ear' | 'pose', HTMLSpanElement | null>>({ asym: null, lips: null, ear: null, pose: null });
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [faceError, setFaceError] = useState<string | null>(null);
  const status = useTelemetryStore((s) => s.ui.status);
  const anomaly = useTelemetryStore((s) => s.ui.anomaly);
  const calibration = useTelemetryStore((s) => s.ui.quality.calibration);

  const onFrameRef = useRef(onFrame);
  onFrameRef.current = onFrame;
  const statusRef = useRef(status);
  statusRef.current = status;

  useEffect(() => {
    if (!active) {
      setPhase('idle');
      setHint(null);
      return undefined;
    }
    let cancelled = false;
    let stream: MediaStream | null = null;
    let frameHandle: number | null = null;
    let usingRvfc = false;
    let seq = 0;
    let lastTs = 0;
    let lastProcessed = 0;
    let lastFaceAt = performance.now();
    let lastLightCheck = 0;
    let lowLight = false;
    let currentHint: string | null = null;
    let readoutCounter = 0;
    let detectMs = 0; // EMA of detection time; sets the adaptive frame interval
    let framesThisSecond = 0;
    let rateWindowStart = performance.now();
    let trackingFps = 0;
    let slowSeconds = 0;
    const thumb = document.createElement('canvas');
    thumb.width = 32;
    thumb.height = 24;
    const thumbCtx = thumb.getContext('2d', { willReadFrequently: true });
    const video = videoRef.current as FrameVideo | null;
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d') ?? null;
    const buffers = useTelemetryStore.getState().buffers;

    const updateHint = (next: string | null) => {
      if (next !== currentHint) {
        currentHint = next;
        setHint(next);
      }
    };

    const writeReadouts = () => {
      const r = readouts.current;
      const v = buffers.latestVision;
      const has = buffers.faceVisible && buffers.version > 0;
      if (r.asym) r.asym.textContent = has ? v[0].toFixed(3) : '–';
      if (r.lips) r.lips.textContent = has ? v[1].toFixed(3) : '–';
      if (r.ear) r.ear.textContent = has ? v[2].toFixed(2) : '–';
      if (r.pose) r.pose.textContent = has ? `${v[3] >= 0 ? '+' : ''}${v[3].toFixed(0)}° / ${v[4] >= 0 ? '+' : ''}${v[4].toFixed(0)}°` : '–';
    };

    const draw = (pts: NormalizedLandmark[] | null) => {
      if (!ctx || !canvas || !video) return;
      if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
      }
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);
      if (!pts) return;
      const accent = STATUS_HUD_COLOR[statusRef.current];

      ctx.fillStyle = 'rgba(25, 167, 181, 0.55)';
      for (let i = 0; i < 468; i += 1) ctx.fillRect(pts[i].x * w - 0.8, pts[i].y * h - 0.8, 1.6, 1.6);

      ctx.lineWidth = Math.max(1, w / 480);
      ctx.strokeStyle = 'rgba(215, 228, 232, 0.8)';
      ctx.beginPath();
      strokeConnections(ctx, pts, FaceLandmarker.FACE_LANDMARKS_LIPS, w, h);
      strokeConnections(ctx, pts, FaceLandmarker.FACE_LANDMARKS_LEFT_EYE, w, h);
      strokeConnections(ctx, pts, FaceLandmarker.FACE_LANDMARKS_RIGHT_EYE, w, h);
      ctx.stroke();

      // Facial midline from the nasal bridge through the subnasale, extended to the chin.
      const m0 = { x: pts[NASAL_BRIDGE].x * w, y: pts[NASAL_BRIDGE].y * h };
      const m1 = { x: pts[SUBNASALE].x * w, y: pts[SUBNASALE].y * h };
      const ux = m1.x - m0.x;
      const uy = m1.y - m0.y;
      const len = Math.hypot(ux, uy) || 1;
      ctx.setLineDash([5, 5]);
      ctx.strokeStyle = 'rgba(215, 228, 232, 0.6)';
      ctx.beginPath();
      ctx.moveTo(m0.x, m0.y);
      ctx.lineTo(m0.x + ux * 2.4, m0.y + uy * 2.4);
      ctx.stroke();
      ctx.setLineDash([]);

      // Reflect the right lip corner across the midline: the gap to the left corner is the asymmetry.
      const r = { x: pts[LIP_CORNER_R].x * w - m0.x, y: pts[LIP_CORNER_R].y * h - m0.y };
      const l = { x: pts[LIP_CORNER_L].x * w, y: pts[LIP_CORNER_L].y * h };
      const u = { x: ux / len, y: uy / len };
      const n = { x: -u.y, y: u.x };
      const along = r.x * u.x + r.y * u.y;
      const across = r.x * n.x + r.y * n.y;
      const reflected = { x: m0.x + along * u.x - across * n.x, y: m0.y + along * u.y - across * n.y };

      ctx.strokeStyle = accent;
      ctx.lineWidth = Math.max(1.5, w / 320);
      ctx.beginPath();
      ctx.moveTo(pts[LIP_CORNER_R].x * w, pts[LIP_CORNER_R].y * h);
      ctx.lineTo(l.x, l.y);
      ctx.stroke();
      ctx.fillStyle = accent;
      for (const p of [pts[LIP_CORNER_R], pts[LIP_CORNER_L]]) {
        ctx.beginPath();
        ctx.arc(p.x * w, p.y * h, Math.max(3, w / 180), 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.beginPath();
      ctx.arc(reflected.x, reflected.y, Math.max(3, w / 180), 0, Math.PI * 2);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(reflected.x, reflected.y);
      ctx.lineTo(l.x, l.y);
      ctx.stroke();
    };

    const schedule = (loop: (now: number) => void) => {
      if (!video) return;
      if (typeof video.requestVideoFrameCallback === 'function') {
        usingRvfc = true;
        frameHandle = video.requestVideoFrameCallback(loop);
      } else {
        frameHandle = requestAnimationFrame(loop);
      }
    };

    (async () => {
      setPhase('starting');
      setError(null);
      setFaceError(null);
      try {
        const [landmarkerResult, media] = await Promise.all([
          getFaceLandmarker().then(
            (lm) => ({ lm, err: null as unknown }),
            (err: unknown) => ({ lm: null, err }),
          ),
          navigator.mediaDevices.getUserMedia({
            video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 30, max: 30 }, facingMode: 'user' },
            audio: false,
          }),
        ]);
        const landmarker = landmarkerResult.lm;
        if (!landmarker) {
          // Degrade instead of failing: keep the preview, explain, let voice measures continue.
          console.error('[hud] face model unavailable', landmarkerResult.err);
          setFaceError(describeCameraError(landmarkerResult.err));
        }
        stream = media;
        if (cancelled || !video) return;
        video.srcObject = media;
        video.muted = true;
        video.playsInline = true;
        await video.play();
        media.getVideoTracks()[0]?.addEventListener('ended', () => {
          setError('The camera was disconnected. Reconnect it, then start the session again.');
          setPhase('error');
        });
        setPhase('running');

        const loop = (now: number) => {
          if (cancelled) return;
          schedule(loop);
          const minInterval = Math.max(1000 / MAX_FPS, detectMs / DETECT_BUDGET);
          if (now - lastProcessed < minInterval - 2 || video.readyState < 2 || video.videoWidth === 0) return;
          lastProcessed = now;
          const ts = Math.max(now, lastTs + 1); // detectForVideo requires strictly increasing timestamps
          lastTs = ts;
          if (!landmarker) return;
          let points: NormalizedLandmark[] | null = null;
          const detectStart = performance.now();
          try {
            const result = landmarker.detectForVideo(video, ts);
            const face = result.faceLandmarks[0];
            if (face && face.length >= 468) points = face;
          } catch (err) {
            console.warn('[hud] landmark detection failed for a frame', err);
          }
          const took = performance.now() - detectStart;
          framesThisSecond += 1;
          if (now - rateWindowStart >= 1000) {
            trackingFps = Math.round((framesThisSecond * 1000) / (now - rateWindowStart));
            framesThisSecond = 0;
            rateWindowStart = now;
            slowSeconds = trackingFps < 10 ? slowSeconds + 1 : 0;
            if (trackingRef.current) trackingRef.current.textContent = `Face tracking ${trackingFps}/s`;
            sectionRef.current?.setAttribute('data-tracking-fps', String(trackingFps));
            sectionRef.current?.setAttribute('data-detect-ms', detectMs.toFixed(1));
          }
          detectMs = detectMs === 0 ? took : detectMs + 0.1 * (took - detectMs);
          const tMs = epochMs();
          if (points) {
            lastFaceAt = now;
            onFrameRef.current(encodeLandmarks(points, seq++, tMs, video.videoWidth, video.videoHeight));
          } else {
            onFrameRef.current(encodeFaceAbsent(seq++, tMs));
          }
          draw(points);

          if (thumbCtx && now - lastLightCheck > 1000) {
            lastLightCheck = now;
            thumbCtx.drawImage(video, 0, 0, 32, 24);
            const px = thumbCtx.getImageData(0, 0, 32, 24).data;
            let sum = 0;
            for (let i = 0; i < px.length; i += 4) sum += 0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2];
            lowLight = sum / (32 * 24) < 45;
            thumbCtx.clearRect(0, 0, 32, 24);
          }
          const missing = now - lastFaceAt > 1000;
          updateHint(
            missing
              ? lowLight
                ? 'No face found and the room looks dark. Add light in front of you.'
                : 'No face found. Center your face in the frame.'
              : lowLight
                ? 'Low light may reduce accuracy.'
                : slowSeconds >= 3
                  ? `Face tracking is slow on this device (${trackingFps} frames a second), so calibration will take longer. Closing other apps and tabs can help.`
                  : null,
          );
          readoutCounter += 1;
          if (readoutCounter % 4 === 0) writeReadouts();
        };
        schedule(loop);
      } catch (err) {
        stream?.getTracks().forEach((t) => t.stop());
        if (!cancelled) {
          setError(describeCameraError(err));
          setPhase('error');
        }
      }
    })();

    return () => {
      cancelled = true;
      if (frameHandle !== null) {
        if (usingRvfc && video?.cancelVideoFrameCallback) video.cancelVideoFrameCallback(frameHandle);
        else cancelAnimationFrame(frameHandle);
      }
      stream?.getTracks().forEach((t) => t.stop());
      if (video) video.srcObject = null;
      ctx?.clearRect(0, 0, canvas?.width ?? 0, canvas?.height ?? 0);
    };
  }, [active]);

  const accent = STATUS_HUD_COLOR[status];

  return (
    <section className="hud hud-panel overflow-hidden" aria-label="Live camera with facial landmark overlay" ref={sectionRef}>
      <div className="relative aspect-[4/3] w-full bg-hud">
        {/* Mirrored like a looking glass; the overlay mirrors with it. Text lives outside the mirror. */}
        <div className="absolute inset-0" style={{ transform: 'scaleX(-1)' }}>
          <video ref={videoRef} className="h-full w-full object-cover" muted playsInline aria-hidden="true" />
          <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full object-cover" aria-hidden="true" />
        </div>

        {phase !== 'running' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-8 text-center">
            {phase === 'error' ? <CameraOff size={32} className="text-elevated-hud" aria-hidden /> : phase === 'starting' ? <ScanFace size={32} className="animate-pulse text-trace" aria-hidden /> : <Camera size={32} className="text-hud-dim" aria-hidden />}
            <p className="max-w-sm text-step-0 text-hud-text" role={phase === 'error' ? 'alert' : undefined}>
              {phase === 'error'
                ? error
                : phase === 'starting'
                  ? 'Starting the camera and loading the face model…'
                  : 'The camera starts with the session. Face analysis runs in this browser, and video frames are never sent.'}
            </p>
          </div>
        )}

        {phase === 'running' && (
          <>
            <div className="absolute left-3 top-3 flex items-center gap-2 rounded-md bg-hud/80 px-3 py-1.5 backdrop-blur-sm">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: accent }} aria-hidden />
              <span className="text-step--1 font-medium" style={{ color: accent }}>{STATUS_LABEL[status]}</span>
              <span className="num text-step--1 text-hud-dim">
                {status === 'CALIBRATING' ? `${Math.round(calibration * 100)}%` : anomaly !== null ? anomaly.toFixed(2) : ''}
              </span>
            </div>
            {faceError && (
              <p className="absolute inset-x-3 top-14 rounded-md bg-elevated/90 px-3 py-2 text-step--1 text-white" role="alert">
                Face analysis is unavailable, so only voice is measured. {faceError}
              </p>
            )}
            <span ref={trackingRef} className="num absolute right-3 top-3 rounded-md bg-hud/80 px-2.5 py-1.5 text-step--1 text-hud-dim backdrop-blur-sm" />
            {hint && !faceError && (
              <p className="absolute inset-x-3 top-14 rounded-md bg-watch-ink/85 px-3 py-2 text-step--1 text-white" role="status">
                {hint}
              </p>
            )}
          </>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-px border-t border-hud-line bg-hud-line text-step--1 sm:grid-cols-4">
        {(
          [
            ['asym', 'Lip-corner asymmetry'],
            ['lips', 'Lip aperture'],
            ['ear', 'Eye aspect ratio'],
            ['pose', 'Pitch / yaw'],
          ] as const
        ).map(([key, label]) => (
          <div key={key} className="bg-hud px-3 py-2.5">
            <dt className="text-hud-dim">{label}</dt>
            <dd className="num mt-0.5 text-step-1 text-hud-text">
              <span ref={(el) => { readouts.current[key] = el; }}>–</span>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
