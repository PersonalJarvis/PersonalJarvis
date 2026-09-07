/**
 * Two agents talking, as numbers — the pure half.
 *
 * A message on the board is not a row in a log. On the island it is either a
 * conversation (two figures close enough to turn to each other and speak) or a
 * call (a signal arcing across the map between two shops that are nowhere near
 * one another). Which one it is depends on nothing but the distance between
 * their feet, and the distance is free: every walker writes its position into
 * `walkerRegistry.ts` each frame.
 *
 * Everything here is a pure function of a phase and a local time, so the whole
 * choreography can be pinned by `conversation.test.ts` without a Canvas — the
 * shape `walkerKinematics.ts` and `retirement.ts` already use. The R3F side
 * (`ConversationScene.tsx`) owns the meshes and the clock; the store
 * (`conversationStore.ts`) owns which conversations are live and hands out the
 * per-frame poses the walkers read.
 *
 * The one rule that outranks every number below: **nothing an agent says moves
 * it** (`docs/agent-society/world-behaviour-manual.md` §1). A conversation may
 * turn a head and open a mouth. It may never take a step.
 */

/** What kind of line it was — the bubble is tinted and labelled by this. */
export type TalkKind = "say" | "query" | "answer" | "propose";

/** Close enough to speak, or far enough to need a signal. */
export type ConvoKind = "talk" | "call";

/** The beats of a near conversation. */
export type TalkPhase =
  | "turn" // both heads come round
  | "speak" // the bubble is up, the speaker plays `talk`
  | "hold" // the bubble is gone, the facing lingers so a reply can land
  | "done";

/** The beats of a call. */
export type CallPhase =
  | "dial" // handset and antenna pop, the ground pips light
  | "arc" // the signal travels the bezier
  | "speak" // the bubble is up at the receiving end
  | "hangup" // the arc runs off, the badges fade
  | "done";

/** The beats of a bounded room at the Town Hall. */
export type RoomPhase =
  | "opening" // the ring scales in under the table
  | "live" // ends on an event, not a clock
  | "settling" // the banner says why it broke up
  | "closing"
  | "done";

// ---------------------------------------------------------------------------
// phase clocks
// ---------------------------------------------------------------------------

/** `speak` is 0 here because its length is the text (see `bubbleSeconds`). */
export const TALK_SECONDS: Record<TalkPhase, number> = {
  turn: 0.45,
  speak: 0,
  hold: 0.6,
  done: 0,
};

export const CALL_SECONDS: Record<CallPhase, number> = {
  dial: 0.35,
  arc: 0.75,
  speak: 0,
  hangup: 0.7,
  done: 0,
};

/** `live` is 0 because the backend ends it, not a timer. */
export const ROOM_SECONDS: Record<RoomPhase, number> = {
  opening: 0.5,
  live: 0,
  settling: 1.6,
  closing: 0.8,
  done: 0,
};

export const TALK_ORDER: readonly TalkPhase[] = ["turn", "speak", "hold", "done"];
export const CALL_ORDER: readonly CallPhase[] = ["dial", "arc", "speak", "hangup", "done"];
export const ROOM_ORDER: readonly RoomPhase[] = [
  "opening",
  "live",
  "settling",
  "closing",
  "done",
];

function step<T extends string>(order: readonly T[], phase: T): T {
  const i = order.indexOf(phase);
  if (i < 0 || i >= order.length - 1) return order[order.length - 1];
  return order[i + 1];
}

export function nextTalkPhase(phase: TalkPhase): TalkPhase {
  return step(TALK_ORDER, phase);
}
export function nextCallPhase(phase: CallPhase): CallPhase {
  return step(CALL_ORDER, phase);
}
export function nextRoomPhase(phase: RoomPhase): RoomPhase {
  return step(ROOM_ORDER, phase);
}

/** True while the phase waits for an event rather than a clock. */
export function isOpenPhase(phase: TalkPhase | CallPhase | RoomPhase): boolean {
  return phase === "speak" || phase === "live";
}

// ---------------------------------------------------------------------------
// near or far
// ---------------------------------------------------------------------------

/**
 * Close enough to talk across, in metres — seven tiles at `TILE_M = 2`.
 *
 * Not a guess. Two figures standing at the SAME place are `lateralOffset`
 * apart (±0.3 m) plus a tile or two of wander, so under ~4 m. The two closest
 * DISTINCT hubs on the island (the Signal Office and the Desktop hall, mirrored
 * halves of the same block template) stand 28 m apart. 14 m is exactly halfway:
 * no pair of different shops can ever read as "near", and no pair at one shop
 * can ever read as "far".
 */
export const NEAR_RADIUS_M = 14;

/**
 * A running talk only gives up its bubbles past this. The 6 m band is pure
 * hysteresis: a wandering figure at the boundary must not flip a live
 * conversation into a call mid-sentence.
 */
export const NEAR_EXIT_M = 20;

/**
 * Closer than this and the vector between two figures is too short to derive a
 * heading from — it jitters, and the two would turn into each other. Below it,
 * they face along the building's front instead (`tangentFacing`).
 */
export const MIN_FACE_M = 0.9;

/** Which choreography a fresh conversation gets. Decided once, at ingest. */
export function classify(distanceM: number): ConvoKind {
  return distanceM < NEAR_RADIUS_M ? "talk" : "call";
}

/** True when a live talk has drifted far enough to drop its facing. */
export function driftedApart(distanceM: number): boolean {
  return distanceM > NEAR_EXIT_M;
}

// ---------------------------------------------------------------------------
// how long a line is up
// ---------------------------------------------------------------------------

export const BUBBLE_MIN_S = 2.2;
export const BUBBLE_MAX_S = 7.0;
export const BUBBLE_BASE_S = 1.6;
/** Characters per second of reading time — a comfortable island pace. */
export const BUBBLE_CHARS_PER_S = 22;

/** How long a bubble of `chars` characters stays up, in seconds. */
export function bubbleSeconds(chars: number): number {
  const raw = BUBBLE_BASE_S + Math.max(0, chars) / BUBBLE_CHARS_PER_S;
  return Math.min(BUBBLE_MAX_S, Math.max(BUBBLE_MIN_S, raw));
}

// ---------------------------------------------------------------------------
// identity
// ---------------------------------------------------------------------------

/** One conversation per pair, whichever way round the message went. */
export function pairKey(a: string, b: string): string {
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}

/** The key a room's scene is stored under. */
export function roomKey(roomId: string): string {
  return `room:${roomId}`;
}

// ---------------------------------------------------------------------------
// facing
// ---------------------------------------------------------------------------

/**
 * The heading that turns `self` to look straight at `other`.
 *
 * A figure faces +Z in its own space (character-pipeline §4.1), which is why
 * this is `atan2(dx, dz)` and not the usual `atan2(dz, dx)`. Pinned against the
 * real `headingFor` in the test — a figure facing backwards is the single most
 * common way to get this wrong.
 */
export function facingBetween(ax: number, az: number, bx: number, bz: number): number {
  return Math.atan2(bx - ax, bz - az);
}

/**
 * The same-place case: two figures on one stand tile turn along the building's
 * front instead of into each other. `side` is −1 or +1, taken from the sign of
 * their lateral offset, so the pair always turns to face across, never through.
 */
export function tangentFacing(placeFacing: number, side: -1 | 1): number {
  return placeFacing + (side * Math.PI) / 2;
}

// ---------------------------------------------------------------------------
// the arc
// ---------------------------------------------------------------------------

/** Head height in metres, where a call attaches (a figure at WORLD_HERO_SCALE). */
export const HEAD_Y_M = 2.7;

/** How many beads make the signal. */
export const ARC_BEADS = 22;
/** Radius of one bead at the closest zoom step, in metres. */
export const ARC_BEAD_R_M = 0.16;

/**
 * How big a bead has to be to stay visible, given how much island the frame
 * shows. A call is the one thing on the island drawn ACROSS the map, so it is
 * usually watched zoomed out — and at 256 m across, a 0.16 m bead is a pixel
 * and a half. This keeps every bead about five pixels wherever the camera is.
 */
export function beadRadiusFor(visibleWidthM: number): number {
  return Math.min(3.4, Math.max(0.45, visibleWidthM * 0.013));
}

/**
 * How high the arc rises over the straight line, in metres.
 *
 * Clamped so it clears the island without a single terrain query: the summit
 * sits at 12.6 m and the town at 1.8 m, so the 22 m ceiling passes over the
 * mountain by a comfortable margin even on the longest call.
 */
export function arcApexFor(distanceM: number): number {
  return Math.min(22, Math.max(5, 3 + 0.16 * distanceM));
}

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

/** A quadratic arc from `a` to `b`, peaking `apex` metres above the midpoint. */
export function arcPoint(a: Vec3, b: Vec3, t: number, apex: number): Vec3 {
  const u = clamp01(t);
  return {
    x: a.x + (b.x - a.x) * u,
    y: a.y + (b.y - a.y) * u + Math.sin(Math.PI * u) * apex,
    z: a.z + (b.z - a.z) * u,
  };
}

/**
 * How big bead `index` is right now, 0 to 1.
 *
 * Scale, never opacity: `worldMaterials.halo()` caches one material per
 * `colour@opacity` pair, so animating opacity would grow that cache without
 * bound. A travelling head of light plus a short tail, and every bead behind
 * the head sits at a low resting glow so the line reads as a connection rather
 * than a lone dot.
 */
export function beadScale(index: number, count: number, t: number): number {
  if (count <= 1) return t > 0 ? 1 : 0;
  const at = index / (count - 1);
  const head = clamp01(t);
  if (at > head) return 0;
  const behind = head - at;
  const pulse = behind < 0.22 ? 1 - (behind / 0.22) * 0.45 : 0.55;
  // The far end fades in over the last stretch so the signal "lands".
  return pulse * (0.55 + 0.45 * ease(clamp01((head - at) * 6)));
}

/** How far the arc has run off after hang-up, 0 (whole) to 1 (gone). */
export function hangupSweep(local: number): number {
  return ease(clamp01(local / CALL_SECONDS.hangup));
}

/** True while bead `index` still exists during the hang-up sweep. */
export function beadAlive(index: number, count: number, sweep: number): boolean {
  if (count <= 1) return sweep < 1;
  return index / (count - 1) > sweep;
}

// ---------------------------------------------------------------------------
// small curves
// ---------------------------------------------------------------------------

export function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/** Smoothstep — the house ease for anything that starts and stops. */
export function ease(v: number): number {
  const t = clamp01(v);
  return t * t * (3 - 2 * t);
}

/** A ping ring expanding from a head, 0 to 1 over `dial`. */
export function pingScale(local: number): number {
  return ease(clamp01(local / CALL_SECONDS.dial));
}

/** How bright that ring still is: full at the start, gone at the edge. */
export function pingFade(local: number): number {
  return 1 - clamp01(local / CALL_SECONDS.dial);
}

/** A ground pip breathing under a figure that is in a conversation. */
export function pipScale(t: number): number {
  return 1 + Math.sin(t * 3.1) * 0.09;
}

// ---------------------------------------------------------------------------
// budget
// ---------------------------------------------------------------------------

/** Full choreographies drawn at once; a fifth pair gets the clip and a bubble only. */
export const MAX_CONCURRENT = 3;
/** Conversations kept in the store at all — a runaway backend cannot grow it. */
export const MAX_LIVE_PAIRS = 8;
/** Lines held behind the one on screen; past this they collapse into a digest. */
export const QUEUE_MAX = 3;
/** A walker that has not appeared yet gets this long before we give up on it. */
export const PIN_GRACE_MS = 1_500;
/** A conversation nothing has advanced in this long is closed. */
export const TALK_TIMEOUT_MS = 20_000;
/**
 * A call stays on screen at least this long, whatever the sentence does.
 * A one-word answer would otherwise put a signal across the whole island and
 * take it away again inside a second — the viewer would see a flicker and not
 * know what it was.
 */
export const CALL_MIN_S = 3.2;
/** A room nobody has spoken in for this long is closed. */
export const ROOM_IDLE_MS = 45_000;
/** Radius of the ring drawn under a room at the Town Hall, in metres. */
export const ROOM_RING_R_M = 4.5;
/** Radius of the pip under a figure in a conversation, in metres. */
export const PIP_R_M = 1.2;

/**
 * The board types the island reacts to. Pinned against the Python
 * `WorldFeed.VISIBLE_TYPES | ROOM_TYPES` by `test_world_feed.py`, so a type
 * added on one side and forgotten on the other fails the build rather than
 * silently going undrawn.
 */
export const WORLD_MSG_TYPES: readonly string[] = [
  "SAY",
  "QUERY",
  "ANSWER",
  "PROPOSE",
  "ROOM_OPEN",
  "ROOM_SETTLE",
];

/** `MsgType` on the wire → the bubble's kind. */
export function kindOf(msgType: string): TalkKind {
  switch (msgType) {
    case "QUERY":
      return "query";
    case "ANSWER":
      return "answer";
    case "PROPOSE":
      return "propose";
    default:
      return "say";
  }
}
