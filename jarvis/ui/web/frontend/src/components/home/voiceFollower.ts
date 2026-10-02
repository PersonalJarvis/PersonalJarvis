/**
 * Turns the raw voice level (microphone or playback, ~30 samples a second)
 * into the two smooth signals voice mode's glow moves with.
 *
 *  - `level` — the slow one: an envelope that rises quickly and falls slowly
 *    (a voice, not a meter), then a short glide so sample steps never show.
 *    It decides how high the light stands.
 *  - `pulse` — the fast one: follows syllables, so the light's core flares
 *    on each word and settles in the gaps.
 *
 * Every constant is a time constant in seconds and every step is
 * `1 - exp(-dt / tau)`, so the motion is the same at 60 Hz, 144 Hz or right
 * after a dropped frame. Quiet speech is lifted (`^0.7`) so a normal voice
 * visibly moves the light.
 */
export interface VoiceSignals {
  level: number;
  pulse: number;
}

const ATTACK_S = 0.06;
const RELEASE_S = 0.45;
const GLIDE_S = 0.12;
const PULSE_ATTACK_S = 0.03;
const PULSE_RELEASE_S = 0.18;

const approach = (from: number, to: number, dt: number, tau: number) =>
  from + (to - from) * (1 - Math.exp(-dt / tau));

export function createVoiceFollower(): {
  step(dt: number, target: number): VoiceSignals;
} {
  let envelope = 0;
  let glide = 0;
  let pulse = 0;
  return {
    step(dt, raw) {
      const target = Number.isFinite(raw) ? Math.min(1, Math.max(0, raw)) : 0;
      envelope = approach(envelope, target, dt, target > envelope ? ATTACK_S : RELEASE_S);
      glide = approach(glide, envelope, dt, GLIDE_S);
      pulse = approach(pulse, target, dt, target > pulse ? PULSE_ATTACK_S : PULSE_RELEASE_S);
      return { level: Math.pow(glide, 0.7), pulse: Math.pow(pulse, 0.7) };
    },
  };
}
