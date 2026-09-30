/**
 * Gigi's errands: when Jarvis (the lead) hands an agent a task, Gigi flies
 * over to that agent's figure, delivers the message in a speech bubble, and
 * flies back to its day.
 *
 * The source is the board's own `SocietyMessageSent` event (msg_type ASSIGN,
 * or a direct line from the lead), which the WebSocket already forwards; the
 * errand is pure choreography on top of it — it never starts, stops or
 * changes work. Errands queue, so three tasks in a row are three flights.
 */
import { useEffect, useRef } from "react";
import { create } from "zustand";
import { useEventStore } from "@/store/events";
import type { SocietyAgent } from "../data";
import type { Point } from "./officeLayout";

/** The board types that count as the lead sending an agent something. */
const ERRAND_TYPES = new Set(["ASSIGN", "SAY", "QUERY"]);
const MESSAGE_EVENT = "SocietyMessageSent";
/** How long Gigi hovers next to the agent while the message is read out. */
export const DELIVER_MS = 5_000;
/** An errand still waiting after this long is stale: the moment has passed. */
export const ERRAND_STALE_MS = 60_000;
/** Queue cap — a burst of delegations never becomes minutes of flying. */
export const MAX_QUEUED = 4;
/** Gigi stops this far from the agent, on the side it comes from (metres). */
export const DELIVERY_GAP_M = 0.95;
/** Gigi's errand speed: brisk, faster than anyone walks (m/s). */
export const ERRAND_SPEED = 3.4;
/** Newest events inspected per update, same bound as the island's feed. */
const MAX_CATCHUP = 64;

export interface Errand {
  id: string;
  /** Recipient agent id. */
  to: string;
  /** Recipient's name, for Gigi's "on my way to …" bubble. */
  toName: string;
  /** Bubble-sized preview of what was handed over. */
  text: string;
  msgType: string;
  queuedMs: number;
}

export type ErrandPhase = "fly" | "deliver";

interface ErrandState {
  queue: Errand[];
  current: (Errand & { phase: ErrandPhase; untilMs: number }) | null;
  enqueue: (errand: Errand) => void;
  arrive: (id: string) => void;
  /** Ends the current errand and starts the next one that is still fresh. */
  finish: (id: string) => void;
  reset: () => void;
}

function nextFresh(queue: Errand[], now: number): { current: ErrandState["current"]; queue: Errand[] } {
  const fresh = queue.filter((e) => now - e.queuedMs < ERRAND_STALE_MS);
  const [head, ...rest] = fresh;
  return { current: head ? { ...head, phase: "fly", untilMs: 0 } : null, queue: rest };
}

export const useGigiErrands = create<ErrandState>((set) => ({
  queue: [],
  current: null,
  enqueue: (errand) => set((s) => {
    if (s.current?.id === errand.id || s.queue.some((e) => e.id === errand.id)) return s;
    if (!s.current) return { current: { ...errand, phase: "fly", untilMs: 0 } };
    return { queue: [...s.queue, errand].slice(-MAX_QUEUED) };
  }),
  arrive: (id) => set((s) => (s.current?.id === id && s.current.phase === "fly"
    ? { current: { ...s.current, phase: "deliver", untilMs: Date.now() + DELIVER_MS } } : s)),
  finish: (id) => set((s) => (s.current?.id === id ? nextFresh(s.queue, Date.now()) : s)),
  reset: () => set({ queue: [], current: null }),
}));

/**
 * The errand a board event describes, or null: only the lead sending a live
 * agent (never itself) a task or a direct line counts.
 */
export function errandFrom(payload: unknown, agents: ReadonlyMap<string, Pick<SocietyAgent, "tier" | "name">>, now: number): Errand | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Record<string, unknown>;
  const from = typeof p.from_agent === "string" ? p.from_agent : "";
  const to = typeof p.to_agent === "string" ? p.to_agent : "";
  const msgType = typeof p.msg_type === "string" ? p.msg_type : "";
  if (!ERRAND_TYPES.has(msgType) || !to || to === from || p.room_id) return null;
  if (agents.get(from)?.tier !== "lead" || !agents.has(to)) return null;
  const id = typeof p.event_id === "string" && p.event_id ? p.event_id : `${from}>${to}:${now}`;
  return { id, to, toName: agents.get(to)?.name ?? "", text: typeof p.text === "string" ? p.text : "", msgType, queuedMs: now };
}

/** Where Gigi hovers to deliver: next to the recipient, on the side Gigi comes from. */
export function deliverySpot(recipient: Point, from: Point, gap = DELIVERY_GAP_M): Point {
  const dx = from.x - recipient.x, dz = from.z - recipient.z;
  const d = Math.hypot(dx, dz);
  if (d < 1e-6) return { x: recipient.x + gap, z: recipient.z };
  return { x: recipient.x + (dx / d) * gap, z: recipient.z + (dz / d) * gap };
}

/**
 * Queue an errand for every board line the lead sends while the office is
 * awake. Drains the event log from a cursor (like the island's feed), so a
 * burst of delegations is never half-lost; asleep, it forgets everything.
 */
export function useErrandFeed(agents: ReadonlyMap<string, SocietyAgent>, enabled: boolean): void {
  const roster = useRef(agents);
  roster.current = agents;
  useEffect(() => {
    if (!enabled) { useGigiErrands.getState().reset(); return; }
    let cursor: string | null = useEventStore.getState().events[0]?.id ?? null;
    return useEventStore.subscribe((state) => {
      const log = state.events;
      const top = log[0];
      if (!top || top.id === cursor) return;
      const fresh = [];
      for (let i = 0; i < log.length && i < MAX_CATCHUP; i += 1) {
        if (log[i].id === cursor) break;
        fresh.push(log[i]);
      }
      cursor = top.id;
      const now = Date.now();
      for (let i = fresh.length - 1; i >= 0; i -= 1) {
        if (fresh[i].name !== MESSAGE_EVENT) continue;
        const errand = errandFrom(fresh[i].payload, roster.current, now);
        if (errand) useGigiErrands.getState().enqueue(errand);
      }
    });
  }, [enabled]);
}
