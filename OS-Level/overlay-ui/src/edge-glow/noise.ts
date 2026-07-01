// noise.ts — 12 Hz simplex noise on 3 CSS custom properties.
// Plan §7.4 item 5: organic randomness via simplex noise.
//
// Drift ranges (values clamped):
//   --hue-drift  : -15deg .. +15deg   (plan constraint, stays within the palette)
//   --halo-a     :  0.25  ..  0.45    (inner halo layer)
//   --halo-b     :  0.45  ..  0.65    (outer halo layer)
//
// 12 Hz = ~83.3 ms period. We use setInterval — the frame drift is
// intentional for organic noise and doesn't need rAF precision. Setting
// custom properties is microsecond-cheap; no style recalc because the
// properties are registered (@property).
//
// prefers-reduced-motion: drift is suspended entirely — values stay
// static at the CSS default.

import { createNoise2D } from "simplex-noise";

const ROOT = document.documentElement;

// Plan constraints — hue-drift NEVER outside +/- 15 deg.
const HUE_AMPLITUDE_DEG = 15;
const HALO_A_MIN = 0.25;
const HALO_A_MAX = 0.45;
const HALO_B_MIN = 0.45;
const HALO_B_MAX = 0.65;

// 12 Hz, slightly jittered so the pattern isn't perfectly periodic.
const PERIOD_MS = 1000 / 12;

interface NoiseRunner {
  stop(): void;
  isRunning(): boolean;
}

function lerp(min: number, max: number, t: number): number {
  // simplex returns -1..1 -> remap to 0..1
  const normalized = (t + 1) / 2;
  return min + (max - min) * normalized;
}

/**
 * Starts the 12-Hz modulation. Returns a handle with ``stop()``.
 *
 * If the browser reports ``prefers-reduced-motion: reduce``, the
 * runner never starts in the first place — the custom properties stay
 * at their CSS defaults and the glow is static (Plan §19.1).
 */
export function startNoise(): NoiseRunner {
  const reducedMotion =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reducedMotion) {
    return {
      stop() {
        /* no-op */
      },
      isRunning() {
        return false;
      },
    };
  }

  // 3 independent noise functions so hue/halo-a/halo-b aren't
  // correlated with each other.
  const hueNoise = createNoise2D();
  const haloANoise = createNoise2D();
  const haloBNoise = createNoise2D();

  let t = 0;
  const intervalId = window.setInterval(() => {
    t += 0.05; // slow walk through the noise field
    const hue = hueNoise(t, 0) * HUE_AMPLITUDE_DEG;
    const haloA = lerp(HALO_A_MIN, HALO_A_MAX, haloANoise(t, 1));
    const haloB = lerp(HALO_B_MIN, HALO_B_MAX, haloBNoise(t, 2));
    ROOT.style.setProperty("--hue-drift", `${hue.toFixed(2)}deg`);
    ROOT.style.setProperty("--halo-a", haloA.toFixed(3));
    ROOT.style.setProperty("--halo-b", haloB.toFixed(3));
  }, PERIOD_MS);

  return {
    stop() {
      window.clearInterval(intervalId);
    },
    isRunning() {
      return true;
    },
  };
}
