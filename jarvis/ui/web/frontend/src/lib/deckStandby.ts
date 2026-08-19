/**
 * The deck's opening — boot, standby, and the moment the board takes over.
 *
 * Before the first turn the mission deck used to show the full board: nine
 * instruments, most of them saying "nothing yet". The maintainer's verdict
 * (2026-08-18): boring, and worse at the very start, when the app is still
 * coming up. So the deck now has THREE phases and one honest rule for moving
 * between them:
 *
 *   boot     — the link or the voice stack is not up yet. The stage shows
 *              the start sequence: the four gates a voice turn needs (link,
 *              voice, brain, wake), each one lighting up the moment it is
 *              really true, with the time it took.
 *   standby  — everything is up, nobody has spoken. The stage is the big
 *              instrument: the orb in a ring that listens, and the same four
 *              gates as standing readouts.
 *   board    — the mission control board. Reached the moment a turn opens
 *              (a wake word, a hotkey, a typed message, a press on the orb),
 *              or when the person opens it by hand — and then it STAYS: the
 *              phases only ever move forward within a session, because a
 *              stage that flips back and forth is a stage nobody can read.
 *
 * Nothing here is invented: every gate is a store field the header lamps
 * already show, and the durations are measured on this very screen. Pure
 * module — no React, no timers — so deckStandby.test.ts can pin all of it.
 */

export type DeckPhase = "boot" | "standby" | "board";

export type GateId = "link" | "voice" | "brain" | "wake";

/**
 * `pending` — not true yet (still starting, or waiting on the ones before it).
 * `ok`      — true.
 * `off`     — the ones before it are up and this one is honestly not coming:
 *             no brain configured, wake word switched off. Not a failure of
 *             the boot; a fact of the setup, shown as one.
 */
export type GateState = "pending" | "ok" | "off";

export interface Gate {
  id: GateId;
  state: GateState;
}

export const GATE_ORDER: readonly GateId[] = ["link", "voice", "brain", "wake"];

export interface GateInput {
  connected: boolean;
  voiceReady: boolean;
  /** The active brain provider, "" while unknown or unset. */
  brainProvider: string;
  /** null while the wake-word config has not been read; otherwise the switch. */
  wakeEnabled: boolean | null;
  /**
   * True once the boot has been complete (link + voice) for long enough that
   * a brain or wake fact still missing is a real absence, not a fetch that
   * has not landed. Owned by the caller's clock so this stays pure.
   */
  settled: boolean;
}

/**
 * The four gates, in the order the boot lights them.
 *
 * Link and voice are the app's own: they are pending until true, and the
 * boot lasts exactly as long as either is pending. Brain and wake are
 * configuration: pending while the boot is still on or the facts are still
 * arriving, then `ok`, or `off` once the caller says the dust has settled.
 */
export function gatesFor(input: GateInput): Gate[] {
  const bootDone = input.connected && input.voiceReady;
  const brain: GateState = input.brainProvider
    ? "ok"
    : bootDone && input.settled
      ? "off"
      : "pending";
  let wake: GateState = "pending";
  if (bootDone) {
    if (input.wakeEnabled === true) wake = "ok";
    else if (input.wakeEnabled === false) wake = "off";
    else if (input.settled) wake = "off";
  }
  return [
    { id: "link", state: input.connected ? "ok" : "pending" },
    { id: "voice", state: input.voiceReady ? "ok" : "pending" },
    { id: "brain", state: brain },
    { id: "wake", state: wake },
  ];
}

export interface PhaseInput {
  connected: boolean;
  voiceReady: boolean;
  /** The person (or the deck itself) opened the board — sticky for the session. */
  boardOpen: boolean;
  /** Turns opened this session (deck store); 0 before the first. */
  turnIndex: number;
  /** Chat messages in the store — a conversation exists, however it started. */
  messageCount: number;
  /** A voice call is live, being set up, or paused: the person reached for it. */
  voiceEngaged: boolean;
}

/**
 * Which phase the deck shows.
 *
 * Forward only: any sign of a conversation — a turn, a message, a live call,
 * or the explicit "open the board" — is the board, whatever the boot says
 * (a person mid-conversation must never be sent back to a start screen).
 * Without any of that, the boot holds until link and voice are both up, and
 * then the standby holds until somebody speaks.
 */
export function resolvePhase(input: PhaseInput): DeckPhase {
  if (input.boardOpen || input.turnIndex > 0 || input.messageCount > 0 || input.voiceEngaged) {
    return "board";
  }
  return input.connected && input.voiceReady ? "standby" : "boot";
}

/**
 * How long after link + voice are up the boot console stops waiting for the
 * brain and wake facts and calls a missing one absent.
 */
export const SETTLE_MS = 3_000;

/* ------------------------------------------------------------------ */
/* Ring geometry — the big instrument the standby stage draws           */
/* ------------------------------------------------------------------ */

/** A point on a circle, 0° at twelve o'clock, clockwise (screen coordinates). */
export function polar(cx: number, cy: number, r: number, deg: number): [number, number] {
  const rad = ((deg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

/**
 * The SVG path of an arc from `a0` to `a1` degrees (clockwise) at radius `r`.
 * Spans of 180° and more take the large-arc flag; a full circle is the
 * caller's job (draw a circle).
 */
export function arcPath(cx: number, cy: number, r: number, a0: number, a1: number): string {
  const [x0, y0] = polar(cx, cy, r, a0);
  const [x1, y1] = polar(cx, cy, r, a1);
  const large = a1 - a0 > 180 ? 1 : 0;
  return `M ${fmt(x0)} ${fmt(y0)} A ${fmt(r)} ${fmt(r)} 0 ${large} 1 ${fmt(x1)} ${fmt(y1)}`;
}

function fmt(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

/** Where each gate's arc sits on the ring: centred on the compass points. */
export const GATE_ARC_CENTRE: Record<GateId, number> = {
  link: 0,
  voice: 90,
  brain: 180,
  wake: 270,
};

/** Degrees one gate's arc spans — four of them leave a gap at each diagonal. */
export const GATE_ARC_SPAN = 64;

export interface RingTick {
  deg: number;
  /** 0 short, 1 long (every 15°), 2 cardinal (every 90°). */
  weight: 0 | 1 | 2;
}

/** The ring's tick marks: one every `step` degrees, weighted for the eye. */
export function ringTicks(step = 3): RingTick[] {
  const out: RingTick[] = [];
  for (let deg = 0; deg < 360; deg += step) {
    const weight: 0 | 1 | 2 = deg % 90 === 0 ? 2 : deg % 15 === 0 ? 1 : 0;
    out.push({ deg, weight });
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* Sizing                                                               */
/* ------------------------------------------------------------------ */

const RING_MIN = 260;
const RING_MAX = 760;
const RING_PAD = 20;
const RETICLE_MIN = 200;
const RETICLE_MAX = 420;
/** The reticle's share of the ring — the ring must read as a ring around it. */
const RETICLE_OF_RING = 0.6;

/**
 * The ring's diameter for a stage of the given size: as large as the
 * shorter side allows, within bounds. The maximum until the stage has been
 * measured, like `orbSizeFor`.
 */
export function ringSizeFor(width: number, height: number): number {
  if (!width || !height) return RING_MAX;
  const fit = Math.min(width, height) - RING_PAD * 2;
  return Math.max(RING_MIN, Math.min(RING_MAX, Math.floor(fit)));
}

/** The orb reticle inside a ring of the given diameter. */
export function reticleSizeFor(ring: number): number {
  return Math.max(RETICLE_MIN, Math.min(RETICLE_MAX, Math.round(ring * RETICLE_OF_RING)));
}

/* ------------------------------------------------------------------ */
/* The hand-off — the standby's last second and the board's first        */
/* ------------------------------------------------------------------ */

/**
 * The choreography the moment the board takes over, as ONE timeline in
 * seconds from the hand-off (the phase flipping to "board"). The maintainer's
 * verdict on the first cut (2026-08-19): a fade and a wipe read as a hard
 * switch — "ridiculous". The deck is a mission deck; the hand-off has to be
 * a launch. So:
 *
 *   0.00  the orb flares and a shockwave leaves it; the stage flashes gold
 *   0.00  the ring draws breath (shrinks a touch), then bursts past the
 *         camera, turning; its ticks flare clockwise, the sweep spins up
 *   0.12  the orb travels from the ring's centre to its place on the board
 *   0.40  the instruments assemble as the wave passes — centre first, then
 *         outward: a targeting frame draws itself, the card fills in from
 *         the centre's side with a scan bar on the front, and locks with a
 *         flash
 *   0.80  the orb lands: one ring leaves it and dies
 *   0.95  the standby stage is gone
 *   1.25  one scan runs down the whole board — the deck is live
 *
 * Every figure lives here so the stage (DeckStandby), the cards
 * (DeckReveal) and the view (MissionDeckView) keep one clock, and the tests
 * can pin the order without reading three components. The standby's burst
 * itself (flash, flare, waves, the ring's breath-and-burst) is CSS keyed on
 * `data-leaving` — index.css mirrors the flare, wave, echo and ring figures
 * below, so it runs on the compositor whatever the main thread is doing.
 */
export const HANDOFF = {
  /** The orb's flare: a burst of light, gone by the time the wave is out. */
  flareS: 0.6,
  /** The shockwave's travel from the orb to past the stage's edge. */
  waveS: 0.95,
  /** The second, fainter wave follows the first by this much. */
  waveEchoDelayS: 0.14,
  /** The ring: breath in, then burst outward — and how far the breath goes. */
  ringS: 0.62,
  ringBreathAt: 0.22,
  ringBreathScale: 0.95,
  ringBurstScale: 1.42,
  ringBurstRotateDeg: 14,
  /** The corners and the cue leave in this long. */
  cornerS: 0.3,
  /** The orb's travel to the board: starts a beat after the flare, takes this long. */
  travelDelayS: 0.12,
  travelS: 0.72,
  /** The standby layer's final fade starts here, so the burst plays out first. */
  stageFadeDelayS: 0.85,
  stageFadeS: 0.25,
  /** The orb's landing ring on the board. */
  landDelayS: 0.8,
  landS: 0.55,
  /** The board's headline fades in once the orb has landed. */
  headlineDelayS: 1.0,
  /** The final scan down the whole board. */
  boardSweepDelayS: 1.25,
  boardSweepS: 0.5,
} as const;

/**
 * How one board instrument powers on (DeckReveal), relative to its slot's
 * `revealDelayMs`: the targeting frame draws in, the card's content wipes
 * in a beat later, the lock flash fires as the wipe lands, and the frame
 * ghost fades once the real frame underneath is there.
 */
export const CARD_POWER_ON = {
  frameS: 0.34,
  wipeLeadS: 0.16,
  wipeS: 0.42,
  lockS: 0.22,
  ghostFadeDelayS: 0.7,
  ghostFadeS: 0.3,
} as const;

/**
 * The reveal order of the board's cards when the board takes over — the
 * centre first, then outward, as the shockwave from the orb reaches each
 * slot — as a delay in milliseconds for each slot. Slots are named so the
 * view cannot mis-number them.
 */
export type BoardSlot =
  | "centre-top"
  | "centre-bottom"
  | "left-top"
  | "right-top"
  | "left-bottom"
  | "right-bottom";

const REVEAL_BASE_MS = 400;
const REVEAL_STEP_MS = 130;
const REVEAL_ORDER: Record<BoardSlot, number> = {
  "centre-top": 0,
  "centre-bottom": 1,
  "left-top": 2,
  "right-top": 2,
  "left-bottom": 3,
  "right-bottom": 3,
};

export function revealDelayMs(slot: BoardSlot): number {
  return REVEAL_BASE_MS + REVEAL_ORDER[slot] * REVEAL_STEP_MS;
}

/**
 * Which way a slot's content wipes in: away from the orb. The wave comes
 * from the centre, so a card on the left fills from its right edge leftward,
 * a card on the right from its left edge rightward, and the centre column
 * top-down.
 */
export type WipeDirection = "down" | "left" | "right";

export function revealWipeFor(slot: BoardSlot): WipeDirection {
  if (slot.startsWith("left")) return "left";
  if (slot.startsWith("right")) return "right";
  return "down";
}
