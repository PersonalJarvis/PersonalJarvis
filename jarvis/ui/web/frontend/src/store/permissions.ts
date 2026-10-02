import { create } from "zustand";
import {
  EMPTY_PROMPTS,
  PERMISSION_EVENT_NAMES,
  dismissEpisode,
  isMacClient,
  reducePermissionEvent,
  seedPermissionEpisodes,
  type PermissionPromptsState,
} from "@/lib/permissionPrompts";
import { fetchPermissionSnapshot } from "@/lib/permissionsApi";
import type { PermissionSnapshot } from "@/lib/permissionSnapshot";

/**
 * Open macOS permission episodes, fed by the WS wildcard event stream
 * (useWebSocket.ts) and by REST seeds, rendered by PermissionPromptLayer and
 * the inline surfaces (hooks/useInlinePermission.ts).
 *
 * Deliberately its own store, like commandActivity: episodes change at
 * permission-dialog rhythm, and keeping them out of the big event store means
 * a card re-renders the card, not the whole shell.
 *
 * WS events only UPDATE; the list is SEEDED from `GET /api/permissions/status`
 * `needed[]` (mount, the `welcome` frame, a focus after a long absence),
 * because an event fired while this window was hidden or not yet created is
 * gone. Only the owner window (see `isPermissionOwnerWindow`) seeds: the host
 * component flips {@link PermissionsStoreState.owner}.
 */

/** Why the last dictation start did nothing, for the composer's inline note. */
export interface DictationNote {
  /** `DICTATION_REFUSAL_REASONS` token (or a normalised `ErrorOccurred` type). */
  reason: string;
  source: "refused" | "error";
  ts: number;
}

/**
 * The `DICTATION_REFUSAL_REASONS` tokens that mean "the start did not happen".
 * Only these may reset a window's optimistic `dictating` flag: the others
 * (`already_running`, and the post-recording `nothing_to_paste`,
 * `paste_unavailable`, `history_disabled`) come from another trigger or from a
 * recording that is over, and must not drop a live pill. The composer's own
 * start that hits an already-running dictation arrives as an `ErrorOccurred`
 * of layer `ui.web.dictation`, which resets separately.
 */
export const DICTATION_START_FAILURES: ReadonlySet<string> = new Set([
  "microphone_unavailable",
  "no_stt",
  "handover_failed",
  "pipeline_not_running",
  "voice_session_active",
]);

export function isDictationStartFailure(reason: unknown): boolean {
  return typeof reason === "string" && DICTATION_START_FAILURES.has(reason);
}

interface PermissionsStoreState extends PermissionPromptsState {
  /** The last snapshot any caller read (platform, headless, app name, rows). */
  snapshot: PermissionSnapshot | null;
  /** Set by the host component: this window shows prompts and seeds. */
  owner: boolean;
  /** Ref-counted inline surfaces: feature -> mounted count (see useInlinePermission). */
  inline: Record<string, number>;
  dictationNote: DictationNote | null;
  /**
   * The dictation button the person pressed last (several composers can be mounted
   * in one window and share the single `dictating` flag): only that one shows the
   * refusal note. Null until a press, and then every button may show it.
   */
  dictationOrigin: string | null;

  ingest: (eventName: string, traceId: string, payload: unknown, tsMs: number) => void;
  /**
   * Single-flight read of `GET /status`; a no-op outside the owner window.
   * `activated` marks the first read after the window regained focus (`?activated=1`).
   */
  seed: (options?: { activated?: boolean }) => Promise<void>;
  setSnapshot: (snapshot: PermissionSnapshot) => void;
  setOwner: (owner: boolean) => void;
  dismiss: (key: string) => void;
  registerInline: (feature: string) => () => void;
  noteDictationRefusal: (note: DictationNote) => void;
  clearDictationNote: () => void;
  setDictationOrigin: (id: string | null) => void;
}

let seedInflight: Promise<void> | null = null;
/** An `activated` seed asked for while another was in flight: it runs right after. */
let seedActivatedNext = false;

export const usePermissionsStore = create<PermissionsStoreState>()((set, get) => ({
  ...EMPTY_PROMPTS,
  snapshot: null,
  owner: false,
  inline: {},
  dictationNote: null,
  dictationOrigin: null,

  ingest: (eventName, traceId, payload, tsMs) => {
    const next = reducePermissionEvent(get(), eventName, traceId, payload, tsMs);
    if (next) set({ episodes: next.episodes, resolved: next.resolved });
  },

  seed: (options) => {
    if (!get().owner) return Promise.resolve();
    const activated = options?.activated === true || seedActivatedNext;
    if (seedInflight) {
      if (!activated) return seedInflight;
      // The running read started before the hint: follow it with one that carries it.
      seedActivatedNext = true;
      return seedInflight.then(() => get().seed());
    }
    seedActivatedNext = false;
    const startedAt = Date.now();
    seedInflight = (async () => {
      try {
        const snapshot = await fetchPermissionSnapshot({ activated });
        if (!snapshot) return;
        const next = seedPermissionEpisodes(get(), snapshot.needed, Date.now(), startedAt);
        set(next ? { snapshot, episodes: next.episodes } : { snapshot });
      } catch {
        // Offline, warming or unauthenticated backend: stay with what the WS
        // events delivered; the next welcome frame or focus seeds again. A
        // banner for a failed read is exactly what this design removed.
      } finally {
        seedInflight = null;
      }
    })();
    return seedInflight;
  },

  setSnapshot: (snapshot) => set({ snapshot }),

  setOwner: (owner) => set({ owner }),

  dismiss: (key) => {
    const next = dismissEpisode(get(), key);
    if (next) set({ episodes: next.episodes });
  },

  registerInline: (feature) => {
    set((state) => ({ inline: { ...state.inline, [feature]: (state.inline[feature] ?? 0) + 1 } }));
    let released = false;
    return () => {
      if (released) return;
      released = true;
      set((state) => {
        const count = (state.inline[feature] ?? 0) - 1;
        const inline = { ...state.inline };
        if (count > 0) inline[feature] = count;
        else delete inline[feature];
        return { inline };
      });
    };
  },

  noteDictationRefusal: (note) => {
    const previous = get().dictationNote;
    // A DictationBusy error follows the specific refusal that explains it:
    // never let the generic one overwrite the better reason.
    if (
      note.source === "error" &&
      previous?.source === "refused" &&
      note.ts - previous.ts < 2_000
    ) {
      return;
    }
    set({ dictationNote: note });
  },

  clearDictationNote: () => set({ dictationNote: null }),

  setDictationOrigin: (id) => set({ dictationOrigin: id }),
}));

/** The event names the WS hook needs to forward here (cheap pre-filter). */
export const PERMISSION_EVENTS: ReadonlySet<string> = PERMISSION_EVENT_NAMES;

/**
 * Whether the Settings page should offer the Privacy section: the backend's
 * platform once a snapshot was read, otherwise the client's own OS (so a
 * Windows or Linux window never flashes a macOS-only entry).
 */
export function privacySectionVisible(
  snapshot: PermissionSnapshot | null = usePermissionsStore.getState().snapshot,
): boolean {
  if (snapshot) return snapshot.platform === "darwin";
  return typeof navigator !== "undefined" && isMacClient(navigator.userAgent);
}

/** Reactive twin of {@link privacySectionVisible} for the Settings page. */
export function usePrivacySectionVisible(): boolean {
  return usePermissionsStore((state) => privacySectionVisible(state.snapshot));
}
