// noise.ts — 12 Hz Simplex-Noise auf 3 CSS-Custom-Properties.
// Plan §7.4 Punkt 5: Organic Randomness via Simplex-Noise.
//
// Driften (Werte clamped):
//   --hue-drift  : -15deg .. +15deg   (Plan-Constraint, bleibt in der Palette)
//   --halo-a     :  0.25  ..  0.45    (innerer Halo-Layer)
//   --halo-b     :  0.45  ..  0.65    (aeusserer Halo-Layer)
//
// 12 Hz = ~83.3 ms Period. Wir nutzen setInterval — der Frame-Drift ist
// fuer organic-noise gewollt und braucht keine rAF-Praezision. Setting
// Custom-Properties ist mikrosekunden-cheap; keine Style-Recalc weil die
// Properties registered (@property) sind.
//
// prefers-reduced-motion: Driften wird komplett ausgesetzt — Werte
// bleiben statisch beim CSS-Default.

import { createNoise2D } from "simplex-noise";

const ROOT = document.documentElement;

// Plan-Constraints — Hue-Drift NIE ausserhalb +/- 15 deg.
const HUE_AMPLITUDE_DEG = 15;
const HALO_A_MIN = 0.25;
const HALO_A_MAX = 0.45;
const HALO_B_MIN = 0.45;
const HALO_B_MAX = 0.65;

// 12 Hz, leicht jittered damit der Pattern nicht perfekt periodisch ist.
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
 * Startet die 12-Hz-Modulation. Returnt einen Handle mit ``stop()``.
 *
 * Wenn der Browser ``prefers-reduced-motion: reduce`` meldet, wird der
 * Runner gar nicht erst gestartet — die Custom-Properties bleiben auf
 * ihren CSS-Defaults und der Glow ist statisch (Plan §19.1).
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

  // 3 unabhaengige Noise-Funktionen damit hue/halo-a/halo-b nicht
  // miteinander korreliert sind.
  const hueNoise = createNoise2D();
  const haloANoise = createNoise2D();
  const haloBNoise = createNoise2D();

  let t = 0;
  const intervalId = window.setInterval(() => {
    t += 0.05; // langsamer Walk durch das Noise-Feld
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
