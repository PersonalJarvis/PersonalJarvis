// cursor-trail.ts — Main-Thread-Coordinator. Plan §15.
//
// Erzeugt OffscreenCanvas, transferControlToOffscreen() in Worker,
// forwarded Cursor-Updates per postMessage. Worker macht das eigentliche
// Drawing (siehe cursor-trail-worker.ts).
//
// Quelle der Cursor-Updates: cursorBridge.cursorMoved-Signal aus dem
// Python-SHM-Reader-Thread (siehe OS-Level/src/overlay/window_glow.py).
// Wir bekommen physical px (Plan §11.2 / §15.3) und reichen sie
// unveraendert an den Worker — der konvertiert dpr-aware.

let worker: Worker | null = null;
let canvas: HTMLCanvasElement | null = null;

// Modul-level Resize-Handler — als named function, damit teardown ihn
// per ``removeEventListener`` wieder abmelden kann (Hot-Reload-Leak-Fix).
function _onWindowResize(): void {
  if (canvas === null || worker === null) return;
  resizeCanvasToViewport(canvas);
  worker.postMessage({
    type: "resize",
    width: canvas.width,
    height: canvas.height,
    dpr: window.devicePixelRatio || 1,
  });
}

interface InitOptions {
  /** Container fuer das Canvas. Default: ``document.body``. */
  container?: HTMLElement;
}

/**
 * Initialisiert Canvas + Worker. Idempotent.
 *
 * Muss VOR ``pushCursorPoint`` aufgerufen werden — sonst sind die
 * Pushes silent dropped.
 */
export function initCursorTrail(options: InitOptions = {}): void {
  if (worker !== null) return;

  const container = options.container ?? document.body;

  canvas = document.createElement("canvas");
  canvas.className = "cursor-trail-canvas";
  // Fullscreen-Surface, click-through.
  resizeCanvasToViewport(canvas);
  container.appendChild(canvas);

  // OffscreenCanvas + Worker — Plan §15.2.
  const offscreen = canvas.transferControlToOffscreen();

  worker = new Worker(
    new URL("./cursor-trail-worker.ts", import.meta.url),
    { type: "module" },
  );

  const dpr = window.devicePixelRatio || 1;
  worker.postMessage(
    { type: "init", canvas: offscreen, dpr },
    [offscreen],
  );

  // Resize-Listener — Edge-Glow-Window kann durch Monitor-Hotplug
  // umgeresizt werden. Named handler, damit teardown ihn entfernen kann.
  window.addEventListener("resize", _onWindowResize);
}

function resizeCanvasToViewport(c: HTMLCanvasElement): void {
  const dpr = window.devicePixelRatio || 1;
  const w = window.innerWidth;
  const h = window.innerHeight;
  c.style.width = `${w}px`;
  c.style.height = `${h}px`;
  c.width = Math.floor(w * dpr);
  c.height = Math.floor(h * dpr);
}

/**
 * Pushed einen Cursor-Punkt. Coords sind PHYSICAL px (Plan §11.2);
 * der Worker konvertiert per dpr.
 */
export function pushCursorPoint(physicalX: number, physicalY: number): void {
  if (worker === null) return;
  worker.postMessage({ type: "push", x: physicalX, y: physicalY });
}

/**
 * Cleart den Trail (z.B. bei action_ended).
 */
export function clearCursorTrail(): void {
  if (worker === null) return;
  worker.postMessage({ type: "clear" });
}

/**
 * Test-Helper: tear-down zwischen Tests.
 */
export function teardownCursorTrail(): void {
  // Resize-Listener zuerst abmelden — sonst feuert er noch waehrend
  // worker/canvas null-en und triggert keine Aktion, leakt aber den
  // Listener-Slot (Hot-Reload-Akkumulation).
  window.removeEventListener("resize", _onWindowResize);
  if (worker !== null) {
    worker.terminate();
    worker = null;
  }
  if (canvas !== null) {
    canvas.remove();
    canvas = null;
  }
}
