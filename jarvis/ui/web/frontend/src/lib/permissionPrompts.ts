/**
 * Open permission episodes as the UI sees them — the pure reducer behind the
 * prompt layer (`PermissionPromptLayer`) and every inline surface.
 *
 * A feature that needs a macOS permission opens ONE episode on the backend
 * (`jarvis/platform/permission_service.py`). The backend publishes
 * `PermissionNeeded` when the episode changes and `PermissionResolved` when it
 * ends, and keeps the open ones in `outstanding()`, which `GET /api/permissions/status`
 * serves as `needed[]`. Events are not persistent: a window that was hidden or
 * not yet created when one fired never saw it. So the list is SEEDED from
 * `needed[]` (mount, the WS `welcome` frame, a focus after a long absence) and
 * WS events only UPDATE it.
 *
 * Identity of an episode: the backend keys it by (feature, permission set), and
 * the set can shrink when one of two permissions gets granted. Two entries are
 * the same episode when they share a feature AND either the same set or the
 * same `trace_id` with overlapping sets (the trace id is fixed for the life of
 * an episode). The map key {@link episodeKey} is (feature, sorted pane
 * families); `event_posting` is folded into `accessibility`, one pane.
 *
 * The card only opens for a person who has something to do: `origin = "user"`
 * (a gesture caused it) and `phase = "blocked"` (macOS is NOT asking by
 * itself). Episodes of other origins/phases stay in the list so inline
 * surfaces and later edges can use them.
 *
 * Pure module on purpose: no React, no zustand, no timers — fully covered by
 * permissionPrompts.test.ts (same pattern as commandActivity.ts).
 */
import {
  PERMISSION_NEEDED_ORIGINS,
  PERMISSION_NEEDED_PHASES,
  PERMISSION_NEEDED_REASONS,
  type PermissionNeededOrigin,
  type PermissionNeededPhase,
  type PermissionNeededReason,
} from "./permissionEvents";

/** One open episode. Field names follow `PermissionNeeded` / `Episode.as_dict()`. */
export interface PromptEpisode {
  /** {@link episodeKey}: stable across updates of the same episode. */
  key: string;
  /** A `PERMISSION_FEATURES` token (kept a string: a newer backend may add one). */
  feature: string;
  /** Pane families still missing, in the order the backend lists them. */
  permissions: string[];
  reason: PermissionNeededReason;
  phase: PermissionNeededPhase;
  origin: PermissionNeededOrigin;
  /** Automation player bundle id, else "". */
  target: string;
  can_prompt: boolean;
  can_open_settings: boolean;
  outside_app: boolean;
  /** English support sentence: only shown in a collapsed details section. */
  detail: string;
  trace_id: string;
  /** Wall-clock ms of the last update; orders the stack (newest first). */
  updatedAt: number;
  /** "Not now": episode-scoped and memory-only, cleared by news (reason/phase change). */
  dismissed: boolean;
}

/** An episode that ended, kept briefly for the "Allowed" confirmation and inline notes. */
export interface ResolvedNote {
  id: string;
  feature: string;
  permissions: string[];
  granted: boolean;
  ts: number;
  /** True when the floating card was showing this episode: it confirms instead of vanishing. */
  hadCard: boolean;
}

export interface PermissionPromptsState {
  episodes: PromptEpisode[];
  /** Newest first, capped at {@link MAX_RESOLVED_NOTES}. */
  resolved: ResolvedNote[];
}

export const EMPTY_PROMPTS: PermissionPromptsState = { episodes: [], resolved: [] };

/** Hard cap: a runaway producer must not grow the list unbounded. */
export const MAX_EPISODES = 16;
export const MAX_RESOLVED_NOTES = 8;
/** How long the "Allowed" confirmation (and its polite live region) stays: >= 5 s. */
export const RESOLVED_HOLD_MS = 6_000;

/**
 * The CSS custom property the prompt layer sets on <html> to the height of its
 * card plus a gap, so the toast column below it starts under the card instead
 * of behind it (both live top-right; see ToastLayer and PermissionPromptLayer).
 */
export const PERMISSION_CARD_OFFSET_VAR = "--permission-card-offset";

/** The events the WS hook forwards here (cheap pre-filter). */
export const PERMISSION_EVENT_NAMES: ReadonlySet<string> = new Set([
  "PermissionNeeded",
  "PermissionResolved",
]);

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function bool(value: unknown): boolean {
  return value === true;
}

/** `event_posting` is an alias of `accessibility`: one pane, one step. */
export function paneFamily(permission: string): string {
  return permission === "event_posting" ? "accessibility" : permission;
}

function normalizePermissions(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const entry of value) {
    if (typeof entry !== "string" || entry === "") continue;
    const family = paneFamily(entry);
    if (seen.has(family)) continue;
    seen.add(family);
    out.push(family);
  }
  return out;
}

/** The map key of an episode: the feature and its sorted pane families. */
export function episodeKey(feature: string, permissions: readonly string[]): string {
  return `${feature}:${[...permissions].sort().join("+")}`;
}

function oneOf<T extends string>(allowed: readonly T[], value: unknown): T | null {
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : null;
}

function overlaps(a: readonly string[], b: readonly string[]): boolean {
  return a.some((entry) => b.includes(entry));
}

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((entry) => b.includes(entry));
}

function isSameEpisode(
  existing: PromptEpisode,
  feature: string,
  permissions: readonly string[],
  traceId: string,
): boolean {
  if (existing.feature !== feature) return false;
  if (sameSet(existing.permissions, permissions)) return true;
  return traceId !== "" && existing.trace_id === traceId && overlaps(existing.permissions, permissions);
}

/**
 * Read one episode from a `PermissionNeeded` payload or a `needed[]` entry.
 * Null when it cannot be rendered honestly (no feature, no permission, a reason
 * /phase/origin from a vocabulary this UI does not know).
 */
export function parseEpisode(
  raw: unknown,
  traceId: string,
  updatedAt: number,
): PromptEpisode | null {
  if (raw === null || typeof raw !== "object") return null;
  const p = raw as Record<string, unknown>;
  const feature = str(p.feature);
  const permissions = normalizePermissions(p.permissions);
  const reason = oneOf(PERMISSION_NEEDED_REASONS, p.reason);
  const phase = oneOf(PERMISSION_NEEDED_PHASES, p.phase);
  const origin = oneOf(PERMISSION_NEEDED_ORIGINS, p.origin);
  if (!feature || permissions.length === 0 || !reason || !phase || !origin) return null;
  return {
    key: episodeKey(feature, permissions),
    feature,
    permissions,
    reason,
    phase,
    origin,
    target: str(p.target),
    can_prompt: bool(p.can_prompt),
    can_open_settings: bool(p.can_open_settings),
    outside_app: bool(p.outside_app),
    detail: str(p.detail),
    trace_id: str(p.trace_id) || traceId,
    updatedAt,
    dismissed: false,
  };
}

/** A card is for a person with something to do: a user gesture, macOS not asking by itself. */
export function isCardEpisode(episode: PromptEpisode): boolean {
  return episode.origin === "user" && episode.phase === "blocked" && !episode.dismissed;
}

function capEpisodes(episodes: PromptEpisode[]): PromptEpisode[] {
  if (episodes.length <= MAX_EPISODES) return episodes;
  return [...episodes].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_EPISODES);
}

/** Insert or replace one episode; "Not now" survives only while nothing new was said. */
function upsert(episodes: PromptEpisode[], incoming: PromptEpisode): PromptEpisode[] {
  const index = episodes.findIndex((entry) =>
    isSameEpisode(entry, incoming.feature, incoming.permissions, incoming.trace_id),
  );
  if (index === -1) return capEpisodes([...episodes, incoming]);
  const previous = episodes[index];
  const unchanged = previous.reason === incoming.reason && previous.phase === incoming.phase;
  const next = [...episodes];
  next[index] = { ...incoming, dismissed: unchanged && previous.dismissed };
  return next;
}

function addResolved(notes: ResolvedNote[], note: ResolvedNote): ResolvedNote[] {
  return [note, ...notes].slice(0, MAX_RESOLVED_NOTES);
}

/**
 * Apply one WS event. Returns the new state, or null when the event is
 * irrelevant (the common case: callers skip the store update entirely).
 */
export function reducePermissionEvent(
  state: PermissionPromptsState,
  eventName: string,
  traceId: string,
  payload: unknown,
  tsMs: number,
): PermissionPromptsState | null {
  if (eventName === "PermissionNeeded") {
    const episode = parseEpisode(payload, traceId, tsMs);
    if (!episode) return null;
    return { ...state, episodes: upsert(state.episodes, episode) };
  }

  if (eventName === "PermissionResolved") {
    if (payload === null || typeof payload !== "object") return null;
    const p = payload as Record<string, unknown>;
    const feature = str(p.feature);
    if (!feature) return null;
    const permissions = normalizePermissions(p.permissions);
    const ended = state.episodes.filter(
      (entry) =>
        entry.feature === feature &&
        (permissions.length === 0 || overlaps(entry.permissions, permissions)),
    );
    const granted = bool(p.granted);
    const note: ResolvedNote = {
      id: `${tsMs}:${feature}`,
      feature,
      permissions: permissions.length > 0 ? permissions : (ended[0]?.permissions ?? []),
      granted,
      ts: tsMs,
      hadCard: ended.some(isCardEpisode),
    };
    return {
      episodes: state.episodes.filter((entry) => !ended.includes(entry)),
      resolved: addResolved(state.resolved, note),
    };
  }

  return null;
}

/**
 * Replace the open episodes with the server's `needed[]` (the truth), keeping
 * "Not now" for an unchanged episode. An episode that arrived over the socket
 * AFTER the request was sent (`startedAtMs`) is newer than the answer, so it
 * survives a snapshot that did not know it yet. The same ordering holds the
 * other way round: an episode a `PermissionResolved` closed AFTER the request
 * was sent is gone even when the (older) snapshot still lists it, so a stale
 * answer cannot resurrect a card for a permission that was just granted.
 *
 * Null when `needed` is not a list (an older backend): keep what we have.
 */
export function seedPermissionEpisodes(
  state: PermissionPromptsState,
  needed: unknown,
  nowMs: number,
  startedAtMs: number,
): PermissionPromptsState | null {
  if (!Array.isArray(needed)) return null;
  let episodes: PromptEpisode[] = [];
  for (const raw of needed) {
    const parsed = parseEpisode(raw, "", nowMs);
    if (!parsed) continue;
    // Resolved while the request was in flight: the snapshot is older than the edge.
    const endedSince = state.resolved.some(
      (note) =>
        note.ts >= startedAtMs &&
        note.feature === parsed.feature &&
        (note.permissions.length === 0 || overlaps(note.permissions, parsed.permissions)),
    );
    if (endedSince) continue;
    const before = state.episodes.find((entry) => entry.key === parsed.key);
    episodes = upsert(
      episodes,
      before && before.reason === parsed.reason && before.phase === parsed.phase
        ? { ...parsed, dismissed: before.dismissed }
        : parsed,
    );
  }
  for (const entry of state.episodes) {
    if (entry.updatedAt >= startedAtMs && !episodes.some((e) => e.key === entry.key)) {
      episodes = upsert(episodes, entry);
    }
  }
  return { ...state, episodes };
}

/** "Not now": hide this episode's card until it says something new. Memory only. */
export function dismissEpisode(
  state: PermissionPromptsState,
  key: string,
): PermissionPromptsState | null {
  if (!state.episodes.some((entry) => entry.key === key && !entry.dismissed)) return null;
  return {
    ...state,
    episodes: state.episodes.map((entry) =>
      entry.key === key ? { ...entry, dismissed: true } : entry,
    ),
  };
}

/**
 * The episodes the floating card may show, newest first. `inlineFeatures` are
 * the features whose own surface is mounted right now (they explain it in
 * place, so the card must not repeat them).
 */
export function cardEpisodes(
  state: PermissionPromptsState,
  inlineFeatures: ReadonlySet<string>,
): PromptEpisode[] {
  return state.episodes
    .filter((entry) => isCardEpisode(entry) && !inlineFeatures.has(entry.feature))
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

/** The newest open episode of a feature, whatever its origin or phase (for inline surfaces). */
export function episodeForFeature(
  state: PermissionPromptsState,
  feature: string,
): PromptEpisode | null {
  let best: PromptEpisode | null = null;
  for (const entry of state.episodes) {
    if (entry.feature === feature && (best === null || entry.updatedAt > best.updatedAt)) {
      best = entry;
    }
  }
  return best;
}

/** The newest confirmation for any of `features` no older than `maxAgeMs`. */
export function recentResolved(
  state: PermissionPromptsState,
  features: readonly string[],
  nowMs: number,
  maxAgeMs: number,
): ResolvedNote | null {
  return (
    state.resolved.find((note) => features.includes(note.feature) && nowMs - note.ts < maxAgeMs) ??
    null
  );
}

/** Clients whose user agent says macOS (the embedded WebView and Safari/Chrome on a Mac). */
export function isMacClient(userAgent: string): boolean {
  return /Macintosh|Mac OS X/i.test(userAgent);
}

/**
 * Which window owns the host-level permission prompts and polls.
 *
 * Every window has its own WS and store and the server broadcasts to all of
 * them, so without an owner N windows would show N cards and run N fetches. The
 * main window is the owner; a detached solo window is one section and never is.
 * `embedded` (a pywebview/WebView bridge) keeps a remote browser out: it must
 * never show host-only "Open System Settings" actions for another computer.
 * `macClient` keeps the boot-time `/status` fetch off Windows and Linux.
 */
export function isPermissionOwnerWindow(input: {
  solo: boolean;
  embedded: boolean;
  macClient: boolean;
}): boolean {
  return !input.solo && input.embedded && input.macClient;
}

/** Whether the layer may draw a card at all (the owner window, on a desktop session). */
export function isCardEligible(input: { owner: boolean; headless: boolean | null }): boolean {
  return input.owner && input.headless !== true;
}
