/**
 * Live memory-write receipts from the backend (`MemoryFileWrite`, see
 * `jarvis/memory/write_feedback.py`), for the lead pet's thought bubble.
 *
 * One write arrives as `pending` and then settles as `saved`, `unchanged` or
 * `failed`, both steps sharing `update_id`. "Saved" is only ever shown after a
 * `saved` receipt: a pending write whose receipt never comes (a dropped socket,
 * a crashed backend) goes stale and disappears without claiming anything.
 * Receipts may arrive out of order; a late `pending` for an already settled
 * write is ignored.
 *
 * The reducer and the bubble selector are pure (`memoryWrites.test.ts`).
 */
import { create } from "zustand";

export const MEMORY_WRITE_EVENT = "MemoryFileWrite";

/** A pending write with no receipt after this long is treated as unconfirmed and hidden. */
export const PENDING_STALE_MS = 30_000;
/** How long "Saved to MEMORY.md" stays visible. */
export const SAVED_SHOW_MS = 4_000;
/** How long a failed write stays visible. */
export const FAILED_SHOW_MS = 8_000;
/** Settled ids are remembered this long so a late `pending` cannot revive them. */
const SETTLED_MEMORY_MS = 60_000;
const MAX_WRITES = 32;

const FILE_NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;

export type MemoryWritePhase = "pending" | "saved" | "failed";

export interface MemoryWrite {
  id: string;
  agentId: string;
  /** Bare file name ("MEMORY.md"), or "" while the backend does not know it yet. */
  file: string;
  phase: MemoryWritePhase;
  /** When this phase began (client clock). */
  atMs: number;
}

export interface MemoryWritesState {
  writes: Record<string, MemoryWrite>;
  /** update id → when it settled. */
  settled: Record<string, number>;
}

interface Receipt { id: string; agentId: string; file: string; phase: MemoryWritePhase | "unchanged" }

function parseReceipt(payload: unknown): Receipt | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Record<string, unknown>;
  const id = typeof p.update_id === "string" ? p.update_id : "";
  const agentId = typeof p.agent_id === "string" ? p.agent_id : "";
  const rawFile = typeof p.file === "string" ? p.file : "";
  const phase = p.phase;
  if (!id || !agentId) return null;
  if (phase !== "pending" && phase !== "saved" && phase !== "failed" && phase !== "unchanged") return null;
  return { id, agentId, file: FILE_NAME.test(rawFile) ? rawFile : "", phase };
}

function visibleFor(write: MemoryWrite): number {
  if (write.phase === "pending") return PENDING_STALE_MS;
  return write.phase === "saved" ? SAVED_SHOW_MS : FAILED_SHOW_MS;
}

/** Drops expired writes and old settled ids. */
export function pruneMemoryWrites(state: MemoryWritesState, nowMs: number): MemoryWritesState {
  const writes = Object.fromEntries(
    Object.entries(state.writes).filter(([, w]) => nowMs - w.atMs < visibleFor(w)),
  );
  const settled = Object.fromEntries(
    Object.entries(state.settled).filter(([, at]) => nowMs - at < SETTLED_MEMORY_MS),
  );
  return { writes, settled };
}

/** Applies one `MemoryFileWrite` payload; malformed payloads change nothing. */
export function applyMemoryWrite(state: MemoryWritesState, payload: unknown, nowMs: number): MemoryWritesState {
  const receipt = parseReceipt(payload);
  if (!receipt) return state;
  const base = pruneMemoryWrites(state, nowMs);
  const writes = { ...base.writes };
  const settled = { ...base.settled };
  const known = writes[receipt.id];
  if (receipt.phase === "pending") {
    if (settled[receipt.id] !== undefined || known) return base;
    writes[receipt.id] = { id: receipt.id, agentId: receipt.agentId, file: receipt.file, phase: "pending", atMs: nowMs };
  } else {
    settled[receipt.id] = nowMs;
    if (receipt.phase === "unchanged") delete writes[receipt.id];
    else writes[receipt.id] = {
      id: receipt.id, agentId: receipt.agentId, file: receipt.file || known?.file || "", phase: receipt.phase, atMs: nowMs,
    };
  }
  const ids = Object.keys(writes);
  if (ids.length > MAX_WRITES) {
    ids.sort((a, b) => writes[a].atMs - writes[b].atMs);
    for (const id of ids.slice(0, ids.length - MAX_WRITES)) delete writes[id];
  }
  return { writes, settled };
}

/** What one agent's bubble says about its memory right now. */
export interface MemoryNote {
  phase: MemoryWritePhase;
  /** Distinct file names involved, oldest write first; may be empty. */
  files: string[];
  /** Latest change; the bubble re-renders until it expires. */
  atMs: number;
}

/**
 * Pending writes win (the agent is still writing), then a visible failure —
 * a later success on another file must not hide it — then "saved". Several
 * files in the same phase are listed together, so concurrent writes never
 * hide each other.
 */
export function memoryNoteFor(writes: Record<string, MemoryWrite>, agentId: string, nowMs: number): MemoryNote | null {
  const live = Object.values(writes)
    .filter((w) => w.agentId === agentId && nowMs - w.atMs < visibleFor(w))
    .sort((a, b) => a.atMs - b.atMs);
  if (live.length === 0) return null;
  const phase: MemoryWritePhase = (["pending", "failed", "saved"] as const).find((p) => live.some((w) => w.phase === p)) ?? "saved";
  const group = live.filter((w) => w.phase === phase);
  const files = [...new Set(group.map((w) => w.file).filter(Boolean))];
  return { phase, files, atMs: Math.max(...group.map((w) => w.atMs)) };
}

/** Milliseconds until the next write expires, or null when none is shown. */
export function nextExpiryIn(writes: Record<string, MemoryWrite>, nowMs: number): number | null {
  const left = Object.values(writes).map((w) => w.atMs + visibleFor(w) - nowMs);
  return left.length > 0 ? Math.max(0, Math.min(...left)) : null;
}

interface MemoryWritesStore extends MemoryWritesState {
  receive: (payload: unknown, nowMs?: number) => void;
  reset: () => void;
}

let sweep: ReturnType<typeof setTimeout> | null = null;

export const useMemoryWrites = create<MemoryWritesStore>((set, get) => {
  /** Expired receipts leave the store on their own, so no bubble keeps a stale line or a ticking clock. */
  const schedule = () => {
    if (sweep !== null) clearTimeout(sweep);
    sweep = null;
    const wait = nextExpiryIn(get().writes, Date.now());
    if (wait === null) return;
    sweep = setTimeout(() => {
      sweep = null;
      set((s) => pruneMemoryWrites(s, Date.now()));
      schedule();
    }, wait + 50);
  };
  return {
    writes: {},
    settled: {},
    receive: (payload, nowMs = Date.now()) => {
      set((s) => applyMemoryWrite(s, payload, nowMs));
      schedule();
    },
    reset: () => {
      if (sweep !== null) clearTimeout(sweep);
      sweep = null;
      set({ writes: {}, settled: {} });
    },
  };
});
