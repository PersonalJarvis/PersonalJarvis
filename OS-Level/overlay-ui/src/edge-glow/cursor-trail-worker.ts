// cursor-trail-worker.ts — OffscreenCanvas Worker. Plan §15.2.
//
// Empfaengt:
//   - { type: "init", canvas: OffscreenCanvas, dpr: number }
//   - { type: "resize", width: number, height: number, dpr: number }
//   - { type: "push", x: number, y: number }
//                        // x, y sind PHYSICAL px; Worker konvertiert
//                        // selbst per dpr in CSS-px.
//   - { type: "clear" }   // bei action_ended
//
// Maintained einen Ring-Buffer von 20 Punkten + zeichnet jeden Frame
// mit fading opacity (Plan §15.1). Punkte verblassen nach
// ``POINT_LIFETIME_MS`` linear.

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

// Ring buffer — feste Groesse, head pointer.
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
  // Self-stopping RAF (Plan §17): falls drawFrame sich beim letzten
  // leeren Frame selbst angehalten hat, jetzt wieder anwerfen.
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

  // Self-stopping RAF (Plan §17): wenn Trail leer ist, pausiere die
  // RAF-Schleife komplett — kein clearRect-GPU-Round-Trip pro Frame
  // fuer Blank-Canvas. Wieder-Start erfolgt in pushPoint().
  if (buf.length === 0) {
    cancelAnimationFrame(rafHandle); // defensive — RAF hat sich u.U. gerade selbst gefired
    rafHandle = 0;
    return;
  }

  rafHandle = requestAnimationFrame(drawFrame);
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Plan §15.2: globalCompositeOperation = 'lighter' fuer luminoses
  // Akkumulieren.
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
