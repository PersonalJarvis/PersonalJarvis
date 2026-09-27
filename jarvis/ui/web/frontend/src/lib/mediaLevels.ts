/** Ephemeral /ws/audio snapshot. Values are normalized, never PCM or text. */
export interface MediaLevels {
  type: "media_levels";
  input_level: number;
  output_level: number;
  input_active: boolean;
  playback_active: boolean;
}

/** Audio-clock hysteresis: short gaps between words keep the speaking look. */
export class MediaActivity {
  active = false;
  private quietFrames = 0;

  constructor(private threshold: number, private hangoverFrames: number) {}

  update(rms: number): boolean {
    const previous = this.active;
    if (Number.isFinite(rms) && rms > this.threshold) {
      this.quietFrames = 0;
      this.active = true;
    } else if (++this.quietFrames >= this.hangoverFrames) {
      this.active = false;
    }
    return previous !== this.active;
  }

  reset(): void {
    this.active = false;
    this.quietFrames = 0;
  }
}
