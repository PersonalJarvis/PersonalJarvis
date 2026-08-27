import { create } from "zustand";

import {
  EMPTY_TIMELINE,
  reduceEvent,
  reduceEvents,
  type Timeline,
  type TimelineItem,
} from "@/components/agentchat/reduce";
import {
  fetchTerminalTimeline,
  interruptTerminal,
  promptTerminal,
  type PaneActivity,
  type TerminalTimelineResponse,
} from "@/lib/agenticIdeApi";
import type {
  AgentChatCatalog,
  AgentChatEvent,
  AgentChatSession,
  ChatAttachment,
} from "@/lib/agentChatApi";
import {
  useAgentSessionStore,
  type AgentChatStore,
  type ComposerDraft,
  type ProviderOption,
} from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import { translate } from "@/i18n";

/**
 * A terminal pane wearing the agent chat's store — what the chat stage runs on.
 *
 * The Agentic IDE's chat stage draws ONE pane with the front page's exact
 * chat: `ChatStage`, its timeline, its composer with the provider, model,
 * effort and permission pills (maintainer, 2026-08-26: the interface we
 * built, not a plain box). Those components read an `AgentChatStore` through
 * `AgentChatStoreProvider`, so a pane gets a store of that shape — this one —
 * with the pane's own answers behind every field:
 *
 * * the timeline is the CLI's transcript, polled and folded through the same
 *   reducer as a chat session's event log (`GET /terminals/{name}/timeline`);
 * * the draft's pills say what the pane RUNS ON, in the CLI's own words —
 *   the model and effort of its last reply, the permission stance its record
 *   declares — where a session the chat runs itself shows its picks;
 * * `send` types the sentence into the pane, verbatim, with the files that
 *   went with it; `cancel` presses Escape there.
 *
 * What a pane cannot do is switch what it runs on from outside: the CLI in
 * the pane chose its provider when it started and keeps its own controls for
 * the rest. A model pick is typed in as the CLI's own `/model` command where
 * the CLI has one; the other picks say where to change them instead of
 * pretending. The catalog, the connections and the model lists are the agent
 * store's — mirrored here so the pickers list what they always list.
 *
 * One store per staged pane, made by the stage and dropped with it.
 */

/** Poll cadence while the agent is working — a tool result every couple of seconds is what it produces. */
export const POLL_WORKING_MS = 2000;
/** Poll cadence once it has stopped — only a new message from elsewhere could change the file. */
export const POLL_IDLE_MS = 6000;

/** How soon the next read follows, from what the last one said about the pane. */
export function pollIntervalFor(live: boolean, activity: PaneActivity): number {
  return live || activity === "working" || activity === "starting"
    ? POLL_WORKING_MS
    : POLL_IDLE_MS;
}

/**
 * A cheap fingerprint of an event list, so an unchanged poll re-renders nothing.
 *
 * The file only ever grows, and a record is written whole (a finished block,
 * a tool result), so length plus the last event's identity plus the text
 * volume tells the two lists apart — without folding sixty turns to find out
 * that nothing happened.
 */
export function eventsSignature(events: AgentChatEvent[], live: boolean): string {
  const last = events[events.length - 1];
  let chars = 0;
  for (const ev of events) {
    const p = ev.payload;
    if (typeof p.text === "string") chars += p.text.length;
    if (typeof p.output === "string") chars += p.output.length;
  }
  return `${events.length}:${last?.seq ?? 0}:${last?.ts_ms ?? 0}:${last?.kind ?? ""}:${chars}:${live ? 1 : 0}`;
}

/**
 * Which catalog provider a pane's CLI is.
 *
 * By the RUNNER, not by a table of names: the catalog's CLI rows name the
 * binary they run (`claude-cli`, `codex-cli`, `agy-cli`, `grok-cli`), and a
 * pane names its agent the same way (`claude`, `codex`, `agy`, `grok`). A CLI
 * added later matches on the same rule.
 */
export function providerForAgent(
  agent: string,
  providers: readonly ProviderOption[],
): ProviderOption | null {
  const key = agent.trim().toLowerCase();
  if (!key) return null;
  const aliases = key === "antigravity" ? ["agy", "antigravity"] : [key];
  return (
    providers.find((p) =>
      aliases.some((a) => p.runner === `${a}-cli` || p.runner === a || p.id === a),
    ) ?? null
  );
}

/** The timeline with every turn's byline pointing at the catalog provider. */
function withProvider(timeline: Timeline, providerId: string): Timeline {
  if (!providerId) return timeline;
  const items: TimelineItem[] = timeline.items.map((item) =>
    item.type === "turn" && item.provider !== providerId ? { ...item, provider: providerId } : item,
  );
  return { ...timeline, items };
}

export interface PaneChatStoreOptions {
  terminal: string;
  workspaceId: string;
  /**
   * The pane's lifetime id — the id of the session OBJECT the chat components
   * see. Never handed out as `activeSessionId`: that is an agent-chat session
   * id, and a pane has none (see the store body).
   */
  historyId: string;
  agent: string;
  /** What the CLI is called — "Claude Code" — the session's title. */
  displayName: string;
  /** The workspace folder: the composer's folder chip and the empty page's headline. */
  folder: string;
}

export interface PaneChatState extends AgentChatStore {
  /** The pane's own facts, beside the chat store's fields. */
  pane: {
    terminal: string;
    workspaceId: string;
    agent: string;
    /** Null until the first answer; false is a settled "this CLI keeps no record". */
    readable: boolean | null;
    available: boolean;
    live: boolean;
    activity: PaneActivity;
    /** True until the first answer for this pane has arrived. */
    loading: boolean;
    /** The last failed read, "" while reads succeed. */
    pollError: string;
  };
  /** Read the transcript again now — the composer's send calls it. */
  reload: () => void;
  /** Start polling (the stage mounts) / stop (the stage unmounts). */
  start: () => void;
  stop: () => void;
}

export type PaneChatStoreHook = ReturnType<typeof createPaneChatStore>;

function toast(kind: "info" | "warning" | "error", message: string): void {
  useEventStore.getState().pushToast(kind, message);
}

export function createPaneChatStore(options: PaneChatStoreOptions) {
  const agentStore = useAgentSessionStore;
  let timer: number | null = null;
  let running = false;
  let signature = "";
  const lastReported = { model: "" };
  let unsubscribe: (() => void) | null = null;

  const mirror = () => {
    const src = agentStore.getState();
    return {
      catalog: src.catalog,
      connections: src.connections,
      catalogError: src.catalogError,
      backendOutdated: src.backendOutdated,
      liveModels: src.liveModels,
      health: src.health,
    };
  };

  const session = (patch: Partial<AgentChatSession> = {}): AgentChatSession => ({
    session_id: options.historyId,
    title: options.displayName,
    provider: "",
    model: "",
    effort: "",
    cwd: options.folder,
    permission_mode: "",
    surface: "agent",
    vendor_session: null,
    created_ms: 0,
    updated_ms: 0,
    message_count: 0,
    preview: "",
    running: false,
    ...patch,
  });

  const store = create<PaneChatState>((set, get) => {
    const providerId = () =>
      providerForAgent(options.agent, agentStore.getState().providerOptions())?.id ?? "";

    const apply = (res: TerminalTimelineResponse) => {
      const pid = providerId();
      // A model picked here is typed into the pane and shows up in the record
      // only with the CLI's next reply; until the record SAYS something new,
      // the pill keeps the pick rather than flipping back for a poll or two.
      const reportedBefore = lastReported.model;
      lastReported.model = res.model;
      const model = res.model !== reportedBefore ? res.model : get().draft.model || res.model;
      const next: Partial<PaneChatState> = {
        pane: {
          ...get().pane,
          readable: res.readable,
          available: res.available,
          live: res.live,
          activity: res.activity,
          loading: false,
          pollError: "",
        },
        draft: {
          ...get().draft,
          provider: pid,
          model,
          effort: res.effort,
          permissionMode: res.permission_mode,
          buildMode: res.permission_mode === "plan" ? get().draft.buildMode : res.permission_mode,
        },
        activeSession: session({
          provider: pid,
          model,
          effort: res.effort,
          permission_mode: res.permission_mode,
          running: res.live,
        }),
      };
      const sig = eventsSignature(res.events, res.live);
      if (sig !== signature) {
        signature = sig;
        next.timeline = withProvider(reduceEvents(EMPTY_TIMELINE, res.events), pid);
      }
      set(next);
      return pollIntervalFor(res.live, res.activity);
    };

    const tick = async () => {
      if (!running) return;
      let next = POLL_IDLE_MS;
      try {
        const res = await fetchTerminalTimeline(options.terminal, options.workspaceId);
        if (!running) return;
        next = apply(res);
      } catch (reason: unknown) {
        if (!running) return;
        // Keep what is on screen: a column that empties itself on one failed
        // poll would read as "the conversation is gone".
        set({
          pane: {
            ...get().pane,
            loading: false,
            pollError: reason instanceof Error ? reason.message : String(reason),
          },
        });
      }
      if (running) timer = window.setTimeout(() => void tick(), next);
    };

    const restart = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      if (running) void tick();
    };

    const cannotPick = (what: string) => {
      toast("info", translate("agentic_grid.pane_chat.pick_in_terminal").replace("{0}", what));
    };

    return {
      surface: "agent",
      ...mirror(),
      sessions: [],
      // No agent-chat session stands behind a pane: the chat's session store
      // has never heard of a pane's history id, and handing it out as one
      // sent the composer's file attach to `/agent-chat/attachments` with a
      // `session_id` the backend could only answer with 404 "session not
      // found" (2026-08-27). Null means "attach by folder", which is where a
      // pane's files belong anyway; the session object beside it still names
      // the pane for the stage.
      activeSessionId: null,
      activeSession: session(),
      timeline: EMPTY_TIMELINE,
      socketState: "open",
      draft: {
        provider: providerId(),
        model: "",
        effort: "",
        permissionMode: "",
        buildMode: "",
        cwd: options.folder,
      },
      busy: false,
      lastError: null,
      pane: {
        terminal: options.terminal,
        workspaceId: options.workspaceId,
        agent: options.agent,
        readable: null,
        available: false,
        live: false,
        activity: "",
        loading: true,
        pollError: "",
      },

      loadCatalog: async () => {
        await agentStore.getState().loadCatalog();
        set({ ...mirror(), draft: { ...get().draft, provider: providerId() } });
      },
      loadSessions: async () => undefined,
      loadModels: (id) => agentStore.getState().loadModels(id),
      loadHealth: async (refresh) => {
        await agentStore.getState().loadHealth(refresh);
        set(mirror());
      },
      providerOptions: () => agentStore.getState().providerOptions(),
      providerById: (id) => agentStore.getState().providerById(id),

      setDraft: async (patch: Partial<ComposerDraft>) => {
        // What the pane runs on is the CLI's to change. A model pick is typed
        // in where the CLI takes it as a command; everything else says where
        // to go rather than showing a pick the pane never received.
        if (patch.provider !== undefined && patch.provider !== get().draft.provider) {
          cannotPick(translate("agent_chat.pick_provider"));
          return;
        }
        if (patch.model !== undefined && patch.model !== get().draft.model) {
          if (options.agent === "claude" && patch.model) {
            try {
              await promptTerminal(options.terminal, `/model ${patch.model}`, { compose: false });
              set({ draft: { ...get().draft, model: patch.model } });
              get().reload();
            } catch (reason: unknown) {
              set({ lastError: reason instanceof Error ? reason.message : String(reason) });
            }
          } else {
            cannotPick(translate("agent_chat.pick_model"));
          }
          return;
        }
        if (patch.effort !== undefined && patch.effort !== get().draft.effort) {
          // The composer snaps an off-ladder effort to the nearest level on
          // its own; that is bookkeeping, not a pick, and gets no toast.
          const ladder = get().providerById(get().draft.provider)?.effort_levels ?? [];
          if (get().draft.effort && ladder.includes(get().draft.effort)) {
            cannotPick(translate("agent_chat.pick_effort"));
          }
          return;
        }
        if (
          patch.permissionMode !== undefined &&
          patch.permissionMode !== get().draft.permissionMode
        ) {
          cannotPick(translate("agent_chat.pick_permission"));
          return;
        }
        // `cwd` is the workspace's folder; a pane does not move.
      },
      setPlan: async () => {
        cannotPick(translate("agent_chat.plan"));
      },
      newChat: () => undefined,
      openSession: () => undefined,
      removeSession: async () => undefined,

      send: async (text: string, attachments: ChatAttachment[] = []) => {
        if (get().busy) return;
        set({ busy: true, lastError: null });
        try {
          // Verbatim: `compose` off. A brief written FOR the person belongs to
          // the voice path; here the person is talking to the agent themselves.
          const result = await promptTerminal(options.terminal, text, {
            compose: false,
            attachments,
          });
          if (result.submitted === false) {
            toast(
              "warning",
              translate("agentic_grid.pane_chat.not_taken")
                .replace("{0}", options.terminal)
                .replace("{1}", result.detail ?? ""),
            );
          }
          // The pane echoes the line before the CLI records it; polling
          // starts at once so the answer is seen forming.
          get().reload();
        } catch (reason: unknown) {
          set({ lastError: reason instanceof Error ? reason.message : String(reason) });
        } finally {
          set({ busy: false });
        }
      },
      cancel: async () => {
        try {
          await interruptTerminal(options.terminal, options.workspaceId);
          get().reload();
        } catch (reason: unknown) {
          set({ lastError: reason instanceof Error ? reason.message : String(reason) });
        }
      },
      // Approvals never come out of a transcript — the CLI asks in its own TUI.
      decide: async () => undefined,
      ingest: (event: AgentChatEvent) => set({ timeline: reduceEvent(get().timeline, event) }),
      disconnect: () => get().stop(),

      reload: restart,
      start: () => {
        if (running) return;
        running = true;
        unsubscribe = agentStore.subscribe(() => {
          set({ ...mirror(), draft: { ...get().draft, provider: providerId() } });
        });
        void tick();
      },
      stop: () => {
        running = false;
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
        unsubscribe?.();
        unsubscribe = null;
      },
    };
  });

  return store;
}

/** Test seam: a catalog the mirror can read without a backend. */
export type { AgentChatCatalog };
