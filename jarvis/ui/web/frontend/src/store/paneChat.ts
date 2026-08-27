import { create } from "zustand";

import {
  EMPTY_TIMELINE,
  reduceEvent,
  reduceEvents,
  type Timeline,
  type TimelineItem,
} from "@/components/agentchat/reduce";
import {
  applyTerminalPicks,
  fetchTerminalTimeline,
  interruptTerminal,
  promptTerminal,
  type PaneActivity,
  type PaneRuntimePick,
  type RuntimePickOffers,
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
 * By the row's registry key first: a catalog CLI row names the Agentic IDE
 * entry it runs (`agent`), which is exactly what a pane calls its agent. The
 * runner rule stays for an older backend that does not send it — the catalog's
 * CLI rows name the binary they run (`claude-cli`, `codex-cli`, `agy-cli`,
 * `grok-cli`), and a pane names its agent the same way (`claude`, `codex`,
 * `agy`, `grok`). A CLI added later matches on either.
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
      aliases.some(
        (a) => p.agent === a || p.runner === `${a}-cli` || p.runner === a || p.id === a,
      ),
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

/**
 * A message typed here that the CLI's record does not hold yet.
 *
 * The record is the CLI's own transcript, read by polling: the sentence shows
 * up there only once the CLI has taken it, and the next read follows seconds
 * later. The front page's chat hears its own message back over the socket at
 * once; a pane gets the same immediacy from this — the sentence is drawn the
 * moment Send is pressed, exactly as the record will draw it (the message,
 * then an open turn, which is what `agent_transcript` itself writes for an
 * unanswered question on a working pane), and it stays drawn until the record
 * holds a newer message than it did before the send. Then the record's copy
 * takes over and the echo is dropped, so nothing is shown twice.
 */
export interface PendingEcho {
  text: string;
  attachments: ChatAttachment[];
  /** When Send was pressed — the echo's timestamp and its turn's start. */
  atMs: number;
  /** The newest user message the record held before the send; the echo settles once that grows. */
  lastUserTsBefore: number;
}

/** The timestamp of the newest user message in a read, 0 when it holds none. */
export function lastUserMessageTs(events: readonly AgentChatEvent[]): number {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    if (events[i].kind === "user_message") return events[i].ts_ms;
  }
  return 0;
}

/** How many user messages a read holds that are newer than `ts`. */
export function countUserMessagesAfter(events: readonly AgentChatEvent[], ts: number): number {
  let n = 0;
  for (const ev of events) if (ev.kind === "user_message" && ev.ts_ms > ts) n += 1;
  return n;
}

/** The two events a pending echo folds through the chat's reducer as. */
export function echoEvents(
  echo: PendingEcho,
  turn: { provider: string; model: string; effort: string },
): AgentChatEvent[] {
  // `seq` 0: an echo never advances the timeline's cursor — the record's own
  // sequence does that once it holds the message.
  return [
    {
      seq: 0,
      ts_ms: echo.atMs,
      kind: "user_message",
      payload: {
        text: echo.text,
        attachments: echo.attachments.map((a) => ({
          name: a.name,
          kind: a.kind,
          described_by: a.described_by,
        })),
      },
    },
    {
      seq: 0,
      ts_ms: echo.atMs,
      kind: "turn_started",
      payload: {
        turn_id: `echo-${echo.atMs}`,
        provider: turn.provider,
        model: turn.model,
        effort: turn.effort,
        runner: "cli",
      },
    },
  ];
}

/** The three picks a pane runs on, as the draft names them. */
type PickKey = "model" | "effort" | "permissionMode";

/** The same three, as the picks route spells them beside the draft's names. */
const PICK_KEYS: readonly (readonly [PaneRuntimePick, PickKey])[] = [
  ["model", "model"],
  ["effort", "effort"],
  ["permission_mode", "permissionMode"],
];

/** The composer's own name for a pick, for the sentences about it. */
function pickWord(key: PickKey): string {
  return translate(
    key === "model"
      ? "agent_chat.pick_model"
      : key === "effort"
        ? "agent_chat.pick_effort"
        : "agent_chat.pick_permission",
  );
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
  /**
   * A message that was sent to this pane before its chat existed.
   *
   * The Agentic IDE's new chat opens the pane and delivers the first sentence
   * itself (`components/agentic/NewPaneChat`), so by the time this store is
   * built the message is on its way to a CLI that has not written it down yet.
   * Seeding it as a pending echo is what keeps the handover from showing an
   * empty conversation for the five seconds that takes — the same mechanism a
   * message typed HERE uses, and it settles the same way, as soon as the
   * record holds a user message.
   */
  firstMessage?: { text: string; attachments: ChatAttachment[]; atMs: number };
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
  /**
   * What the record last said each pick was, so a pick typed in HERE holds on
   * the pill until the record says something NEW — the CLI writes the change
   * down only with its next reply, and a poll in between would otherwise
   * flip the pill back to the old value for no reason.
   */
  const lastReported: Record<PickKey, string> = { model: "", effort: "", permissionMode: "" };
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

  /** The record as the last read returned it — what the timeline is rebuilt from. */
  let lastEvents: AgentChatEvent[] = [];
  /** Messages typed here that the record does not hold yet, oldest first. */
  let echoes: PendingEcho[] = options.firstMessage
    ? [
        {
          text: options.firstMessage.text,
          attachments: options.firstMessage.attachments,
          atMs: options.firstMessage.atMs,
          // A pane this fresh has no record at all, so ANY user message in the
          // first read is this one arriving.
          lastUserTsBefore: 0,
        },
      ]
    : [];

  const store = create<PaneChatState>((set, get) => {
    const providerId = () =>
      providerForAgent(options.agent, agentStore.getState().providerOptions())?.id ?? "";

    /** The record's timeline, with every pending echo drawn after it. */
    const rebuild = (pid: string): Timeline => {
      const { model, effort } = get().draft;
      const recorded = reduceEvents(EMPTY_TIMELINE, lastEvents);
      const echoed = echoes.reduce(
        (tl, echo) => reduceEvents(tl, echoEvents(echo, { provider: pid, model, effort })),
        recorded,
      );
      return withProvider(echoed, pid);
    };

    /**
     * The sentence on each pill the pane cannot change, from what the backend
     * says its CLI takes while it runs. The provider is always the pane's: the
     * CLI chose it when it started, and a new chat is where another is picked
     * (maintainer, 2026-08-27). A backend from before the picks route answers
     * nothing here; then the model pick goes the way it always did on Claude
     * Code, and the other two say where they change.
     */
    const locksFor = (offers?: RuntimePickOffers): PaneChatState["locks"] => {
      const live = offers ?? {
        model: options.agent === "claude",
        effort: false,
        permission_mode: false,
      };
      const lock = (pick: string) =>
        translate("agentic_grid.pane_chat.lock_pick")
          .replace("{0}", options.displayName)
          .replace("{1}", pick);
      return {
        provider: translate("agentic_grid.pane_chat.lock_provider").replace(
          "{0}",
          options.displayName,
        ),
        ...(live.model ? {} : { model: lock(pickWord("model")) }),
        ...(live.effort ? {} : { effort: lock(pickWord("effort")) }),
        ...(live.permission_mode ? {} : { permissionMode: lock(pickWord("permissionMode")) }),
      };
    };

    const apply = (res: TerminalTimelineResponse) => {
      const pid = providerId();
      // A pick typed in here shows up in the record only with the CLI's next
      // reply; until the record SAYS something new, the pill keeps the pick
      // rather than flipping back for a poll or two.
      const hold = (key: PickKey, reported: string): string => {
        const before = lastReported[key];
        lastReported[key] = reported;
        return reported !== before ? reported : get().draft[key] || reported;
      };
      const model = hold("model", res.model);
      const effort = hold("effort", res.effort);
      const permissionMode = hold("permissionMode", res.permission_mode);
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
          effort,
          permissionMode,
          buildMode: permissionMode === "plan" ? get().draft.buildMode : permissionMode,
        },
        activeSession: session({
          provider: pid,
          model,
          effort,
          permission_mode: permissionMode,
          running: res.live,
        }),
        locks: locksFor(res.runtime_picks),
      };
      const sig = eventsSignature(res.events, res.live);
      if (sig !== signature) {
        signature = sig;
        lastEvents = res.events;
        // The record has caught up with a send when it holds a user message
        // newer than the one it held before that send: the oldest echo is now
        // the record's, and the record draws it from here on. By timestamp,
        // not by count — the read keeps only the last N turns, so a count
        // stands still exactly when the conversation is long.
        // Every pending echo was sent against the same read (the read only
        // moves here), so one base serves them all: each message the record
        // gained past it settles one echo, oldest first.
        if (echoes.length > 0) {
          const gained = countUserMessagesAfter(res.events, echoes[0].lastUserTsBefore);
          echoes = echoes.slice(Math.min(gained, echoes.length));
        }
        next.timeline = rebuild(pid);
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
      locks: locksFor(undefined),
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
      // A handover message is drawn from the first frame; without it the
      // stage would open empty under a sentence the person just sent.
      timeline: options.firstMessage
        ? reduceEvents(
            EMPTY_TIMELINE,
            echoEvents(echoes[0], { provider: providerId(), model: "", effort: "" }),
          )
        : EMPTY_TIMELINE,
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
        // What the pane runs on is the CLI's to change, and it changes it on
        // its own command line: every pick the CLI has a typed command for is
        // sent there (`POST /terminals/{name}/picks`), the pill takes the
        // pick at once, and a pick the pane would not take goes back with
        // the backend's sentence about why. A pick the CLI has no command for
        // is locked on the pill (`locks`) — the sentence there says where it
        // changes — so a click never lands here for it; a stale bundle's
        // click is answered with the same sentence. The provider is the
        // pane's own from the moment it started (maintainer, 2026-08-27).
        const draft = get().draft;
        const locks = get().locks ?? {};
        if (patch.provider !== undefined && patch.provider !== draft.provider) {
          if (locks.provider) toast("info", locks.provider);
          else cannotPick(translate("agent_chat.pick_provider"));
          return;
        }
        // `cwd` is the workspace's folder; a pane does not move.
        const wanted: Partial<Record<PaneRuntimePick, string>> = {};
        for (const [wire, key] of PICK_KEYS) {
          const value = patch[key];
          if (value === undefined || value === draft[key]) continue;
          if (key === "effort") {
            // The composer snaps an off-ladder effort to the nearest level on
            // its own; that is bookkeeping, not a pick, and is not typed in.
            const ladder = get().providerById(draft.provider)?.effort_levels ?? [];
            if (draft.effort && !ladder.includes(draft.effort)) continue;
          }
          const locked = locks[key];
          if (locked) {
            toast("info", locked);
            continue;
          }
          wanted[wire] = value;
        }
        if (!Object.keys(wanted).length) return;

        // The pill reads the pick the moment it is made, as the front page's
        // does; what the pane declines is put back below, with the reason.
        const before = { ...draft };
        const optimistic = { ...draft };
        for (const [wire, key] of PICK_KEYS) {
          const value = wanted[wire];
          if (value !== undefined) optimistic[key] = value;
        }
        if (wanted.permission_mode !== undefined && wanted.permission_mode !== "plan") {
          optimistic.buildMode = wanted.permission_mode;
        }
        set({ draft: optimistic, lastError: null });
        try {
          const result = await applyTerminalPicks(options.terminal, wanted, options.workspaceId);
          const settled = { ...get().draft };
          for (const [wire, key] of PICK_KEYS) {
            const why = result.declined[wire];
            if (!why) continue;
            settled[key] = before[key];
            if (key === "permissionMode") settled.buildMode = before.buildMode;
            toast(
              "warning",
              translate("agentic_grid.pane_chat.pick_not_taken")
                .replace("{0}", pickWord(key))
                .replace("{1}", why),
            );
          }
          set({ draft: settled });
          // The record catches up with the CLI's next reply; read again now so
          // the timeline shows the command having gone in.
          get().reload();
        } catch (reason: unknown) {
          const back = { ...get().draft };
          for (const [wire, key] of PICK_KEYS) if (wanted[wire] !== undefined) back[key] = before[key];
          back.buildMode = before.buildMode;
          set({ draft: back, lastError: reason instanceof Error ? reason.message : String(reason) });
        }
      },
      setPlan: async (on) => {
        const st = get();
        const locked = st.locks?.permissionMode;
        if (locked) {
          toast("info", locked);
          return;
        }
        const back =
          st.draft.buildMode && st.draft.buildMode !== "plan"
            ? st.draft.buildMode
            : (st.providerById(st.draft.provider)?.default_permission_mode ?? "");
        await st.setDraft({ permissionMode: on ? "plan" : back });
      },
      newChat: () => undefined,
      openSession: () => undefined,
      removeSession: async () => undefined,

      send: async (text: string, attachments: ChatAttachment[] = []) => {
        if (get().busy) return;
        // Drawn before anything is sent: the person just pressed Send and the
        // sentence is theirs to see now, not after the pane has typed it, the
        // CLI has taken it, written it down, and a poll has read it back
        // (measured at five seconds, 2026-08-27). See `PendingEcho`.
        const echo: PendingEcho = {
          text,
          attachments,
          atMs: Date.now(),
          lastUserTsBefore: lastUserMessageTs(lastEvents),
        };
        echoes = [...echoes, echo];
        set({ busy: true, lastError: null, timeline: rebuild(providerId()) });
        const withdraw = () => {
          echoes = echoes.filter((e) => e !== echo);
          set({ timeline: rebuild(providerId()) });
        };
        try {
          // Verbatim: `compose` off. A brief written FOR the person belongs to
          // the voice path; here the person is talking to the agent themselves.
          const result = await promptTerminal(options.terminal, text, {
            compose: false,
            attachments,
          });
          if (result.submitted === false) {
            // The text sits in the pane's input box, not in its conversation;
            // a bubble saying otherwise would be the lie the toast corrects.
            withdraw();
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
          withdraw();
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
