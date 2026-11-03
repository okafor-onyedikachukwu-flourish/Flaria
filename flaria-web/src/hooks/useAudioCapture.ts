/**
 * Microphone capture through an AudioWorklet that low-pass filters and
 * resamples to the server rate, emitting 100 ms Float32 chunks.
 *
 * Browser voice processing (echo cancellation, noise suppression, automatic
 * gain) is disabled: those filters reshape exactly the cycle-to-cycle detail
 * that jitter, shimmer and HNR measure. PCM is never written to storage on
 * the client; chunks are handed to the socket and released.
 */
import { useEffect, useRef, useState } from 'react';

export type AudioCaptureState = 'idle' | 'starting' | 'running' | 'error';

interface Options {
  active: boolean;
  targetRate?: number;
  chunkSize?: number;
  onChunk: (samples: Float32Array) => void;
}

function describe(err: unknown): string {
  const name = (err as { name?: string })?.name;
  switch (name) {
    case 'NotAllowedError':
      return 'Microphone access is blocked. Allow it in your browser’s site settings, then start the session again.';
    case 'NotFoundError':
      return 'No microphone was found. Connect one, then start the session again.';
    case 'NotReadableError':
      return 'The microphone is in use by another application. Close it, then start the session again.';
    default:
      return `The microphone couldn’t start: ${(err as Error)?.message ?? 'unknown error'}`;
  }
}

export function useAudioCapture({ active, targetRate = 16_000, chunkSize = 1_600, onChunk }: Options) {
  const [state, setState] = useState<AudioCaptureState>('idle');
  const [error, setError] = useState<string | null>(null);
  const levelRef = useRef(0);
  const onChunkRef = useRef(onChunk);
  onChunkRef.current = onChunk;

  useEffect(() => {
    if (!active) {
      setState('idle');
      return undefined;
    }
    let cancelled = false;
    let stream: MediaStream | null = null;
    let ctx: AudioContext | null = null;
    let node: AudioWorkletNode | null = null;

    (async () => {
      setState('starting');
      setError(null);
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
          video: false,
        });
        if (cancelled) return;
        ctx = new AudioContext({ latencyHint: 'interactive' });
        await ctx.audioWorklet.addModule('/worklets/pcm-capture.worklet.js');
        if (cancelled) return;
        const source = ctx.createMediaStreamSource(stream);
        node = new AudioWorkletNode(ctx, 'pcm-capture', {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          channelCount: 1,
          processorOptions: { targetRate, chunkSize },
        });
        node.port.onmessage = (event: MessageEvent<{ type: string; samples?: Float32Array; rms?: number }>) => {
          if (event.data.type === 'chunk' && event.data.samples) {
            levelRef.current = event.data.rms ?? 0;
            onChunkRef.current(event.data.samples);
          }
        };
        // A muted sink keeps the graph pulling audio in every browser without playing it back.
        const sink = ctx.createGain();
        sink.gain.value = 0;
        source.connect(node).connect(sink).connect(ctx.destination);
        await ctx.resume();
        stream.getAudioTracks()[0]?.addEventListener('ended', () => {
          setError('The microphone was disconnected. Reconnect it, then start the session again.');
          setState('error');
        });
        setState('running');
      } catch (err) {
        if (!cancelled) {
          setError(describe(err));
          setState('error');
        }
      }
    })();

    return () => {
      cancelled = true;
      node?.port.close();
      node?.disconnect();
      stream?.getTracks().forEach((t) => t.stop());
      void ctx?.close();
      levelRef.current = 0;
    };
  }, [active, targetRate, chunkSize]);

  return { state, error, levelRef };
}
