// Client-side input-level normalizer for the browser realtime surface.
//
// Simplified port of the backend LevelNormalizer (jarvis/audio/mic_level.py):
// adaptive noise floor (EMA on quiet frames), peak auto-gain (fast attack,
// slow decay), and attack-fast / release-slow output smoothing so the level
// indicator pulses naturally instead of flickering. Raw RMS comes from the
// capture worklet on float32 samples in [-1, 1], the same scale the backend
// uses, so both surfaces behave alike.

const MIN_NOISE_FLOOR = 0.0002;
const MIN_PEAK = 0.004;

export class LevelMeter {
  private noiseFloor = 0.005;
  private peak = MIN_PEAK;
  private smoothed = 0;

  /** Normalize one raw RMS sample to a reactive 0..1 level. */
  push(rms: number): number {
    const value = Number.isFinite(rms) && rms > 0 ? rms : 0;

    if (value < this.noiseFloor * 1.5) {
      this.noiseFloor = 0.95 * this.noiseFloor + 0.05 * value;
    }
    this.noiseFloor = Math.max(this.noiseFloor, MIN_NOISE_FLOOR);

    const speechThreshold = this.noiseFloor * 3.0;
    const gated = Math.max(0, value - speechThreshold);

    if (gated > this.peak) this.peak = gated;
    else this.peak *= 0.997;
    this.peak = Math.max(this.peak, MIN_PEAK);

    const raw = Math.min(1, gated / this.peak);

    if (raw > this.smoothed) {
      // attack fast
      this.smoothed = 0.4 * this.smoothed + 0.6 * raw;
    } else {
      // release slow
      this.smoothed = 0.75 * this.smoothed + 0.25 * raw;
    }
    return this.smoothed;
  }

  reset(): void {
    this.noiseFloor = 0.005;
    this.peak = MIN_PEAK;
    this.smoothed = 0;
  }
}
