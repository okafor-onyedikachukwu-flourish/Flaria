/**
 * Fixed-capacity time series held in typed arrays. Writes never allocate, so
 * 60 Hz ingestion produces no garbage and never touches React state.
 */
export class SeriesBuffer {
  readonly capacity: number;
  private readonly t: Float64Array;
  private readonly v: Float32Array;
  private head = 0;
  private count = 0;

  constructor(capacity: number) {
    this.capacity = capacity;
    this.t = new Float64Array(capacity);
    this.v = new Float32Array(capacity);
  }

  get size(): number {
    return this.count;
  }

  push(tSeconds: number, value: number | null | undefined): void {
    this.t[this.head] = tSeconds;
    this.v[this.head] = value == null || !Number.isFinite(value) ? Number.NaN : value;
    this.head = (this.head + 1) % this.capacity;
    if (this.count < this.capacity) this.count += 1;
  }

  /** Most recent finite value, or null. */
  latest(): number | null {
    for (let i = 1; i <= this.count; i += 1) {
      const value = this.v[(this.head - i + this.capacity) % this.capacity];
      if (Number.isFinite(value)) return value;
    }
    return null;
  }

  /** Visit samples newer than `sinceSeconds`, oldest first. Returns the number visited. */
  forEachSince(sinceSeconds: number, fn: (t: number, v: number, index: number) => void): number {
    let start = this.count;
    // Walk back from the newest sample to find the window start (series is time-ordered).
    for (let i = 1; i <= this.count; i += 1) {
      if (this.t[(this.head - i + this.capacity) % this.capacity] < sinceSeconds) {
        start = i - 1;
        break;
      }
    }
    for (let k = 0; k < start; k += 1) {
      const idx = (this.head - start + k + this.capacity) % this.capacity;
      fn(this.t[idx], this.v[idx], k);
    }
    return start;
  }

  clear(): void {
    this.t.fill(0);
    this.v.fill(0);
    this.head = 0;
    this.count = 0;
  }
}
