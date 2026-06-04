// typing-sweep.ts — Bottom-Edge Sweep pro Tastendruck. Plan §16.
//
// Single Sweep-Div am Bottom (4 px tall). triggerSweep() macht eine
// 200-ms-Animation (-100% -> +100% via translateX) und bumpt
// --intensity von 0.85 auf 1.2 fuer 200 ms.
//
// scheduleSweepBurst(durationHintMs, n) verteilt n Sweeps gleichmaessig
// ueber die Action-Duration (Plan: "pulse N/100 Sweeps spread evenly").
// Das ist die Ambient-Variante fuer eine ganze Tipp-Phase.

const SWEEP_DURATION_MS = 200; // Plan §16.1
const INTENSITY_BOOST = 1.2;
const INTENSITY_BASELINE = 1.0; // matched edge-glow.css default
const INTENSITY_LOWER = 0.85;

let sweepEl: HTMLElement | null = null;
let sweepResetTimer: number | null = null;
let intensityResetTimer: number | null = null;
const burstHandles: number[] = [];
let burstIntervalId: number | null = null;

/**
 * Erzeugt das Sweep-Div. Idempotent. Container default: ``document.body``.
 */
export function buildTypingSweep(container?: HTMLElement): void {
  if (sweepEl !== null) return;
  const parent = container ?? document.body;
  sweepEl = document.createElement("div");
  sweepEl.className = "typing-sweep";
  parent.appendChild(sweepEl);
}

/**
 * Triggert ein einzelnes Sweep + Intensity-Bump. Re-trigger-fest:
 * cancelt einen laufenden Sweep und startet neu.
 */
export function triggerSweep(): void {
  if (sweepEl === null) buildTypingSweep();
  const el = sweepEl as HTMLElement;
  const root = document.documentElement;

  // Sweep neu starten — class entfernen, reflow, class wieder setzen.
  el.classList.remove("active");
  void el.offsetWidth;
  el.classList.add("active");

  // Intensity-Bump.
  root.style.setProperty("--intensity", String(INTENSITY_BOOST));
  if (intensityResetTimer !== null) {
    window.clearTimeout(intensityResetTimer);
  }
  intensityResetTimer = window.setTimeout(() => {
    root.style.setProperty("--intensity", String(INTENSITY_BASELINE));
    intensityResetTimer = null;
  }, SWEEP_DURATION_MS);

  if (sweepResetTimer !== null) {
    window.clearTimeout(sweepResetTimer);
  }
  sweepResetTimer = window.setTimeout(() => {
    el.classList.remove("active");
    sweepResetTimer = null;
  }, SWEEP_DURATION_MS);
}

/**
 * Ambient-Burst: pulst ``ceil(durationHintMs / 100)`` Sweeps verteilt
 * ueber die action-Duration. Plan: "pulse N/100 Sweeps spread evenly".
 *
 * Cancelt eine laufende Burst (z.B. wenn ein neuer typing-Action den
 * laufenden ueberlappt).
 */
export function scheduleSweepBurst(durationHintMs: number): void {
  cancelSweepBurst();
  if (durationHintMs <= 0) {
    triggerSweep();
    return;
  }
  // 1 Sweep pro 100 ms, mindestens 1.
  const total = Math.max(1, Math.ceil(durationHintMs / 100));
  const interval = durationHintMs / total;

  // Ersten Sweep sofort, danach intervall-based.
  triggerSweep();
  let fired = 1;
  burstIntervalId = window.setInterval(() => {
    if (fired >= total) {
      cancelSweepBurst();
      return;
    }
    triggerSweep();
    fired += 1;
  }, interval);
}

/**
 * Bricht einen laufenden Burst ab. Sweep-Animation laeuft aus, aber
 * keine weiteren werden gespawnt.
 */
export function cancelSweepBurst(): void {
  if (burstIntervalId !== null) {
    window.clearInterval(burstIntervalId);
    burstIntervalId = null;
  }
  for (const h of burstHandles) {
    window.clearTimeout(h);
  }
  burstHandles.length = 0;
}

/**
 * Lower-Bound der Intensity zwischen Sweep-Bursts (action_started bis
 * action_ended bleibt --intensity bei INTENSITY_LOWER damit der Glow
 * dimmer wirkt zwischen Tastenanschlaegen). Optional — main.ts ruft
 * das auf typing-action_started auf.
 */
export function applySweepBaseline(active: boolean): void {
  document.documentElement.style.setProperty(
    "--intensity",
    active ? String(INTENSITY_LOWER) : String(INTENSITY_BASELINE),
  );
}

/**
 * Test-Helper: tear-down.
 */
export function teardownTypingSweep(): void {
  cancelSweepBurst();
  if (sweepResetTimer !== null) {
    window.clearTimeout(sweepResetTimer);
    sweepResetTimer = null;
  }
  if (intensityResetTimer !== null) {
    window.clearTimeout(intensityResetTimer);
    intensityResetTimer = null;
  }
  if (sweepEl !== null) {
    sweepEl.remove();
    sweepEl = null;
  }
  document.documentElement.style.setProperty(
    "--intensity",
    String(INTENSITY_BASELINE),
  );
}
