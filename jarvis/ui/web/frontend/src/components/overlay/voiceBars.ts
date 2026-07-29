/**
 * Geometry and smoothing math for the voice visualizer pill — pure, so both
 * the static style preview and the live visualizer draw the SAME graphic and
 * the animation math can be unit-tested without a DOM or an audio device.
 *
 * The numbers mirror the desktop JarvisBar renderer (``jarvis/ui/jarvisbar/
 * renderer.py``) so the web surface and the on-screen overlay read as one
 * product: a dark pill with a gold rim and a slim row of rounded strokes.
 * Only the palette differs — see ``OverlayStylePreviews`` for why.
 */

/** Preview/visualizer viewBox. Everything below is in these units. */
export const VIEW_W = 100;
export const VIEW_H = 40;

/** The pill: x 6..94, y 11..29 — a 88x18 rounded rectangle. */
export const PILL_X = 6;
export const PILL_Y = 11;
export const PILL_W = VIEW_W - 2 * PILL_X;
export const PILL_H = 18;
export const PILL_R = PILL_H / 2;
export const PILL_CY = PILL_Y + PILL_H / 2;

/** Bar row width. The two surfaces share the pill and the maths but not this
 *  number, because seven chunky strokes and eighteen slim ones do not want the
 *  same room: the thumbnail keeps the tight middle cluster it always had (it
 *  portrays the RESTING desktop bar), while the live row spreads out to give a
 *  scrolling waveform something to scroll across. */
export const PREVIEW_BAR_SPAN = 48;
export const LIVE_BAR_SPAN = 62;
export const BAR_MIN_H = 2.4;
export const BAR_MAX_H = 15;

/** Bar count of the live visualizer. Wide enough to read as a waveform, few
 *  enough that every stroke stays a distinct ~2 px at the size the pill
 *  renders in the sidebar. */
export const LIVE_BAR_COUNT = 18;

/** Static heights of the settings/onboarding thumbnail (unchanged look). */
export const PREVIEW_BAR_HEIGHTS = [6, 11, 15, 8, 14, 9, 7];

/** How often the live waveform advances by one column (ms).
 *
 * Time-based, NOT frame-based: the row must scroll at the same speed on a
 * 60 Hz laptop and a 144 Hz monitor. ~33 ms matches the capture worklet's
 * ~30 Hz level cadence, so every column is one real measurement rather than an
 * interpolated in-between. */
export const COLUMN_MS = 33;

/** Bar stroke width for a given count, sized so the gaps stay even. */
export function barWidth(count: number, span: number = LIVE_BAR_SPAN): number {
  if (count <= 1) return 3;
  // Half the pitch, capped so a short row does not turn into fat blocks.
  return Math.min(3, (span / (count - 1)) * 0.5);
}

/** X-positions of ``n`` items centred on ``cx`` across ``span``.
 *
 * The direct counterpart of ``renderer.evenly_spaced``; the preview used to
 * hard-code ``24 + i * 8``, which left its row 2 units off the pill's centre. */
export function evenlySpaced(cx: number, span: number, n: number): number[] {
  if (n <= 1) return [cx];
  const x0 = cx - span / 2;
  const step = span / (n - 1);
  return Array.from({ length: n }, (_, i) => x0 + i * step);
}

export function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

/** Map a 0..1 level to a bar height inside the pill. */
export function barHeight(value: number): number {
  return BAR_MIN_H + (BAR_MAX_H - BAR_MIN_H) * clamp01(value);
}

/** Attack/release time constants (seconds) of the displayed level. */
export const ATTACK_TAU_S = 0.045;
export const RELEASE_TAU_S = 0.18;

/**
 * Ease ``current`` toward ``target`` over ``dtS`` seconds.
 *
 * Frame-rate independent on purpose: the exponential is evaluated against the
 * REAL elapsed time, so the same voice produces the same visible rise on a
 * 60 Hz and a 144 Hz display. A fixed per-frame factor (what the desktop
 * renderer can afford, because it drives its own fixed-cadence loop) would
 * make the browser visualizer twice as twitchy on a high-refresh monitor.
 *
 * Asymmetric like the desktop bar: the rise is near-instant so a syllable
 * registers on the frame its sample arrives, the fall is slower so the row
 * does not flicker between words — and a target of zero snaps all the way
 * down, because a sub-visible tail otherwise keeps the bars wiggling in
 * silence.
 */
export function smoothLevel(
  current: number,
  target: number,
  dtS: number,
  attackTauS: number = ATTACK_TAU_S,
  releaseTauS: number = RELEASE_TAU_S,
): number {
  const to = clamp01(target);
  if (!Number.isFinite(dtS) || dtS <= 0) return clamp01(current);
  const tau = to > current ? attackTauS : releaseTauS;
  if (tau <= 0) return to;
  const next = current + (to - current) * (1 - Math.exp(-dtS / tau));
  return to <= 0 && next < 0.01 ? 0 : clamp01(next);
}

/** Gaussian width of the travelling activity highlight, in row fractions. */
export const SWEEP_WIDTH = 0.16;

/**
 * Brightness of bar ``index`` under a highlight sitting at ``phase`` (0..1).
 *
 * This is the "we are working on it" motion — used whenever there IS activity
 * but no measured level to draw: connecting, transcribing, and the assistant's
 * own reply. The distinction is deliberate and load-bearing: a scrolling
 * waveform claims "this is your voice, measured right now", so it is only ever
 * drawn from real microphone samples. Anything unmeasured gets the sweep
 * instead, which promises motion and nothing more.
 *
 * The distance is measured around a ring, so the highlight leaves on the right
 * and re-enters on the left without a visible jump.
 */
export function sweepGain(
  index: number,
  count: number,
  phase: number,
  width: number = SWEEP_WIDTH,
): number {
  if (count <= 1) return 1;
  const pos = index / (count - 1);
  const wrapped = phase - Math.floor(phase);
  let d = Math.abs(pos - wrapped);
  if (d > 0.5) d = 1 - d;
  return Math.exp(-(d * d) / (2 * width * width));
}

/** Seconds one sweep needs to cross the row, per phase. Each phase gets its
 *  own tempo so the three unmeasured states stay distinguishable at a glance:
 *  a brisk sweep while work is in flight, a calm one while the reply plays. */
export const SWEEP_PERIOD_S: Record<string, number> = {
  connecting: 1.6,
  working: 1.05,
  speaking: 2.2,
};

/**
 * Read a scrolling history ring so index 0 is the OLDEST column and
 * ``count - 1`` the newest — the newest sample enters at the right edge and
 * the row travels left, the way a voice memo draws itself.
 */
export function ringValue(
  history: readonly number[],
  head: number,
  index: number,
): number {
  const n = history.length;
  if (n === 0) return 0;
  const at = (((head + index) % n) + n) % n;
  return history[at] ?? 0;
}
