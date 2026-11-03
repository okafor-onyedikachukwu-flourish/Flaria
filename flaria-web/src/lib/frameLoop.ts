/**
 * One shared requestAnimationFrame loop for every imperative 60 Hz consumer
 * (charts, HUD readouts). Each subscriber declares its own maximum frame rate.
 * The loop stops when nothing is subscribed or the tab is hidden.
 */
type FrameCallback = (now: number) => void;
interface Subscriber { fn: FrameCallback; interval: number; last: number }

const subscribers = new Set<Subscriber>();
let rafId: number | null = null;

function tick(now: number): void {
  rafId = null;
  for (const s of subscribers) {
    if (now - s.last >= s.interval) {
      s.last = now;
      try {
        s.fn(now);
      } catch (err) {
        console.error('[frameLoop] subscriber failed', err);
      }
    }
  }
  schedule();
}

function schedule(): void {
  if (rafId === null && subscribers.size > 0 && document.visibilityState === 'visible') {
    rafId = requestAnimationFrame(tick);
  }
}

document.addEventListener('visibilitychange', schedule);

export function subscribeFrame(fn: FrameCallback, maxFps = 60): () => void {
  const sub: Subscriber = { fn, interval: 1000 / maxFps - 1, last: 0 };
  subscribers.add(sub);
  schedule();
  return () => {
    subscribers.delete(sub);
    if (subscribers.size === 0 && rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
  };
}
