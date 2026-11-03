/**
 * Landing hero: the product idea drawn live. Speech leaves a face-mesh
 * constellation as a trace printed on chart paper; the mouth opens with the
 * same syllable envelope a moment later, and faint attention lines tie the
 * mouth to the acoustic peaks that explain it (the cross-attention the model
 * performs). One continuous motion; a single still frame when the visitor
 * prefers reduced motion; paused when off-screen or the tab is hidden.
 */
import { useEffect, useRef } from 'react';

import { COLORS } from '@/lib/theme';

interface FacePoint { x: number; y: number; kind: 'outline' | 'field' | 'eye' | 'mouth' | 'brow' }

function buildFace(): FacePoint[] {
  const pts: FacePoint[] = [];
  for (let i = 0; i < 64; i += 1) {
    const a = (i / 64) * Math.PI * 2;
    pts.push({ x: Math.cos(a) * 0.78, y: Math.sin(a) * (a > 0 && a < Math.PI ? 1.02 : 0.98), kind: 'outline' });
  }
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < 170; i += 1) {
    const r = Math.sqrt((i + 0.5) / 170) * 0.92;
    const a = i * golden;
    const x = Math.cos(a) * r * 0.78;
    const y = Math.sin(a) * r;
    const nearEye = Math.hypot((Math.abs(x) - 0.3) / 0.2, (y + 0.22) / 0.1) < 1.2;
    const nearMouth = Math.hypot(x / 0.3, (y - 0.5) / 0.12) < 1.2;
    if (!nearEye && !nearMouth) pts.push({ x, y, kind: 'field' });
  }
  for (const side of [-1, 1]) {
    for (let i = 0; i < 16; i += 1) {
      const a = (i / 16) * Math.PI * 2;
      pts.push({ x: side * 0.3 + Math.cos(a) * 0.15, y: -0.22 + Math.sin(a) * 0.06, kind: 'eye' });
    }
    for (let i = 0; i < 7; i += 1) pts.push({ x: side * (0.18 + i * 0.04), y: -0.4 - Math.sin((i / 6) * Math.PI) * 0.05, kind: 'brow' });
  }
  for (let i = 0; i < 24; i += 1) {
    const a = (i / 24) * Math.PI * 2;
    pts.push({ x: Math.cos(a) * 0.24, y: 0.5 + Math.sin(a), kind: 'mouth' }); // y offset encodes the unit-circle sine for animation
  }
  return pts;
}

/** Syllabic envelope with phrase pauses: the same signal drives voice and mouth. */
function envelope(t: number): number {
  const syllable = Math.max(0, Math.sin(2 * Math.PI * 4.1 * t)) ** 0.8;
  const phrase = 0.5 + 0.5 * Math.tanh(3.2 * Math.sin(2 * Math.PI * 0.32 * t + 0.6));
  return syllable * phrase;
}

export function HeroTrace() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return undefined;
    const face = buildFace();
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let width = 0;
    let height = 0;
    let raf: number | null = null;
    let visible = true;
    const start = performance.now();

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const render = (t: number) => {
      ctx.clearRect(0, 0, width, height);
      const compact = width < 560;
      const fr = Math.min(height * 0.36, width * (compact ? 0.22 : 0.17));
      const fx = width - fr * 1.05 - 8;
      const fy = height * 0.5;
      const mouthY = fy + fr * 0.5;
      const mouthX = fx - fr * 0.05;
      const traceEnd = fx - fr * 0.95;
      const lag = 0.06;

      // --- voice trace, newest sample at the mouth, older samples drifting left
      const pxPerSecond = compact ? 90 : 120;
      const amp = height * 0.2;
      const peaks: { x: number; y: number; e: number }[] = [];
      ctx.lineWidth = 1.8;
      ctx.lineJoin = 'round';
      let prevE = 0;
      let rising = false;
      ctx.beginPath();
      for (let x = traceEnd; x >= 0; x -= 1.5) {
        const tau = t - (traceEnd - x) / pxPerSecond;
        const e = envelope(tau);
        const carrier = Math.sin(2 * Math.PI * 23 * tau) + 0.45 * Math.sin(2 * Math.PI * 46 * tau + 0.8);
        const y = fy + e * amp * 0.62 * carrier;
        if (x === traceEnd) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
        if (e > prevE) rising = true;
        else if (rising && e < prevE && prevE > 0.55 && peaks.length < 9) {
          peaks.push({ x, y: fy - prevE * amp * 0.62, e: prevE });
          rising = false;
        }
        prevE = e;
      }
      const grad = ctx.createLinearGradient(0, 0, traceEnd, 0);
      grad.addColorStop(0, 'rgba(29, 107, 115, 0)');
      grad.addColorStop(0.35, 'rgba(29, 107, 115, 0.55)');
      grad.addColorStop(1, COLORS.scrub);
      ctx.strokeStyle = grad;
      ctx.stroke();

      // --- attention lines: mouth -> acoustic peaks
      const mouthOpen = envelope(t - lag);
      for (const p of peaks) {
        const alpha = 0.08 + 0.28 * p.e * (1 - (traceEnd - p.x) / traceEnd);
        ctx.strokeStyle = `rgba(25, 167, 181, ${alpha.toFixed(3)})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(mouthX, mouthY);
        ctx.quadraticCurveTo((mouthX + p.x) / 2, Math.min(mouthY, p.y) - height * 0.12, p.x, p.y);
        ctx.stroke();
        ctx.fillStyle = `rgba(25, 167, 181, ${(alpha * 2).toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2.2, 0, Math.PI * 2);
        ctx.fill();
      }

      // --- face constellation
      const breathe = 1 + 0.008 * Math.sin(2 * Math.PI * 0.25 * t);
      const blinkPhase = (t % 4.3) / 4.3;
      const blink = blinkPhase > 0.96 ? Math.abs(Math.cos(((blinkPhase - 0.96) / 0.04) * Math.PI)) : 1;
      for (const p of face) {
        let x = p.x;
        let y = p.y;
        if (p.kind === 'mouth') {
          const open = 0.025 + 0.085 * mouthOpen;
          y = 0.5 + (p.y - 0.5) * open;
        } else if (p.kind === 'eye') {
          y = -0.22 + (p.y + 0.22) * blink;
        }
        const px = fx + x * fr * breathe;
        const py = fy + y * fr * breathe;
        const accent = p.kind === 'mouth' || p.kind === 'eye';
        ctx.fillStyle = accent ? COLORS.trace : p.kind === 'outline' ? 'rgba(20, 32, 40, 0.55)' : 'rgba(74, 90, 102, 0.42)';
        const r = accent ? 2 : p.kind === 'outline' ? 1.6 : 1.3;
        ctx.beginPath();
        ctx.arc(px, py, r, 0, Math.PI * 2);
        ctx.fill();
      }

      // Midline and lip-corner chord, echoing the live HUD.
      ctx.setLineDash([4, 5]);
      ctx.strokeStyle = 'rgba(20, 32, 40, 0.3)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(fx, fy - fr * 0.55);
      ctx.lineTo(fx, fy + fr * 0.95);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.strokeStyle = COLORS.scrub;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(fx - fr * 0.24, mouthY);
      ctx.lineTo(fx + fr * 0.24, mouthY);
      ctx.stroke();
    };

    const frame = (now: number) => {
      raf = null;
      if (!visible || document.visibilityState !== 'visible') return;
      render((now - start) / 1000);
      raf = requestAnimationFrame(frame);
    };
    const kick = () => {
      if (!reduced && raf === null && visible && document.visibilityState === 'visible') raf = requestAnimationFrame(frame);
    };

    resize();
    render(reduced ? 1.35 : 0);
    const ro = new ResizeObserver(() => {
      resize();
      render(reduced ? 1.35 : (performance.now() - start) / 1000);
    });
    ro.observe(canvas);
    const io = new IntersectionObserver(([entry]) => {
      visible = entry?.isIntersecting ?? true;
      kick();
    });
    io.observe(canvas);
    document.addEventListener('visibilitychange', kick);
    kick();

    return () => {
      if (raf !== null) cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      document.removeEventListener('visibilitychange', kick);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="h-[280px] w-full sm:h-[360px] lg:h-[460px]"
      role="img"
      aria-label="Animated illustration: a voice waveform flows from a face drawn as a mesh of points, with lines linking the moving mouth to peaks in the sound."
    />
  );
}
