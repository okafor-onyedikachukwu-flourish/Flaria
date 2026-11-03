/**
 * pcm-capture: anti-aliased resampling from the device rate (44.1/48 kHz) to
 * the analysis rate (16 kHz). A 63-tap Blackman-windowed sinc low-pass at
 * 0.45 x target rate removes content that would alias, then linear
 * interpolation reads the filtered stream at the target sample positions.
 * Emits fixed-size Float32 chunks (transferred, not copied) with their RMS.
 */
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.targetRate = opts.targetRate || 16000;
    this.chunkSize = opts.chunkSize || 1600;
    this.ratio = sampleRate / this.targetRate;

    const taps = 63;
    const m = (taps - 1) / 2;
    const fc = Math.min(0.5, (0.45 * this.targetRate) / sampleRate);
    const h = new Float32Array(taps);
    let sum = 0;
    for (let i = 0; i < taps; i += 1) {
      const x = i - m;
      const sinc = x === 0 ? 2 * fc : Math.sin(2 * Math.PI * fc * x) / (Math.PI * x);
      const w = 0.42 - 0.5 * Math.cos((2 * Math.PI * i) / (taps - 1)) + 0.08 * Math.cos((4 * Math.PI * i) / (taps - 1));
      h[i] = sinc * w;
      sum += h[i];
    }
    for (let i = 0; i < taps; i += 1) h[i] /= sum;
    this.h = h;
    this.history = new Float32Array(taps);
    this.hpos = 0;

    this.n = 0; // input samples consumed
    this.nextOut = 0; // fractional input position of the next output sample
    this.prevY = 0;
    this.chunk = new Float32Array(this.chunkSize);
    this.fill = 0;
    this.sumSq = 0;
  }

  filter(x) {
    const taps = this.h.length;
    this.history[this.hpos] = x;
    let acc = 0;
    let idx = this.hpos;
    for (let k = 0; k < taps; k += 1) {
      acc += this.h[k] * this.history[idx];
      idx = idx === 0 ? taps - 1 : idx - 1;
    }
    this.hpos = (this.hpos + 1) % taps;
    return acc;
  }

  emit(sample) {
    this.chunk[this.fill] = sample;
    this.sumSq += sample * sample;
    this.fill += 1;
    if (this.fill === this.chunkSize) {
      const out = this.chunk;
      const rms = Math.sqrt(this.sumSq / this.chunkSize);
      this.port.postMessage({ type: 'chunk', samples: out, rms }, [out.buffer]);
      this.chunk = new Float32Array(this.chunkSize);
      this.fill = 0;
      this.sumSq = 0;
    }
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channel = input[0];
    for (let i = 0; i < channel.length; i += 1) {
      const y = this.filter(channel[i]);
      if (this.n >= 1) {
        while (this.nextOut <= this.n) {
          const frac = this.nextOut - (this.n - 1);
          this.emit(this.prevY + (y - this.prevY) * frac);
          this.nextOut += this.ratio;
        }
      }
      this.prevY = y;
      this.n += 1;
    }
    return true;
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor);
