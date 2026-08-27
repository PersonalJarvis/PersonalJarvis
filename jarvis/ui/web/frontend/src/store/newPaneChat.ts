import { create } from "zustand";

import { EMPTY_TIMELINE, type Timeline } from "@/components/agentchat/reduce";
import type { AgentLaunchPicks, SplitAgentChoice } from "@/components/agentic/AgentPicker";
import type { AgentChatCatalog, AgentChatProvider, AgentChatSession } from "@/lib/agentChatApi";
import type { ChatAttachment } from "@/lib/agentChatApi";
import { type AgentChatStore, type ComposerDraft, type ProviderOption } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import { translate } from "@/i18n";

/**
 * A chat that has no agent behind it YET — the Agentic IDE's new chat.
 *
 * Everywhere else in this app a chat surface is opened ON something: a pane
 * that is already running, a session that already exists. Chat mode used to be
 * the same, and that is what made starting one awkward — the CLI had to be
 * chosen from a menu before there was anywhere to type, and the model, the
 * effort and the permission stance could not be chosen at all, because by then
 * the process was already running on the vendor's defaults (maintainer report,
 * 2026-08-27: a new chat should be an EMPTY chat window where the coding agent
 * and its model are picked in the interface).
 *
 * So this store is the chat before the agent: it wears the same
 * `AgentChatStore` shape the stage and the composer read, its picker lists the
 * coding CLIs the Agentic IDE has connected on this machine rather than the
 * chat catalog's providers, and its picks are ordinary local state that
 * nothing has to be asked about — because nothing is running to ask.
 *
 * `send` is where the chat becomes real: the pane is opened WITH those picks
 * (`POST /terminals`, which puts them on the CLI's command line) and the first
 * message goes into it. From that moment the pane's own store takes over and
 * this one is dropped — a draft is a thing you use once.
 */

/** How one CLI's launch picks reach the composer, as a catalog row. */
export function agentAsProvider(agent: SplitAgentChoice): ProviderOption {
  const picks: AgentLaunchPicks = agent.picks ?? {
    models: [],
    defaultModel: "",
    effortLevels: [],
    defaultEffort: "",
    permissionModes: [],
    defaultPermissionMode: "",
  };
  const row: AgentChatProvider = {
    id: agent.name,
    label: agent.displayName,
    // The mark is `AgentMark`'s, so the family is only ever a search word here.
    family: agent.name,
    // Every row in this list runs a vendor CLI in a terminal; saying so is what
    // files them under "Coding CLIs" in the composer's own grouping.
    runner: "cli",
    models_source: "curated",
    curated_models: picks.models,
    default_model: picks.defaultModel,
    keyless: false,
    native_resume: false,
    effort_levels: picks.effortLevels,
    default_effort: picks.defaultEffort,
    permission_modes: picks.permissionModes,
    default_permission_mode: picks.defaultPermissionMode,
    cli_installed: agent.installed,
    // Nothing is running to complete against: a "/" here would open a list of
    // commands belonging to a process that does not exist yet.
    typeahead: [],
  };
  return {
    ...row,
    // "Connected" is simply "installed" for a CLI seat: a coding CLI spends a
    // subscription login it manages itself, and this app has no key to check.
    connected: agent.installed,
    active: false,
    agentMark: agent.name,
    logoUrl: agent.logoUrl,
  };
}

/**
 * Which entries a NEW chat may be started on.
 *
 * Every coding CLI the Agentic IDE knows, installed or not — an entry that is
 * missing stays listed and disabled with the composer's own "not installed"
 * hint, so its absence is visible and explains itself. Two kinds are left out
 * because there would be nothing to say to them: a plain terminal is a shell
 * prompt rather than an agent, and an entry whose interface lives elsewhere
 * (DeepSeek Harness puts its chat in the browser) never reads what is typed
 * into its pane.
 */
export function chatStartableAgents(agents: readonly SplitAgentChoice[]): SplitAgentChoice[] {
  return agents.filter(
    (agent) => (agent.kind ?? "cli") === "cli" && (agent.acceptsPrompts ?? true),
  );
}

/** The entry a fresh draft starts on: the first installed one, else the first. */
export function firstStartableAgent(agents: readonly SplitAgentChoice[]): SplitAgentChoice | null {
  const startable = chatStartableAgents(agents);
  return startable.find((agent) => agent.installed) ?? startable[0] ?? null;
}

/** What `send` hands back to the surface that owns the workspace. */
export interface NewPaneRequest {
  agent: string;
  model: string;
  effort: string;
  permissionMode: string;
  text: string;
  attachments: ChatAttachment[];
}

export interface NewPaneChatOptions {
  /** The workspace folder — the empty page's headline and the pane's cwd. */
  folder: string;
  /** Every entry the backend registered, as the split menus receive them. */
  agents: readonly SplitAgentChoice[];
  /**
   * Open the pane and deliver the first message.
   *
   * Owned by the grid rather than performed here: opening a pane rearranges a
   * workspace, waits for the CLI's input line and moves the stage — all of
   * which belong to the surface that draws them. A rejection leaves the draft
   * exactly as it was, so the sentence someone typed is never lost to a failed
   * spawn.
   */
  open: (request: NewPaneRequest) => Promise<void>;
}

export interface NewPaneChatState extends AgentChatStore {
  /** Every CLI a chat may be started on, in the picker's order. */
  startable: SplitAgentChoice[];
}

export type NewPaneChatStoreHook = ReturnType<typeof createNewPaneChatStore>;

/** The picks a fresh draft opens with for one entry. */
export function seedDraft(agent: SplitAgentChoice | null, folder: string): ComposerDraft {
  const picks = agent?.picks;
  const permission = picks?.defaultPermissionMode ?? "";
  return {
    provider: agent?.name ?? "",
    model: picks?.defaultModel ?? "",
    effort: picks?.defaultEffort ?? "",
    permissionMode: permission,
    // Where Plan switches back TO. A stance of "" means the CLI's own, which
    // is the right thing to return to as well.
    buildMode: permission === "plan" ? "" : permission,
    cwd: folder,
  };
}

export function createNewPaneChatStore(options: NewPaneChatOptions) {
  const startable = chatStartableAgents(options.agents);
  const rows: ProviderOption[] = startable.map(agentAsProvider);
  const catalog: AgentChatCatalog = {
    providers: rows,
    default_cwd: options.folder,
    shell: "",
  };
  const first = firstStartableAgent(options.agents);

  const session = (draft: ComposerDraft): AgentChatSession => ({
    // No id: nothing has been created, and handing out a made-up one would
    // send the composer's file attach to a session the backend cannot find.
    session_id: "",
    title: startable.find((a) => a.name === draft.provider)?.displayName ?? "",
    provider: draft.provider,
    model: draft.model,
    effort: draft.effort,
    cwd: options.folder,
    permission_mode: draft.permissionMode,
    surface: "agent",
    vendor_session: null,
    created_ms: 0,
    updated_ms: 0,
    message_count: 0,
    preview: "",
    running: false,
  });

  return create<NewPaneChatState>((set, get) => {
    const initial = seedDraft(first, options.folder);
    return {
      surface: "agent",
      startable,
      catalog,
      connections: [],
      catalogError: null,
      backendOutdated: false,
      liveModels: {},
      health: {},
      sessions: [],
      activeSessionId: null,
      activeSession: session(initial),
      timeline: EMPTY_TIMELINE as Timeline,
      socketState: "open",
      draft: initial,
      busy: false,
      lastError: null,

      // The list is handed in whole, from the poll the IDE already runs; there
      // is no second catalog to fetch and nothing to wait for.
      loadCatalog: async () => undefined,
      loadSessions: async () => undefined,
      loadModels: async () => undefined,
      loadHealth: async () => undefined,
      providerOptions: () => rows,
      providerById: (id) => rows.find((row) => row.id === id) ?? null,

      setDraft: async (patch: Partial<ComposerDraft>) => {
        const current = get().draft;
        let next: ComposerDraft = { ...current, ...patch };
        // Changing the CLI re-seats the other three on the new one's defaults:
        // a model, a ladder step and a stance belong to one CLI's vocabulary,
        // and carrying them across would leave picks the next CLI cannot take.
        if (patch.provider !== undefined && patch.provider !== current.provider) {
          const agent = startable.find((a) => a.name === patch.provider) ?? null;
          next = { ...seedDraft(agent, options.folder), ...stripPicks(patch) };
        }
        set({ draft: next, activeSession: session(next) });
      },

      setPlan: async (on: boolean) => {
        const current = get().draft;
        const next: ComposerDraft = on
          ? { ...current, permissionMode: "plan", buildMode: current.buildMode }
          : { ...current, permissionMode: current.buildMode };
        set({ draft: next, activeSession: session(next) });
      },

      newChat: () => set({ draft: seedDraft(first, options.folder) }),
      openSession: () => undefined,
      removeSession: async () => undefined,

      send: async (text: string, attachments: ChatAttachment[] = []) => {
        const draft = get().draft;
        if (get().busy) return;
        if (!draft.provider) {
          set({ lastError: translate("agentic_grid.new_chat.pick_an_agent") });
          return;
        }
        set({ busy: true, lastError: null });
        try {
          await options.open({
            agent: draft.provider,
            model: draft.model,
            effort: draft.effort,
            permissionMode: draft.permissionMode,
            text,
            attachments,
          });
        } catch (reason: unknown) {
          const message = reason instanceof Error ? reason.message : String(reason);
          set({ lastError: message });
          useEventStore.getState().pushToast("error", message);
        } finally {
          set({ busy: false });
        }
      },

      // Nothing is running: there is no turn to stop, no approval to answer
      // and no socket to close. Each answers rather than throwing, because the
      // shared composer calls them without asking which surface it is on.
      cancel: async () => undefined,
      decide: async () => undefined,
      ingest: () => undefined,
      disconnect: () => undefined,
    };
  });
}

/** A provider change's patch, minus the fields the re-seat has just decided. */
function stripPicks(patch: Partial<ComposerDraft>): Partial<ComposerDraft> {
  const { provider, model, effort, permissionMode, buildMode } = patch;
  return {
    ...(provider !== undefined ? { provider } : {}),
    ...(model !== undefined ? { model } : {}),
    ...(effort !== undefined ? { effort } : {}),
    ...(permissionMode !== undefined ? { permissionMode } : {}),
    ...(buildMode !== undefined ? { buildMode } : {}),
  };
}
