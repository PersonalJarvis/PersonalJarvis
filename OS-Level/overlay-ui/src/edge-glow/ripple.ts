// ripple.ts — Click-ripple effect. Plan §14.
//
// Pre-warms a pool of 8 divs (Plan §14.2) so burst clicks don't trigger
// appendChild/layout jank. The animation runs entirely in CSS
// (transform+opacity, compositor-only).
//
// Coords from Hauptjarvis are PHYSICAL pixels (Plan §14.4). We convert
// to CSS-logical at the renderer boundary — one spot, not an
// off-by-DPI bug scattered everywhere.

const POOL_SIZE = 8;
const RIPPLE_LIFETIME_MS = 600; // Plan §14.1

interface RippleSlot {
  el: HTMLDivElement;
  inUse: boolean;
  resetTimer: number | null;
}

let pool: RippleSlot[] | null = null;
let layer: HTMLElement | null = null;

/**
 * Creates the pool. Idempotent — repeated calls are no-ops.
 *
 * The container must be a ``.ripple-layer`` surface (per CSS
 * pointer-events:none, position:fixed inset:0). buildRipplePool
 * appends it to ``document.body``; the caller can also pass its own
 * container element (testable).
 */
export function buildRipplePool(container?: HTMLElement): void {
  if (pool !== null) return;

  layer = container ?? document.querySelector<HTMLElement>(".ripple-layer");
  if (layer === null) {
    layer = document.createElement("div");
    layer.className = "ripple-layer";
    document.body.appendChild(layer);
  }

  pool = [];
  for (let i = 0; i < POOL_SIZE; i += 1) {
    const el = document.createElement("div");
    el.className = "ripple";
    el.dataset["slot"] = String(i);
    // Initial off-screen + invisible — takes up no space.
    el.style.opacity = "0";
    el.style.transform = "translate(-9999px, -9999px) scale(0)";
    layer.appendChild(el);
    pool.push({ el, inUse: false, resetTimer: null });
  }
}

/**
 * Spawns a ripple at the click coordinate. Plan §14.4 — physical px in,
 * CSS px out via ``window.devicePixelRatio``.
 *
 * Returns ``true`` if a pool slot was available, ``false`` if all 8
 * slots are active (a burst of > 8 clicks within 600 ms — the 9th
 * gets dropped; an acceptable edge case).
 */
export function triggerRipple(physicalX: number, physicalY: number): boolean {
  if (pool === null) {
    buildRipplePool();
  }
  // pool was just built, guaranteed non-null.
  const p = pool as RippleSlot[];

  const slot = p.find((s) => !s.inUse);
  if (slot === undefined) {
    // All 8 slots in use. Plan §14.5 (edge case) says "acceptable".
    return false;
  }

  const dpr = window.devicePixelRatio || 1;
  const cssX = physicalX / dpr;
  const cssY = physicalY / dpr;

  slot.inUse = true;
  // Re-trigger the CSS animation: reset first, then set.
  // Force reflow between the two class toggles so the animation
  // actually restarts (important on pool reuse).
  slot.el.classList.remove("active");
  slot.el.style.transform = `translate(${cssX}px, ${cssY}px) scale(0)`;
  slot.el.style.opacity = "1";
  // Force reflow.
  void slot.el.offsetWidth;
  slot.el.classList.add("active");

  // Reset timer: back into the pool after 600 ms.
  if (slot.resetTimer !== null) {
    window.clearTimeout(slot.resetTimer);
  }
  slot.resetTimer = window.setTimeout(() => {
    slot.el.classList.remove("active");
    slot.el.style.opacity = "0";
    slot.el.style.transform = "translate(-9999px, -9999px) scale(0)";
    slot.inUse = false;
    slot.resetTimer = null;
  }, RIPPLE_LIFETIME_MS);

  return true;
}

/**
 * Test helper: current pool utilization. Not relevant to production.
 */
export function ripplePoolStats(): { total: number; inUse: number } {
  if (pool === null) return { total: 0, inUse: 0 };
  return {
    total: pool.length,
    inUse: pool.filter((s) => s.inUse).length,
  };
}

/**
 * Test helper: reset the pool between tests.
 */
export function resetRipplePool(): void {
  if (pool !== null) {
    for (const slot of pool) {
      if (slot.resetTimer !== null) {
        window.clearTimeout(slot.resetTimer);
      }
      slot.el.remove();
    }
  }
  if (layer !== null && layer.parentElement !== null) {
    layer.remove();
  }
  pool = null;
  layer = null;
}
