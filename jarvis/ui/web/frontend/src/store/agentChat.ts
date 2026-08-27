import { create } from "zustand";

import {
  agentChatSocketUrl,
  cancelAgentChatTurn,
  createAgentChatSession,
  deleteAgentChatSession,
  fetchAgentChatCatalog,
  fetchAgentChatSessions,
  fetchAgentConnections,
  fetchProviderHealth,
  fetchProviderModels,
  isApiRunner,
  patchAgentChatSession,
  resolveAgentChatApproval,
  sendAgentChatMessage,
  type AgentChatCatalog,
  type AgentChatEvent,
  type AgentChatProvider,
  type AgentChatSession,
  type AgentChatSurface,
  type AgentConnectionRow,
  type ApprovalDecision,
  type ChatAttachment,
  type CuratedModel,
  type PatchSessionInput,
  type ProviderHealth,
} from "@/lib/agentChatApi";
import { EMPTY_TIMELINE, reduceEvent, reduceEvents, type Timeline } from "@/components/agentchat/reduce";

/**
 * The agent chat's store — one per SURFACE (`createAgentChatStore`).
 *
 * The front page's store (`useAgentChatStore`, surface `jarvis`) is Jarvis
 * with a keyboard: what is typed there goes to the same assistant the
 * microphone reaches, and its sessions are the front page's chats. An
 * Agentic IDE store (surface `agent`) holds coding sessions instead. Each
 * store asks the backend for its own list and catalog, stamps its surface on
 * the sessions it creates, and keeps its own socket and draft.
 *
 * One active session at a time: its timeline is fed by one WebSocket
 * (snapshot, then live events, reconnect from the last seq). The composer's
 * choice of provider / model / effort / permission mode lives here as the
 * DRAFT: for a fresh chat it is what the next session is created with; with
 * a session open, changing a pick patches that session on the backend and
 * the same draft mirrors it. The draft survives a reload (localStorage) so
 * the composer opens on what you used last, like the Claude app does.
 *
 * The provider list is the backend catalog (`/api/agent-chat/catalog`)
 * joined with the Agents tab's credential truth (`/api/jarvis-agent/status`):
 * a provider is offered when it is connected — a key saved, a subscription
 * logged in, or a local server — and shown greyed out with a "connect" hint
 * otherwise. The one marked active on the Agents tab is the draft's default.
 */

/**
 * Where the composer's draft survives a reload. The front page keeps the key
 * it has always had, so picks made before the surface split still load; an
 * IDE store gets its own so the two never overwrite each other's picks.
 */
export function draftKey(surface: AgentChatSurface): string {
  // v2 for the front page (2026-08-25): the v1 draft was written while the
  // IDE's chat and the front page shared one store, so it carried the IDE's
  // last folder and a vendor permission mode the Jarvis ladder does not know.
  // A new key starts the Jarvis chat clean — the active brain, the home
  // folder, the ladder's default — instead of on a coding session's picks.
  return surface === "jarvis" ? "jarvis.agentChat.draft.v2" : `jarvis.agentChat.draft.${surface}.v1`;
}
const RECONNECT_MS = 1500;
/** How many sessions the list holds — the "All chats" archive shows them all. */
const SESSION_LIST_LIMIT = 500;

export interface ComposerDraft {
  provider: string;
  model: string;
  effort: string;
  permissionMode: string;
  /** The permission mode to go back to when Plan is switched off. */
  buildMode: string;
  cwd: string;
}

export interface ProviderOption extends AgentChatProvider {
  /** Usable right now: credential present (or keyless) and, for a CLI, installed. */
  connected: boolean;
  /** The sub-agent marked active on the Agents tab. */
  active: boolean;
  /**
   * Draw this row with a CODING AGENT's mark rather than a provider-family
   * logo, naming the workspace entry whose mark it is ("opencode", "kimi").
   *
   * The catalog's rows are brands the provider table knows; the Agentic IDE's
   * rows are the CLIs installed on this machine, and several of them —
   * OpenCode, Kimi, GLM — are marks only `AgentMark` carries. Without this the
   * IDE's picker would fall back to a letter in a box, which is the one thing
   * a logo must never be here. Absent for every catalog row, which keeps the
   * front page's picker exactly as it was.
   */
  agentMark?: string;
  /** A mark the entry brings itself — the file a user uploaded for their own CLI. */
  logoUrl?: string;
}

export interface AgentChatStore {
  /** Which list and catalog this store speaks for; fixed at creation. */
  readonly surface: AgentChatSurface;
  catalog: AgentChatCatalog | null;
  connections: AgentConnectionRow[];
  catalogError: string | null;
  /**
   * The catalog answered without the fields this bundle reads (no permission
   * ladders): the backend process predates the app on disk — an update that
   * was reloaded but not yet restarted. The composer says so instead of
   * silently showing fewer picks.
   */
  backendOutdated: boolean;
  /** Live model lists per provider id (the API-backed rows). */
  liveModels: Record<string, CuratedModel[]>;
  /** Live per-provider state, keyed by provider id; empty until the sweep lands. */
  health: Record<string, ProviderHealth>;
  sessions: AgentChatSession[];
  activeSessionId: string | null;
  activeSession: AgentChatSession | null;
  timeline: Timeline;
  socketState: "idle" | "connecting" | "open" | "closed";
  draft: ComposerDraft;
  busy: boolean;
  lastError: string | null;

  loadCatalog: () => Promise<void>;
  loadSessions: () => Promise<void>;
  loadModels: (providerId: string) => Promise<void>;
  providerOptions: () => ProviderOption[];
  /** Ask which seats actually answer. Never awaited by a render path. */
  loadHealth: (refresh?: boolean) => Promise<void>;
  providerById: (id: string) => ProviderOption | null;
  setDraft: (patch: Partial<ComposerDraft>) => Promise<void>;
  setPlan: (on: boolean) => Promise<void>;
  newChat: () => void;
  openSession: (sessionId: string) => void;
  removeSession: (sessionId: string) => Promise<void>;
  /** Send the sentence, with whatever files the composer is holding for it. */
  send: (text: string, attachments?: ChatAttachment[]) => Promise<void>;
  cancel: () => Promise<void>;
  decide: (approvalId: string, decision: ApprovalDecision) => Promise<void>;
  /** Tests and the socket: fold one event into the active timeline. */
  ingest: (event: AgentChatEvent) => void;
  disconnect: () => void;
}

function readDraft(key: string): ComposerDraft {
  const empty: ComposerDraft = {
    provider: "",
    model: "",
    effort: "",
    permissionMode: "",
    buildMode: "",
    cwd: "",
  };
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return empty;
    const parsed = JSON.parse(raw) as Partial<ComposerDraft>;
    return { ...empty, ...parsed };
  } catch {
    return empty;
  }
}

function writeDraft(key: string, draft: ComposerDraft): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(draft));
  } catch {
    /* storage blocked — the draft just does not survive a reload */
  }
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Which `/api/jarvis-agent/status` row speaks for a catalog provider. */
function connectionFor(
  provider: AgentChatProvider,
  rows: AgentConnectionRow[],
): AgentConnectionRow | undefined {
  return rows.find((r) => r.jarvis === provider.id);
}

/** A session patch the composer's draft must mirror. */
function draftFromSession(session: AgentChatSession, prev: ComposerDraft): ComposerDraft {
  const plan = session.permission_mode === "plan";
  return {
    provider: session.provider,
    model: session.model,
    effort: session.effort,
    permissionMode: session.permission_mode,
    buildMode: plan ? prev.buildMode || prev.permissionMode : session.permission_mode,
    cwd: session.cwd,
  };
}

/**
 * One store per surface. Each holds its own socket, its own draft key and
 * its own session list, so the front page's chat and an IDE's coding
 * sessions can be open at the same time without sharing a connection.
 */
/** `value` when the ladder offers it (or the ladder is unknown), else `fallback`. */
function onLadder(value: string, ladder: readonly string[], fallback: string): string {
  if (!value) return fallback;
  if (ladder.length === 0) return value;
  return ladder.includes(value) ? value : fallback;
}

export function createAgentChatStore(surface: AgentChatSurface) {
  const DRAFT_KEY = draftKey(surface);

  let socket: WebSocket | null = null;
  let socketSession: string | null = null;
  let reconnectTimer: number | null = null;

  function closeSocket(): void {
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    const s = socket;
    socket = null;
    socketSession = null;
    if (s) {
      s.onclose = null;
      s.onmessage = null;
      s.onerror = null;
      s.onopen = null;
      try {
        s.close();
      } catch {
        /* already closed */
      }
    }
  }

  return create<AgentChatStore>((set, get) => {
    function connect(sessionId: string, afterSeq: number): void {
      if (typeof WebSocket === "undefined") return;
      closeSocket();
      socketSession = sessionId;
      set({ socketState: "connecting" });
      const ws = new WebSocket(agentChatSocketUrl(sessionId, afterSeq));
      socket = ws;
      ws.onopen = () => {
        if (socket === ws) set({ socketState: "open" });
      };
      ws.onmessage = (msg) => {
        if (socket !== ws) return;
        let frame: { type?: string; session?: AgentChatSession; events?: AgentChatEvent[]; event?: AgentChatEvent };
        try {
          frame = JSON.parse(String(msg.data));
        } catch {
          return;
        }
        if (frame.type === "snapshot" && frame.session) {
          const prev = get();
          // A snapshot after a reconnect only carries events past `after`;
          // fold them onto what is already there, else start fresh.
          const base = afterSeq > 0 && prev.activeSessionId === sessionId ? prev.timeline : EMPTY_TIMELINE;
          const tl = reduceEvents(base, frame.events ?? []);
          set({
            activeSession: frame.session,
            timeline: { ...tl, sessionPatch: null },
            draft: draftFromSession(frame.session, prev.draft),
          });
          writeDraft(DRAFT_KEY, get().draft);
          return;
        }
        if (frame.type === "event" && frame.event) get().ingest(frame.event);
      };
      ws.onclose = () => {
        if (socket !== ws) return;
        socket = null;
        set({ socketState: "closed" });
        // Reconnect while this session is still the open one; the backend
        // replays what was missed from the last seq.
        reconnectTimer = window.setTimeout(() => {
          reconnectTimer = null;
          const st = get();
          if (st.activeSessionId === sessionId && socketSession === sessionId) {
            connect(sessionId, st.timeline.lastSeq);
          }
        }, RECONNECT_MS);
      };
      ws.onerror = () => {
        /* onclose follows and schedules the reconnect */
      };
    }

    return {
      surface,
      catalog: null,
      connections: [],
      catalogError: null,
      backendOutdated: false,
      liveModels: {},
      health: {},
      sessions: [],
      activeSessionId: null,
      activeSession: null,
      timeline: EMPTY_TIMELINE,
      socketState: "idle",
      draft: readDraft(DRAFT_KEY),
      busy: false,
      lastError: null,

      loadCatalog: async () => {
        try {
          const [raw, connections] = await Promise.all([
            fetchAgentChatCatalog(surface),
            fetchAgentConnections().catch(() => [] as AgentConnectionRow[]),
          ]);
          // A backend older than this bundle (the app not yet restarted after
          // an update) may lack the newer arrays; an empty ladder is honest,
          // a crash is not — and the composer tells the person to restart.
          // `permission_modes` is the tell: every row of the current route
          // carries the array (possibly empty); only the older route omits it.
          const providersRaw = raw.providers ?? [];
          const backendOutdated =
            providersRaw.length > 0 && providersRaw.some((p) => !Array.isArray(p.permission_modes));
          const catalog: AgentChatCatalog = {
            ...raw,
            providers: providersRaw.map((p) => ({
              ...p,
              curated_models: Array.isArray(p.curated_models) ? p.curated_models : [],
              effort_levels: Array.isArray(p.effort_levels) ? p.effort_levels : [],
              permission_modes: Array.isArray(p.permission_modes) ? p.permission_modes : [],
              default_permission_mode: p.default_permission_mode ?? "",
              default_effort: p.default_effort ?? "",
              default_model: p.default_model ?? "",
            })),
          };
          set({ catalog, connections, catalogError: null, backendOutdated });
          // Deliberately not awaited: the sweep makes one real request per
          // provider and can take seconds. The composer paints from the
          // catalog now and the dots appear when the answers arrive.
          void get().loadHealth();
          // Settle the draft: an empty or unknown provider becomes the active
          // sub-agent (else the first connected one); blank picks take the
          // provider's defaults.
          const st = get();
          const options = st.providerOptions();
          let draft = st.draft;
          const known = options.find((o) => o.id === draft.provider);
          if (!known) {
            const pick = options.find((o) => o.active && o.connected) ?? options.find((o) => o.connected) ?? options[0];
            if (pick) {
              draft = {
                provider: pick.id,
                model: pick.default_model,
                effort: pick.default_effort,
                permissionMode: pick.default_permission_mode,
                buildMode: pick.default_permission_mode,
                cwd: draft.cwd || catalog.default_cwd,
              };
            }
          } else {
            draft = {
              ...draft,
              effort: onLadder(draft.effort, known.effort_levels, known.default_effort),
              // A stored mode the row's ladder does not offer (a vendor word
              // from before the unified ladder, a pick made on another
              // surface) snaps to the row's default rather than showing a
              // label the person cannot choose.
              permissionMode: onLadder(
                draft.permissionMode,
                known.permission_modes.map((m) => m.id),
                known.default_permission_mode,
              ),
              buildMode: onLadder(
                draft.buildMode,
                known.permission_modes.map((m) => m.id),
                known.default_permission_mode,
              ),
              cwd: draft.cwd || catalog.default_cwd,
            };
          }
          if (draft !== st.draft) {
            set({ draft });
            writeDraft(DRAFT_KEY, draft);
          }
          if (draft.provider) void get().loadModels(draft.provider);
        } catch (err) {
          set({ catalogError: errorText(err) });
        }
      },

      loadSessions: async () => {
        try {
          const rows = await fetchAgentChatSessions(SESSION_LIST_LIMIT, surface);
          // The backend filters by surface; a row that names another surface
          // anyway is dropped here too. A row with no surface at all comes
          // from a backend older than the split — those are kept, so an
          // update never makes a person's chats vanish until the restart.
          const sessions = rows.filter((row) => row.surface === undefined || row.surface === surface);
          set({ sessions });
        } catch {
          /* offline / headless — keep the list as-is */
        }
      },

      loadModels: async (providerId) => {
        const provider = get().catalog?.providers.find((p) => p.id === providerId);
        if (!provider || provider.models_source !== "live") return;
        if (get().liveModels[providerId]) return;
        try {
          const models = await fetchProviderModels(providerId);
          const rows: CuratedModel[] = models.map((m) => ({ id: m.id, label: m.label ?? m.name ?? m.id }));
          set((s) => ({ liveModels: { ...s.liveModels, [providerId]: rows } }));
        } catch {
          /* the curated list stays */
        }
      },

      loadHealth: async (refresh = false) => {
        try {
          const rows = await fetchProviderHealth(surface, refresh);
          const health: Record<string, ProviderHealth> = {};
          for (const row of rows) health[row.provider] = row;
          set({ health });
        } catch {
          // A backend too old to know the route, or a sweep that failed: the
          // composer simply shows no live state, which is what it did before.
          // Never an error banner — nothing the person did went wrong.
        }
      },

      providerOptions: () => {
        const { catalog, connections } = get();
        if (!catalog) return [];
        return catalog.providers.map((p) => {
          const row = connectionFor(p, connections);
          // An API seat needs an API KEY, not just any credential. The Agents
          // tab counts a subscription login as connected — right for a row a
          // CLI runs — but the brain runner calls the provider's endpoint,
          // where a Claude Code login buys nothing. `key_set` stands in when
          // an older backend does not report the finer field.
          const credentialed = p.keyless
            ? true
            : isApiRunner(p.runner)
              ? Boolean(row?.api_key_set ?? row?.key_set)
              : Boolean(row?.key_set);
          const installed = p.cli_installed === null ? true : p.cli_installed;
          return {
            ...p,
            connected: credentialed && installed,
            active: Boolean(row?.is_active_brain),
          };
        });
      },

      providerById: (id) => get().providerOptions().find((p) => p.id === id) ?? null,

      setDraft: async (patch) => {
        const st = get();
        let draft: ComposerDraft = { ...st.draft, ...patch };
        // A provider change re-seats model / effort / permission on the new
        // provider's defaults unless the caller set them explicitly.
        if (patch.provider && patch.provider !== st.draft.provider) {
          const p = st.providerById(patch.provider);
          if (p) {
            draft = {
              ...draft,
              model: patch.model ?? p.default_model,
              effort: patch.effort ?? p.default_effort,
              permissionMode: patch.permissionMode ?? p.default_permission_mode,
              buildMode: patch.permissionMode ?? p.default_permission_mode,
            };
          }
          void get().loadModels(patch.provider);
        } else if (patch.permissionMode && patch.permissionMode !== "plan") {
          draft = { ...draft, buildMode: patch.permissionMode };
        }
        set({ draft });
        writeDraft(DRAFT_KEY, draft);
        // With a session open, the pick applies to it right away.
        const sid = st.activeSessionId;
        if (sid) {
          const body: PatchSessionInput = {};
          if (patch.provider !== undefined) body.provider = draft.provider;
          if (patch.provider !== undefined || patch.model !== undefined) body.model = draft.model;
          if (patch.provider !== undefined || patch.effort !== undefined) body.effort = draft.effort;
          if (patch.provider !== undefined || patch.permissionMode !== undefined) {
            body.permission_mode = draft.permissionMode;
          }
          if (patch.cwd !== undefined) body.cwd = draft.cwd;
          try {
            const session = await patchAgentChatSession(sid, body);
            set((s) => ({
              activeSession: s.activeSessionId === sid ? session : s.activeSession,
              sessions: s.sessions.map((x) => (x.session_id === sid ? { ...x, ...session } : x)),
            }));
          } catch (err) {
            set({ lastError: errorText(err) });
          }
        }
      },

      setPlan: async (on) => {
        const st = get();
        if (on) {
          await st.setDraft({ permissionMode: "plan" });
        } else {
          const p = st.providerById(st.draft.provider);
          const back = st.draft.buildMode && st.draft.buildMode !== "plan" ? st.draft.buildMode : p?.default_permission_mode ?? "";
          await st.setDraft({ permissionMode: back });
        }
      },

      newChat: () => {
        closeSocket();
        set({
          activeSessionId: null,
          activeSession: null,
          timeline: EMPTY_TIMELINE,
          socketState: "idle",
          lastError: null,
        });
      },

      openSession: (sessionId) => {
        if (get().activeSessionId === sessionId && socket) return;
        set({
          activeSessionId: sessionId,
          activeSession: get().sessions.find((s) => s.session_id === sessionId) ?? null,
          timeline: EMPTY_TIMELINE,
          lastError: null,
        });
        connect(sessionId, 0);
      },

      removeSession: async (sessionId) => {
        try {
          await deleteAgentChatSession(sessionId);
        } catch (err) {
          set({ lastError: errorText(err) });
        }
        if (get().activeSessionId === sessionId) get().newChat();
        set((s) => ({ sessions: s.sessions.filter((x) => x.session_id !== sessionId) }));
        void get().loadSessions();
      },

      send: async (text, attachments = []) => {
        const content = text.trim();
        // A message may be files alone — dropping a screenshot and pressing
        // Enter is a complete gesture — but never nothing at all.
        if (!content && attachments.length === 0) return;
        const st = get();
        set({ busy: true, lastError: null });
        try {
          let sid = st.activeSessionId;
          if (!sid) {
            const d = st.draft;
            const session = await createAgentChatSession({
              provider: d.provider,
              model: d.model,
              effort: d.effort,
              cwd: d.cwd || null,
              permission_mode: d.permissionMode,
              surface,
            });
            sid = session.session_id;
            set({
              activeSessionId: sid,
              activeSession: session,
              timeline: EMPTY_TIMELINE,
              sessions: [session, ...get().sessions],
            });
            connect(sid, 0);
          }
          await sendAgentChatMessage(sid, content, attachments);
          void get().loadSessions();
        } catch (err) {
          set({ lastError: errorText(err) });
        } finally {
          set({ busy: false });
        }
      },

      cancel: async () => {
        const sid = get().activeSessionId;
        if (!sid) return;
        try {
          await cancelAgentChatTurn(sid);
        } catch (err) {
          set({ lastError: errorText(err) });
        }
      },

      decide: async (approvalId, decision) => {
        const sid = get().activeSessionId;
        if (!sid) return;
        try {
          await resolveAgentChatApproval(sid, approvalId, decision);
        } catch (err) {
          set({ lastError: errorText(err) });
        }
      },

      ingest: (event) => {
        const st = get();
        const tl = reduceEvent(st.timeline, event);
        if (tl === st.timeline) return;
        const patch: Partial<AgentChatStore> = { timeline: tl };
        if (tl.sessionPatch && st.activeSession) {
          const session = { ...st.activeSession, ...tl.sessionPatch } as AgentChatSession;
          patch.activeSession = session;
          patch.draft = draftFromSession(session, st.draft);
          writeDraft(DRAFT_KEY, patch.draft);
        }
        set(patch);
        if (event.kind === "turn_finished" || event.kind === "user_message") void st.loadSessions();
      },

      disconnect: () => {
        closeSocket();
        set({ socketState: "idle" });
      },
    };
  });
}

/** The front page's store — every existing call site reads this one. */
export const useAgentChatStore = createAgentChatStore("jarvis");

/**
 * The Agentic IDE's chat store — surface `agent`: coding-agent sessions in a
 * workspace's folder, with their own sessions, draft and socket. The shared
 * chat components reach it through `AgentChatStoreProvider`
 * (components/agentchat/AgentChatStoreContext); nothing on the front page
 * ever reads it, so the IDE's last chat can never appear there.
 */
export const useAgentSessionStore = createAgentChatStore("agent");

/** The hook a `createAgentChatStore` call returns — what the context carries. */
export type AgentChatStoreHook = typeof useAgentChatStore;
