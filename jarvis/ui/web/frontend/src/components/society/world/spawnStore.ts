/**
 * New agents walk out of the Agent Foundry — the client half of it.
 *
 * The island is a projection of the society log (MASTERPLAN §2.7): the backend
 * owns WHERE an agent is, never how it got there. A brand-new agent therefore
 * does not get a new checkpoint from the backend; it gets a one-off entrance,
 * the same way footsteps are one-off and never synced. This store is the whole
 * memory of that entrance:
 *
 *  - `announceSpawn` is called the moment an agent is created in THIS window,
 *    so its walker knows to come out of the portal instead of appearing on the
 *    plaza;
 *  - a row created in another window (or another device) still gets its
 *    entrance, because a `createdMs` younger than `FRESH_ENTRANCE_MS` counts;
 *  - `sessionStorage` remembers the ids this window has already decided about,
 *    so the roster's 30 s refetch — or a reload — never marches the same figure
 *    out of the factory twice;
 *  - but a walker that is REMOUNTED mid-entrance keeps it. The canvas is torn
 *    down and rebuilt on a WebGL context loss (AP-32), and React can remount a
 *    subtree at any time; a claim that burned on the first mount would leave
 *    the figure standing on the plaza with the portal still flaring. So a
 *    claimed entrance stays claimable for `ENTRANCE_REPLAY_MS`, which is
 *    longer than the walk and shorter than anyone's next visit.
 *
 * `portalOpenedMs` is what the building itself listens to: the portal flares
 * and the forecourt lights up for a few seconds after any entrance.
 *
 * `focusRequestedMs` is the other half of "you made this one": when the agent
 * was created in THIS window, the island swings its camera to the foundry so
 * the maker actually watches the figure come out, instead of it happening on a
 * mountain they are not looking at. It expires — a request nobody consumed
 * (the world was not mounted, the viewer was reading the ledger) must not
 * yank the camera minutes later.
 */
import { create } from "zustand";

/** A row created less than this ago is treated as newly born (ms). */
export const FRESH_ENTRANCE_MS = 60_000;
/** How long the portal keeps glowing after a figure came through (ms). */
export const PORTAL_FLARE_MS = 4_200;
/** A camera request older than this is stale and is ignored. */
export const FOCUS_GRACE_MS = 20_000;
/**
 * A claimed entrance stays claimable this long, so a remount of the walker
 * (a rebuilt canvas, a re-rendered subtree) resumes the walk instead of
 * dropping the figure on the plaza. Longer than the ~6 s ride down the belt.
 */
export const ENTRANCE_REPLAY_MS = 15_000;

const SEEN_KEY = "jarvis.world.foundry.seen.v2";

/** Decisions this window has made: agent id → when it walked, or 0 for never. */
type Decisions = Map<string, number>;

function readSeen(): Decisions {
  try {
    const raw = sessionStorage.getItem(SEEN_KEY);
    if (!raw) return new Map();
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return new Map();
    const rows = parsed.filter(
      (v): v is [string, number] =>
        Array.isArray(v) && typeof v[0] === "string" && typeof v[1] === "number",
    );
    return new Map(rows);
  } catch {
    // Private mode or a blocked store: the entrance simply plays again.
    return new Map();
  }
}

function writeSeen(seen: Decisions): void {
  try {
    // Keep the tail only — a long-lived window never needs the whole history.
    sessionStorage.setItem(SEEN_KEY, JSON.stringify([...seen].slice(-200)));
  } catch {
    /* not persisted, still remembered for this window */
  }
}

interface SpawnState {
  /** Epoch ms of the last figure that came through the portal; 0 = never. */
  portalOpenedMs: number;
  /** Ids created in this window that are still owed their entrance. */
  announced: Set<string>;
  /** What this window decided per agent: when it walked out, or 0 for never. */
  seen: Decisions;
  /** When this window last asked the island to look at the foundry; 0 = never. */
  focusRequestedMs: number;
  announce: (agentId: string) => void;
  clearFocus: () => void;
  claim: (agentId: string, createdMs: number, now?: number) => boolean;
}

export const useSpawnStore = create<SpawnState>((set, get) => ({
  portalOpenedMs: 0,
  focusRequestedMs: 0,
  announced: new Set<string>(),
  seen: typeof sessionStorage === "undefined" ? (new Map() as Decisions) : readSeen(),
  announce: (agentId) => {
    const announced = new Set(get().announced);
    announced.add(agentId);
    // Made here, so watch it happen: the stage swings to the works.
    set({ announced, focusRequestedMs: Date.now() });
  },
  clearFocus: () => set({ focusRequestedMs: 0 }),
  claim: (agentId, createdMs, now = Date.now()) => {
    const { seen, announced } = get();
    const decided = seen.get(agentId);
    if (decided !== undefined) {
      // Already answered for this agent: a remount within the replay window
      // resumes the walk, anything later stands where the roster says.
      return decided > 0 && now - decided < ENTRANCE_REPLAY_MS;
    }
    const wasAnnounced = announced.has(agentId);
    const isFresh = Number.isFinite(createdMs) && now - createdMs < FRESH_ENTRANCE_MS;
    const owed = wasAnnounced || isFresh;
    const nextSeen = new Map(seen);
    nextSeen.set(agentId, owed ? now : 0);
    writeSeen(nextSeen);
    if (!owed) {
      set({ seen: nextSeen });
      return false;
    }
    const nextAnnounced = new Set(announced);
    nextAnnounced.delete(agentId);
    set({ seen: nextSeen, announced: nextAnnounced, portalOpenedMs: now });
    return true;
  },
}));

/** Called by the create flow the moment a row exists. */
export function announceSpawn(agentId: string): void {
  useSpawnStore.getState().announce(agentId);
}

/** Called once per walker on mount: may this figure walk out of the foundry? */
export function claimEntrance(agentId: string, createdMs: number): boolean {
  return useSpawnStore.getState().claim(agentId, createdMs);
}
