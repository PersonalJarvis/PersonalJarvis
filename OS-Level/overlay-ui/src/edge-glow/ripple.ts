// ripple.ts — Click-Ripple-Effect. Plan §14.
//
// Pre-warm Pool von 8 Divs (Plan §14.2) damit Burst-Clicks keinen
// appendChild/Layout-Jank ausloesen. Animation laeuft komplett im CSS
// (transform+opacity, compositor-only).
//
// Coords vom Hauptjarvis sind PHYSICAL pixels (Plan §14.4). Wir
// rechnen am Renderer-Boundary in CSS-logical um — ein Punkt, kein
// verstreuter Off-by-DPI-Bug.

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
 * Erzeugt den Pool. Idempotent — mehrere Aufrufe sind no-op.
 *
 * Container muss eine ``.ripple-layer``-Surface sein (per CSS
 * pointer-events:none, position:fixed inset:0). buildRipplePool laesst
 * sie an ``document.body`` haengen; der Caller kann auch eigenes
 * Container-Element uebergeben (testbar).
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
    // Initial off-screen + invisible — nimmt keinen Platz ein.
    el.style.opacity = "0";
    el.style.transform = "translate(-9999px, -9999px) scale(0)";
    layer.appendChild(el);
    pool.push({ el, inUse: false, resetTimer: null });
  }
}

/**
 * Spawnt ein Ripple am Click-Coord. Plan §14.4 — physical px in,
 * CSS-px out via ``window.devicePixelRatio``.
 *
 * Returnt ``true`` wenn ein Pool-Slot verfuegbar war, ``false`` wenn
 * alle 8 Slots aktiv sind (Burst > 8 Klicks innerhalb 600 ms — der
 * 9. wird droppt; akzeptabler Edge-Case).
 */
export function triggerRipple(physicalX: number, physicalY: number): boolean {
  if (pool === null) {
    buildRipplePool();
  }
  // pool wurde gerade gebaut, sicher non-null.
  const p = pool as RippleSlot[];

  const slot = p.find((s) => !s.inUse);
  if (slot === undefined) {
    // Alle 8 Slots in-use. Plan §14.5 (Edge-Case) sagt "akzeptabel".
    return false;
  }

  const dpr = window.devicePixelRatio || 1;
  const cssX = physicalX / dpr;
  const cssY = physicalY / dpr;

  slot.inUse = true;
  // Re-trigger der CSS-Animation: erst zuruecksetzen, dann setzen.
  // Force reflow zwischen den beiden Klassen-Toggles damit die Animation
  // wirklich neu startet (wichtig beim Pool-Reuse).
  slot.el.classList.remove("active");
  slot.el.style.transform = `translate(${cssX}px, ${cssY}px) scale(0)`;
  slot.el.style.opacity = "1";
  // Force reflow.
  void slot.el.offsetWidth;
  slot.el.classList.add("active");

  // Reset-Timer: nach 600 ms zurueck in den Pool.
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
 * Test-Helper: aktuelle Pool-Auslastung. Production nicht relevant.
 */
export function ripplePoolStats(): { total: number; inUse: number } {
  if (pool === null) return { total: 0, inUse: 0 };
  return {
    total: pool.length,
    inUse: pool.filter((s) => s.inUse).length,
  };
}

/**
 * Test-Helper: Pool resetten zwischen Tests.
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
