/**
 * Which agents are talking right now, and what that asks of their figures —
 * the state half of `conversation.ts`.
 *
 * Two channels, on purpose, exactly like `retireStore.ts`:
 *
 *  - the zustand store carries the conversations themselves (who, which line,
 *    which beat). One changes a few times over a handful of seconds, so a
 *    React render per change costs nothing and the bubbles — which are DOM —
 *    can read it;
 *  - the pose map is a plain mutable `Map`, written and read inside
 *    `useFrame`, exactly like `walkerRegistry.ts`. A walker in a conversation
 *    reads its pose every frame; re-rendering React at 60 fps to turn a head
 *    would be absurd.
 *
 * The pose is deliberately weaker than the retirement ceremony's
 * `WalkerOverride`: it has no position, no pitch, no hidden flag. A
 * conversation may turn a head and open a mouth and nothing else
 * (world-behaviour-manual §1), so the two systems have no territory to fight
 * over — the ceremony owns where a figure is, speech owns where it looks.
 *
 * Every watchdog lives here rather than in the scene: a conversation no stage
 * ever picks up (the viewer is on the ledger, the canvas never mounted, WebGL
 * is gone) must still expire instead of sitting in the store forever.
 */
import { create } from "zustand";

import {
  MAX_LIVE_PAIRS,
  QUEUE_MAX,
  ROOM_IDLE_MS,
  TALK_TIMEOUT_MS,
  bubbleSeconds,
  kindOf,
  pairKey,
  roomKey,
  type ConvoKind,
  type TalkKind,
} from "./conversation";

/** One line somebody said, as the island needs it. */
export interface TalkLine {
  /** The board envelope's id — the dedupe key and the deep link. */
  id: string;
  seq: number;
  speakerId: string;
  text: string;
  kind: TalkKind;
  /** Characters in the original, so the bubble can say there is more. */
  chars: number;
  truncated: boolean;
  ms: number;
}

/** A conversation between two figures, or one figure and somebody off-island. */
export interface Conversation {
  key: string;
  /** The two participants; `partnerId` is "" when the other side has no figure. */
  speakerId: string;
  partnerId: string;
  /** "" unless the line was spoken into a room. */
  roomId: string;
  /** Decided once from the distance at ingest, never re-decided mid-sentence. */
  kind: ConvoKind | null;
  line: TalkLine | null;
  queued: TalkLine[];
  /** Lines that overflowed the queue; the bubble shows "+N" for them. */
  skipped: number;
  startedMs: number;
  lineStartedMs: number;
  /** True while the scene draws the badges/arc; a 4th conversation runs plain. */
  degraded: boolean;
}

/** A bounded room in session at the Town Hall. */
export interface RoomScene {
  roomId: string;
  members: string[];
  topic: string;
  round: number;
  maxRounds: number;
  settled: boolean;
  reason: string;
  startedMs: number;
  touchedMs: number;
}

/** What a conversation asks of a walker. It can never move one. */
export interface SpeechPose {
  /** Heading to turn toward, or null for "keep your own". */
  heading: number | null;
  /** Play the `talk` clip this frame. */
  talking: boolean;
}

/** What arrives from the WebSocket, already narrowed. */
export interface IncomingMessage {
  eventId: string;
  seq: number;
  msgType: string;
  fromAgent: string;
  toAgent: string;
  roomId: string;
  roomRound: number;
  text: string;
  textChars: number;
  truncated: boolean;
}

export interface IncomingRoom {
  roomId: string;
  phase: string;
  members: string[];
  topic: string;
  reason: string;
  maxRounds: number;
}

interface ConvoState {
  talks: Record<string, Conversation>;
  room: RoomScene | null;
  /** Highest board sequence already ingested; anything older is a replay. */
  lastSeq: number;
}

interface ConvoActions {
  noteMessage: (m: IncomingMessage, known: ReadonlySet<string>) => void;
  noteRoom: (r: IncomingRoom) => void;
  /** The line on screen finished: promote the queue, or close the conversation. */
  advance: (key: string) => void;
  closeTalk: (key: string) => void;
  closeRoom: () => void;
  /** Reduced motion, a lost canvas, a section change: forget everything. */
  reset: () => void;
}

/** Per-frame poses, keyed by agent id. Mutable on purpose; see the docstring. */
const poses = new Map<string, SpeechPose>();

/** Watchdogs, one per live key, cleared whenever the key goes. */
const timers = new Map<string, ReturnType<typeof setTimeout>>();

function arm(key: string, ms: number, run: () => void): void {
  disarm(key);
  timers.set(
    key,
    setTimeout(() => {
      timers.delete(key);
      run();
    }, ms),
  );
}

function disarm(key: string): void {
  const handle = timers.get(key);
  if (handle !== undefined) {
    clearTimeout(handle);
    timers.delete(key);
  }
}

function lineFrom(m: IncomingMessage, now: number): TalkLine {
  return {
    id: m.eventId,
    seq: m.seq,
    speakerId: m.fromAgent,
    text: m.text,
    kind: kindOf(m.msgType),
    chars: m.textChars || m.text.length,
    truncated: m.truncated,
    ms: now,
  };
}

export const useConversationStore = create<ConvoState & ConvoActions>((set, get) => ({
  talks: {},
  room: null,
  lastSeq: 0,

  noteMessage: (m, known) => {
    const state = get();
    // A replayed or duplicated frame changes nothing. `seq` is the board's own
    // monotone row number, which is exactly why it rides on the event.
    if (m.seq > 0 && m.seq <= state.lastSeq) return;

    const speakerKnown = known.has(m.fromAgent);
    const partnerKnown = m.toAgent !== "" && known.has(m.toAgent);
    // Nobody on the island said this and nobody on the island heard it — the
    // board's own plumbing talking to itself. There is nothing to draw.
    if (!speakerKnown && !partnerKnown) {
      if (m.seq > 0) set({ lastSeq: m.seq });
      return;
    }

    const now = Date.now();
    const line = lineFrom(m, now);
    const key = m.roomId ? roomKey(m.roomId) : pairKey(m.fromAgent, m.toAgent || m.fromAgent);
    const talks = { ...state.talks };
    const live = talks[key];

    if (live) {
      // A reply does not restart the conversation: it swaps the roles and the
      // two keep standing there. Four disconnected pops would read as noise.
      if (live.line === null) {
        talks[key] = { ...live, line, lineStartedMs: now, speakerId: line.speakerId };
      } else if (live.queued.length < QUEUE_MAX) {
        talks[key] = { ...live, queued: [...live.queued, line] };
      } else {
        talks[key] = { ...live, skipped: live.skipped + 1 };
      }
    } else {
      const partnerId = partnerKnown ? m.toAgent : "";
      const fresh: Conversation = {
        key,
        speakerId: m.fromAgent,
        partnerId,
        roomId: m.roomId,
        kind: null, // the scene decides from the live distance, once
        line,
        queued: [],
        skipped: 0,
        startedMs: now,
        lineStartedMs: now,
        degraded: false,
      };
      talks[key] = fresh;
      // Oldest out first when the board floods: the island is not a transcript.
      const keys = Object.keys(talks);
      if (keys.length > MAX_LIVE_PAIRS) {
        keys
          .sort((a, b) => talks[a].startedMs - talks[b].startedMs)
          .slice(0, keys.length - MAX_LIVE_PAIRS)
          .forEach((old) => {
            disarm(old);
            poses.delete(talks[old].speakerId);
            if (talks[old].partnerId) poses.delete(talks[old].partnerId);
            delete talks[old];
          });
      }
    }

    arm(key, TALK_TIMEOUT_MS, () => get().closeTalk(key));
    set({ talks, lastSeq: m.seq > 0 ? m.seq : state.lastSeq });

    if (m.roomId && get().room?.roomId === m.roomId) {
      const room = get().room;
      if (room) set({ room: { ...room, round: m.roomRound || room.round, touchedMs: now } });
    }
  },

  noteRoom: (r) => {
    const now = Date.now();
    if (r.phase === "open") {
      arm("room", ROOM_IDLE_MS, () => get().closeRoom());
      set({
        room: {
          roomId: r.roomId,
          members: r.members,
          topic: r.topic,
          round: 1,
          maxRounds: r.maxRounds || 3,
          settled: false,
          reason: "",
          startedMs: now,
          touchedMs: now,
        },
      });
      return;
    }
    const room = get().room;
    if (!room || room.roomId !== r.roomId) return;
    disarm("room");
    set({ room: { ...room, settled: true, reason: r.reason, touchedMs: now } });
  },

  advance: (key) => {
    const talks = { ...get().talks };
    const live = talks[key];
    if (!live) return;
    const [next, ...rest] = live.queued;
    if (next) {
      talks[key] = {
        ...live,
        line: next,
        queued: rest,
        speakerId: next.speakerId,
        lineStartedMs: Date.now(),
      };
      arm(key, TALK_TIMEOUT_MS, () => get().closeTalk(key));
      set({ talks });
      return;
    }
    talks[key] = { ...live, line: null };
    set({ talks });
  },

  closeTalk: (key) => {
    disarm(key);
    const talks = { ...get().talks };
    const live = talks[key];
    if (!live) return;
    poses.delete(live.speakerId);
    if (live.partnerId) poses.delete(live.partnerId);
    delete talks[key];
    set({ talks });
  },

  closeRoom: () => {
    disarm("room");
    const room = get().room;
    if (room) for (const id of room.members) poses.delete(id);
    set({ room: null });
  },

  reset: () => {
    for (const key of [...timers.keys()]) disarm(key);
    poses.clear();
    set({ talks: {}, room: null });
  },
}));

// --------------------------------------------------------------- the poses

/** The pose a conversation wants this walker in, or null when it is free. */
export function speechPose(agentId: string): SpeechPose | null {
  return poses.get(agentId) ?? null;
}

/** Written by the scene each frame for the figures it drives. */
export function setSpeechPose(agentId: string, pose: SpeechPose): void {
  poses.set(agentId, pose);
}

/** Hand one figure back to its own sim. */
export function clearSpeechPose(agentId: string): void {
  poses.delete(agentId);
}

/** Scene unmount, a lost WebGL context, reduced motion: everybody is free. */
export function clearSpeechPoses(): void {
  poses.clear();
}

// ------------------------------------------------------------- convenience

/** How long the line currently on `talk` should stay up, in ms. */
export function lineLifeMs(talk: Conversation): number {
  return talk.line ? bubbleSeconds(talk.line.chars) * 1000 : 0;
}

/** Live conversations, oldest first — the order that decides who wins a heading. */
export function orderedTalks(talks: Record<string, Conversation>): Conversation[] {
  return Object.values(talks).sort((a, b) => a.startedMs - b.startedMs);
}
