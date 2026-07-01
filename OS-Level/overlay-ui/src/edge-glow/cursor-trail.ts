// cursor-trail.ts — Main-thread coordinator. Plan §15.
//
// Creates the OffscreenCanvas, transferControlToOffscreen() to the worker,
// forwards cursor updates via postMessage. The worker does the actual
// drawing (see cursor-trail-worker.ts).
//
// Source of the cursor updates: the cursorBridge.cursorMoved signal from
// the Python SHM reader thread (see OS-Level/src/overlay/window_glow.py).
// We receive physical px (Plan §11.2 / §15.3) and pass them through
// unchanged to the worker — which converts them dpr-aware.

let worker: Worker | null = null;
let canvas: HTMLCanvasElement | null = null;

// Module-level resize handler — as a named function so teardown can
// unregister it via ``removeEventListener`` (hot-reload leak fix).
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
  /** Container for the canvas. Default: ``document.body``. */
  container?: HTMLElement;
}

/**
 * Initializes the canvas + worker. Idempotent.
 *
 * Must be called BEFORE ``pushCursorPoint`` — otherwise pushes are
 * silently dropped.
 */
export function initCursorTrail(options: InitOptions = {}): void {
  if (worker !== null) return;

  const container = options.container ?? document.body;

  canvas = document.createElement("canvas");
  canvas.className = "cursor-trail-canvas";
  // Fullscreen surface, click-through.
  resizeCanvasToViewport(canvas);
  container.appendChild(canvas);

  // OffscreenCanvas + worker — Plan §15.2.
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

  // Resize listener — the Edge-Glow window can get resized by monitor
  // hotplug. Named handler so teardown can remove it.
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
 * Pushes a cursor point. Coords are PHYSICAL px (Plan §11.2);
 * the worker converts via dpr.
 */
export function pushCursorPoint(physicalX: number, physicalY: number): void {
  if (worker === null) return;
  worker.postMessage({ type: "push", x: physicalX, y: physicalY });
}

/**
 * Clears the trail (e.g. on action_ended).
 */
export function clearCursorTrail(): void {
  if (worker === null) return;
  worker.postMessage({ type: "clear" });
}

/**
 * Test helper: tear-down between tests.
 */
export function teardownCursorTrail(): void {
  // Unregister the resize listener first — otherwise it can still fire
  // while worker/canvas are being nulled out; it won't trigger any
  // action, but it leaks the listener slot (hot-reload accumulation).
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
