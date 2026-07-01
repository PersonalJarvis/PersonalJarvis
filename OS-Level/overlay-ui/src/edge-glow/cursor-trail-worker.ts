// cursor-trail-worker.ts — OffscreenCanvas Worker. Plan §15.2.
//
// Receives:
//   - { type: "init", canvas: OffscreenCanvas, dpr: number }
//   - { type: "resize", width: number, height: number, dpr: number }
//   - { type: "push", x: number, y: number }
//                        // x, y are PHYSICAL px; the worker itself
//                        // converts them to CSS px via dpr.
//   - { type: "clear" }   // on action_ended
//
// Maintains a ring buffer of 20 points and draws every frame with
// fading opacity (Plan §15.1). Points fade out linearly after
// ``POINT_LIFETIME_MS``.

interface InitMsg {
  type: "init";
  canvas: OffscreenCanvas;
  dpr: number;
}
interface ResizeMsg {
  type: "resize";
  width: number;
  height: number;
  dpr: number;
}
interface PushMsg {
  type: "push";
  x: number;
  y: number;
}
interface ClearMsg {
  type: "clear";
}
type WorkerMsg = InitMsg | ResizeMsg | PushMsg | ClearMsg;

const TRAIL_LENGTH = 20; // Plan §15.1
const POINT_RADIUS = 16; // CSS px
const POINT_LIFETIME_MS = 400; // Plan §15.1 — opacity 0.35 -> 0
const MAX_OPACITY = 0.35;

interface TrailPoint {
  x: number; // CSS px
  y: number;
  ts: number; // performance.now() ms
}

let canvas: OffscreenCanvas | null = null;
let ctx: OffscreenCanvasRenderingContext2D | null = null;
let dpr = 1;

// Ring buffer — fixed size, head pointer.
const buf: TrailPoint[] = [];
let head = 0;

let rafHandle = 0;

function pushPoint(physX: number, physY: number): void {
  const cssX = physX / dpr;
  const cssY = physY / dpr;
  const point: TrailPoint = { x: cssX, y: cssY, ts: performance.now() };
  if (buf.length < TRAIL_LENGTH) {
    buf.push(point);
  } else {
    buf[head] = point;
    head = (head + 1) % TRAIL_LENGTH;
  }
  // Self-stopping RAF (Plan §17): if drawFrame stopped itself on the
  // last empty frame, kick it off again now.
  if (rafHandle === 0 && ctx !== null) {
    rafHandle = requestAnimationFrame(drawFrame);
  }
}

function clearTrail(): void {
  buf.length = 0;
  head = 0;
  if (ctx !== null && canvas !== null) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
}

function drawFrame(): void {
  if (ctx === null || canvas === null) return;

  // Self-stopping RAF (Plan §17): pause the RAF loop entirely once the
  // trail is empty — no clearRect GPU round-trip per frame for a blank
  // canvas. Restart happens in pushPoint().
  if (buf.length === 0) {
    cancelAnimationFrame(rafHandle); // defensive — the RAF may have just self-fired
    rafHandle = 0;
    return;
  }

  rafHandle = requestAnimationFrame(drawFrame);
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Plan §15.2: globalCompositeOperation = 'lighter' for luminous
  // accumulation.
  ctx.globalCompositeOperation = "lighter";
  ctx.fillStyle = "#FFC700"; // Plan §7.1 yellow-primary

  const now = performance.now();
  for (const p of buf) {
    const age = now - p.ts;
    if (age >= POINT_LIFETIME_MS) continue;
    const fade = 1 - age / POINT_LIFETIME_MS; // 1 -> 0
    ctx.globalAlpha = MAX_OPACITY * fade;
    ctx.beginPath();
    ctx.arc(p.x * dpr, p.y * dpr, POINT_RADIUS * dpr, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1.0;
}

self.addEventListener("message", (ev: MessageEvent<WorkerMsg>) => {
  const msg = ev.data;
  switch (msg.type) {
    case "init":
      canvas = msg.canvas;
      dpr = msg.dpr;
      ctx = canvas.getContext("2d");
      if (ctx !== null && rafHandle === 0) {
        rafHandle = requestAnimationFrame(drawFrame);
      }
      break;
    case "resize":
      if (canvas !== null) {
        canvas.width = msg.width;
        canvas.height = msg.height;
        dpr = msg.dpr;
      }
      break;
    case "push":
      pushPoint(msg.x, msg.y);
      break;
    case "clear":
      clearTrail();
      break;
    default: {
      // Exhaustiveness check.
      const _exhaustive: never = msg;
      void _exhaustive;
    }
  }
});
