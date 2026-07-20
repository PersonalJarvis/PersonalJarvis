// Client-side input-level normalizer for the browser realtime surface.
//
// Port of the backend LevelNormalizer (jarvis/audio/mic_level.py): adaptive
// noise floor, a volume-faithful logarithmic range, and attack-fast /
// release-slow output smoothing. Raw RMS comes from the capture worklet on
// float32 samples in [-1, 1], the same scale the backend uses, so native and
// browser surfaces behave alike.

const MIN_NOISE_FLOOR = 0.0002;
const METER_FLOOR_RMS = 0.00025;
const METER_CEILING_RMS = 0.25;
const METER_LOG_SPAN = Math.log(METER_CEILING_RMS / METER_FLOOR_RMS);
const METER_CURVE = 1.15;

export class LevelMeter {
  private noiseFloor = 0.005;
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
    let raw = 0;
    if (gated >= METER_CEILING_RMS) {
      raw = 1;
    } else if (gated > METER_FLOOR_RMS) {
      const position = Math.log(gated / METER_FLOOR_RMS) / METER_LOG_SPAN;
      raw = position ** METER_CURVE;
    }

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
    this.smoothed = 0;
  }
}
