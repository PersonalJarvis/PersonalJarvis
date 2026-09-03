/**
 * Retiring an agent, as a piece of choreography — the pure half.
 *
 * Deleting an agent is not a row disappearing from a table. On the island it
 * is an execution: the lead walks up to the condemned figure, raises a rifle,
 * fires, and the body is carried off by two bearers and tipped into the mine.
 * Nothing about it is subtle, and that is the point — an agent you delete is
 * gone, and the world should make you watch it happen.
 *
 * Everything here is a pure function of a phase and a local time, so the
 * whole ceremony can be pinned by `retirement.test.ts` without a Canvas. The
 * R3F side (`RetirementScene.tsx`) owns the meshes and the clock; the store
 * (`retireStore.ts`) owns which agent is being retired and hands out the
 * per-frame poses the walkers read.
 *
 * Two phases are NOT timed here, because their length is a distance:
 * `march` (the lead walking to the condemned) and `bearers` / `arrive` (the
 * stretcher crew closing on their target) run until they get there. The
 * timed phases below are the beats in between.
 */

/** The beats of the ceremony, in order. */
export type RetirePhase =
  | "march" // the lead walks to the condemned (distance, not time)
  | "confront" // the two stand face to face
  | "raise" // the rifle comes up
  | "hold" // the aim settles
  | "shot" // the muzzle flash
  | "fall" // the body topples
  | "settle" // the lead lowers the rifle, the body lies still
  | "bearers" // two bearers trot in from the mine road (distance)
  | "load" // the body is rolled onto the stretcher
  | "carry" // the crew sets off, the camera goes with them
  | "travel" // the cut: the camera flies ahead, the crew is further along
  | "arrive" // the last stretch to the mine's portal (distance)
  | "toss" // the wind-up and the throw into the dark
  | "depart" // the crew walks into the tunnel
  | "done";

/** How long each timed phase lasts, in seconds. Distance phases hold 0. */
export const PHASE_SECONDS: Record<RetirePhase, number> = {
  march: 0,
  confront: 1.0,
  raise: 0.75,
  hold: 0.6,
  shot: 0.22,
  fall: 1.0,
  settle: 0.8,
  bearers: 0,
  load: 1.4,
  carry: 3.0,
  travel: 1.3,
  arrive: 0,
  toss: 1.75,
  depart: 1.4,
  done: 0,
};

/** The order the ceremony runs in. */
export const PHASE_ORDER: readonly RetirePhase[] = [
  "march",
  "confront",
  "raise",
  "hold",
  "shot",
  "fall",
  "settle",
  "bearers",
  "load",
  "carry",
  "travel",
  "arrive",
  "toss",
  "depart",
  "done",
];

/** True while the phase ends on arrival rather than on a clock. */
export function isDistancePhase(phase: RetirePhase): boolean {
  return phase === "march" || phase === "bearers" || phase === "arrive";
}

/** The phase after `phase`; "done" is its own successor. */
export function nextPhase(phase: RetirePhase): RetirePhase {
  const i = PHASE_ORDER.indexOf(phase);
  if (i < 0 || i >= PHASE_ORDER.length - 1) return "done";
  return PHASE_ORDER[i + 1];
}

// ---------------------------------------------------------------------------
// distances and speeds
// ---------------------------------------------------------------------------

/** How far the lead stops from the condemned, in metres. Close enough to be personal. */
export const EXECUTION_RANGE_M = 2.6;
/** The bearers trot; they are not on a stroll. */
export const BEARER_MPS = 3.1;
/**
 * The lead walks on from this far out. The island is 500 m across and the
 * condemned figure can be anywhere on it — a full walk from the square to the
 * summit is a minute of watching a figure cross a map. So the ceremony opens
 * with the lead already most of the way there, on its own route, and the
 * camera holds the condemned while it comes into frame. Same cut as `travel`,
 * at the other end of the story.
 */
export const MARCH_APPROACH_M = 14;
/** Where the bearers come in from: this far back along the route to the mine. */
export const BEARER_ENTRY_M = 18;
/** After the cut, the crew stands this far from the mine's portal. */
export const MINE_APPROACH_M = 15;
/**
 * The bearers stop this far from the portal's mouth and throw from there.
 * Tuned so their stand point lands on the middle of a walkable forecourt tile:
 * the rail head right in front of the portal blocks pathing (islandLayout).
 */
export const TOSS_STANDOFF_M = 5.3;
/** How high the body flies on its way into the dark, in metres. */
export const TOSS_APEX_M = 2.4;
/**
 * Half the distance between the two bearers along the stretcher, in metres.
 * A walker is drawn at hero scale (WORLD_HERO_SCALE), so a body on the deck is
 * closer to 2.8 m than to 1.75 m. The bearers stand clear of it, or the figure
 * lying between them covers them both.
 */
export const STRETCHER_HALF_M = 1.95;
/** The stretcher rides this high while it is being carried, in metres. */
export const STRETCHER_CARRY_Y = 0.95;

// ---------------------------------------------------------------------------
// curves
// ---------------------------------------------------------------------------

export function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/** Smoothstep — the house ease for anything that starts and stops. */
export function ease(u: number): number {
  const t = clamp01(u);
  return t * t * (3 - 2 * t);
}

/**
 * How far the rifle is up, 0 (slung at the hip) to 1 (levelled at the head).
 * It rises over `raise`, stays up through the shot and the fall, and comes
 * down again over `settle`.
 */
export function aimBlend(phase: RetirePhase, local: number): number {
  switch (phase) {
    case "raise":
      return ease(local / PHASE_SECONDS.raise);
    case "hold":
    case "shot":
    case "fall":
      return 1;
    case "settle":
      return 1 - ease(local / PHASE_SECONDS.settle);
    default:
      return 0;
  }
}

/** True while the lead is carrying the rifle at all (it is not always drawn). */
export function riflePhase(phase: RetirePhase): boolean {
  return (
    phase === "confront" ||
    phase === "raise" ||
    phase === "hold" ||
    phase === "shot" ||
    phase === "fall" ||
    phase === "settle"
  );
}

/** Muzzle flash brightness, 0..1 — a hard spike that dies inside the shot. */
export function muzzleFlash(phase: RetirePhase, local: number): number {
  if (phase !== "shot") return 0;
  const u = clamp01(local / PHASE_SECONDS.shot);
  // Full at the trigger, gone a fifth of a second later.
  return (1 - u) ** 2;
}

/** How far the lead is shoved back by the recoil, in metres. */
export function recoilM(phase: RetirePhase, local: number): number {
  if (phase !== "shot") return 0;
  const u = clamp01(local / PHASE_SECONDS.shot);
  return Math.sin(u * Math.PI) * 0.12;
}

/**
 * The condemned body's pitch, in radians. It faces its executioner, so it
 * goes over BACKWARDS: negative rotation about local X lays the head down
 * behind the feet. It accelerates like a toppling plank and settles with two
 * small bounces.
 */
export function fallPitch(u: number): number {
  const t = clamp01(u);
  const HIT = 0.78;
  if (t <= 0) return 0;
  if (t < HIT) {
    const k = t / HIT;
    return -(Math.PI / 2) * k * k;
  }
  const over = (t - HIT) / (1 - HIT);
  const bounce = Math.abs(Math.sin(over * Math.PI * 2)) * 0.18 * (1 - over);
  return -Math.PI / 2 + bounce;
}

/** How far the body slides back as it goes down, in metres along its own −Z. */
export function fallSlide(u: number): number {
  return ease(u) * 0.55;
}

/** How high the body's origin sits once it is down: lying on its side, not standing. */
export function fallLift(u: number): number {
  return ease(u) * 0.18;
}

/**
 * The body being rolled onto the stretcher: 0 on the ground, 1 on the deck.
 * It lifts late, so the bearers are visibly bent over it first.
 */
export function loadBlend(local: number): number {
  return ease(clamp01((local - 0.45) / (PHASE_SECONDS.load - 0.45)));
}

/** The stretcher's sway while it is carried — a slow roll, not a bounce. */
export function carrySway(t: number): { roll: number; lift: number } {
  return { roll: Math.sin(t * 4.4) * 0.055, lift: Math.abs(Math.sin(t * 4.4)) * 0.04 };
}

/**
 * The throw, as one number: 0 through the wind-up, then 0→1 over the flight.
 * `toss` is one phase so the wind-up and the release read as one motion.
 */
export const TOSS_WIND_S = 0.9;

export function tossProgress(local: number): { wind: number; fly: number; released: boolean } {
  if (local < TOSS_WIND_S) {
    // Two swings back and forth before the release.
    return { wind: Math.sin((local / TOSS_WIND_S) * Math.PI * 2) * 0.5 + 0.5, fly: 0, released: false };
  }
  const fly = clamp01((local - TOSS_WIND_S) / (PHASE_SECONDS.toss - TOSS_WIND_S));
  return { wind: 1, fly, released: true };
}

/** A ballistic arc from `from` to `to` peaking `apex` metres above the straight line. */
export function tossArc(
  u: number,
  from: readonly [number, number, number],
  to: readonly [number, number, number],
  apex: number,
): [number, number, number] {
  const t = clamp01(u);
  return [
    from[0] + (to[0] - from[0]) * t,
    from[1] + (to[1] - from[1]) * t + Math.sin(Math.PI * t) * apex,
    from[2] + (to[2] - from[2]) * t,
  ];
}

/** How much of the body is still visible as it drops into the dark, 1 → 0. */
export function tossFade(fly: number): number {
  // It only disappears over the last third: the mouth of the tunnel swallows it.
  return 1 - ease(clamp01((fly - 0.66) / 0.34));
}

// ---------------------------------------------------------------------------
// placement
// ---------------------------------------------------------------------------

/**
 * Where the lead stands to fire: `EXECUTION_RANGE_M` in front of the
 * condemned, on the line between them, and the heading that puts them
 * face to face. A figure faces +Z in its own space, hence atan2(dx, dz).
 */
export function firingStand(
  target: readonly [number, number],
  from: readonly [number, number],
): { x: number; z: number; heading: number } {
  const dx = from[0] - target[0];
  const dz = from[1] - target[1];
  const len = Math.hypot(dx, dz);
  // Degenerate (standing on the same tile): back off along +X, any side will do.
  const ux = len > 1e-3 ? dx / len : 1;
  const uz = len > 1e-3 ? dz / len : 0;
  return {
    x: target[0] + ux * EXECUTION_RANGE_M,
    z: target[1] + uz * EXECUTION_RANGE_M,
    heading: Math.atan2(-ux, -uz),
  };
}

/** The heading that turns the condemned to look straight at its executioner. */
export function facingHeading(
  self: readonly [number, number],
  other: readonly [number, number],
): number {
  return Math.atan2(other[0] - self[0], other[1] - self[1]);
}

/**
 * Walk `distanceM` back from the END of a polyline, and return the point plus
 * the heading a figure walking the line forwards would have there. Used for
 * both "where do the bearers come in from" and "where does the cut drop them".
 * A line shorter than the distance yields its first point.
 */
export function pointBackFrom(
  path: ReadonlyArray<readonly [number, number]>,
  distanceM: number,
): { x: number; z: number; heading: number; index: number } {
  if (path.length === 0) return { x: 0, z: 0, heading: 0, index: 0 };
  if (path.length === 1) return { x: path[0][0], z: path[0][1], heading: 0, index: 0 };
  let left = Math.max(0, distanceM);
  for (let i = path.length - 1; i > 0; i--) {
    const [ax, az] = path[i - 1];
    const [bx, bz] = path[i];
    const seg = Math.hypot(bx - ax, bz - az);
    if (seg <= 1e-6) continue;
    if (seg >= left) {
      const t = left / seg;
      return {
        x: bx + (ax - bx) * t,
        z: bz + (az - bz) * t,
        heading: Math.atan2(bx - ax, bz - az),
        index: i,
      };
    }
    left -= seg;
  }
  const [x0, z0] = path[0];
  const [x1, z1] = path[1];
  return { x: x0, z: z0, heading: Math.atan2(x1 - x0, z1 - z0), index: 1 };
}

/** Total length of a polyline in metres. */
export function pathLength(path: ReadonlyArray<readonly [number, number]>): number {
  let sum = 0;
  for (let i = 1; i < path.length; i++) {
    sum += Math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1]);
  }
  return sum;
}

/**
 * The two bearers' stand points: one ahead of the stretcher's centre, one
 * behind, along the heading it is being carried on.
 */
export function bearerSlots(
  centre: readonly [number, number],
  heading: number,
): [[number, number], [number, number]] {
  const fx = Math.sin(heading) * STRETCHER_HALF_M;
  const fz = Math.cos(heading) * STRETCHER_HALF_M;
  return [
    [centre[0] + fx, centre[1] + fz],
    [centre[0] - fx, centre[1] - fz],
  ];
}
