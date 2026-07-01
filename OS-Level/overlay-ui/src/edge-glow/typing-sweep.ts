// typing-sweep.ts — Bottom-edge sweep per keystroke. Plan §16.
//
// Single sweep div at the bottom (4 px tall). triggerSweep() runs a
// 200-ms animation (-100% -> +100% via translateX) and bumps
// --intensity from 0.85 to 1.2 for 200 ms.
//
// scheduleSweepBurst(durationHintMs, n) distributes n sweeps evenly
// over the action duration (Plan: "pulse N/100 sweeps spread evenly").
// This is the ambient variant for an entire typing phase.

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
 * Creates the sweep div. Idempotent. Container default: ``document.body``.
 */
export function buildTypingSweep(container?: HTMLElement): void {
  if (sweepEl !== null) return;
  const parent = container ?? document.body;
  sweepEl = document.createElement("div");
  sweepEl.className = "typing-sweep";
  parent.appendChild(sweepEl);
}

/**
 * Triggers a single sweep + intensity bump. Re-trigger safe:
 * cancels a running sweep and starts a new one.
 */
export function triggerSweep(): void {
  if (sweepEl === null) buildTypingSweep();
  const el = sweepEl as HTMLElement;
  const root = document.documentElement;

  // Restart the sweep — remove class, reflow, re-add class.
  el.classList.remove("active");
  void el.offsetWidth;
  el.classList.add("active");

  // Intensity bump.
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
 * Ambient burst: pulses ``ceil(durationHintMs / 100)`` sweeps spread
 * over the action duration. Plan: "pulse N/100 sweeps spread evenly".
 *
 * Cancels a running burst (e.g. when a new typing action overlaps
 * the running one).
 */
export function scheduleSweepBurst(durationHintMs: number): void {
  cancelSweepBurst();
  if (durationHintMs <= 0) {
    triggerSweep();
    return;
  }
  // 1 sweep per 100 ms, at least 1.
  const total = Math.max(1, Math.ceil(durationHintMs / 100));
  const interval = durationHintMs / total;

  // First sweep immediately, then interval-based.
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
 * Aborts a running burst. The sweep animation runs out, but no
 * further sweeps get spawned.
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
 * Lower bound of intensity between sweep bursts (from action_started
 * to action_ended, --intensity stays at INTENSITY_LOWER so the glow
 * looks dimmer between keystrokes). Optional — main.ts calls this on
 * typing-action_started.
 */
export function applySweepBaseline(active: boolean): void {
  document.documentElement.style.setProperty(
    "--intensity",
    active ? String(INTENSITY_LOWER) : String(INTENSITY_BASELINE),
  );
}

/**
 * Test helper: tear-down.
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
