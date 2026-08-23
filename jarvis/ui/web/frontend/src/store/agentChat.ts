import { create } from "zustand";

import {
  agentChatSocketUrl,
  cancelAgentChatTurn,
  createAgentChatSession,
  deleteAgentChatSession,
  fetchAgentChatCatalog,
  fetchAgentChatSessions,
  fetchAgentConnections,
  fetchProviderModels,
  patchAgentChatSession,
  resolveAgentChatApproval,
  sendAgentChatMessage,
  type AgentChatCatalog,
  type AgentChatEvent,
  type AgentChatProvider,
  type AgentChatSession,
  type AgentConnectionRow,
  type ApprovalDecision,
  type CuratedModel,
  type PatchSessionInput,
} from "@/lib/agentChatApi";
import { EMPTY_TIMELINE, reduceEvent, reduceEvents, type Timeline } from "@/components/agentchat/reduce";

/**
 * The agent chat's own store — the typed half of the front page.
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

const DRAFT_KEY = "jarvis.agentChat.draft.v1";
const RECONNECT_MS = 1500;

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
}

interface AgentChatStore {
  catalog: AgentChatCatalog | null;
  connections: AgentConnectionRow[];
  catalogError: string | null;
  /** Live model lists per provider id (the API-backed rows). */
  liveModels: Record<string, CuratedModel[]>;
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
  providerById: (id: string) => ProviderOption | null;
  setDraft: (patch: Partial<ComposerDraft>) => Promise<void>;
  setPlan: (on: boolean) => Promise<void>;
  newChat: () => void;
  openSession: (sessionId: string) => void;
  removeSession: (sessionId: string) => Promise<void>;
  send: (text: string) => Promise<void>;
  cancel: () => Promise<void>;
  decide: (approvalId: string, decision: ApprovalDecision) => Promise<void>;
  /** Tests and the socket: fold one event into the active timeline. */
  ingest: (event: AgentChatEvent) => void;
  disconnect: () => void;
}

function readDraft(): ComposerDraft {
  const empty: ComposerDraft = {
    provider: "",
    model: "",
    effort: "",
    permissionMode: "",
    buildMode: "",
    cwd: "",
  };
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (!raw) return empty;
    const parsed = JSON.parse(raw) as Partial<ComposerDraft>;
    return { ...empty, ...parsed };
  } catch {
    return empty;
  }
}

function writeDraft(draft: ComposerDraft): void {
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
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

export const useAgentChatStore = create<AgentChatStore>((set, get) => {
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
        writeDraft(get().draft);
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
    catalog: null,
    connections: [],
    catalogError: null,
    liveModels: {},
    sessions: [],
    activeSessionId: null,
    activeSession: null,
    timeline: EMPTY_TIMELINE,
    socketState: "idle",
    draft: readDraft(),
    busy: false,
    lastError: null,

    loadCatalog: async () => {
      try {
        const [raw, connections] = await Promise.all([
          fetchAgentChatCatalog(),
          fetchAgentConnections().catch(() => [] as AgentConnectionRow[]),
        ]);
        // A backend older than this bundle (the app not yet restarted after
        // an update) may lack the newer arrays; an empty ladder is honest,
        // a crash is not.
        const catalog: AgentChatCatalog = {
          ...raw,
          providers: (raw.providers ?? []).map((p) => ({
            ...p,
            curated_models: Array.isArray(p.curated_models) ? p.curated_models : [],
            effort_levels: Array.isArray(p.effort_levels) ? p.effort_levels : [],
            permission_modes: Array.isArray(p.permission_modes) ? p.permission_modes : [],
            default_permission_mode: p.default_permission_mode ?? "",
            default_effort: p.default_effort ?? "",
            default_model: p.default_model ?? "",
          })),
        };
        set({ catalog, connections, catalogError: null });
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
            effort: draft.effort || known.default_effort,
            permissionMode: draft.permissionMode || known.default_permission_mode,
            buildMode: draft.buildMode || known.default_permission_mode,
            cwd: draft.cwd || catalog.default_cwd,
          };
        }
        if (draft !== st.draft) {
          set({ draft });
          writeDraft(draft);
        }
        if (draft.provider) void get().loadModels(draft.provider);
      } catch (err) {
        set({ catalogError: errorText(err) });
      }
    },

    loadSessions: async () => {
      try {
        const sessions = await fetchAgentChatSessions();
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

    providerOptions: () => {
      const { catalog, connections } = get();
      if (!catalog) return [];
      return catalog.providers.map((p) => {
        const row = connectionFor(p, connections);
        const credentialed = p.keyless || Boolean(row?.key_set);
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
      writeDraft(draft);
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

    send: async (text) => {
      const content = text.trim();
      if (!content) return;
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
        await sendAgentChatMessage(sid, content);
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
        writeDraft(patch.draft);
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
