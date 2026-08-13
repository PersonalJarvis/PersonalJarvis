/**
 * Agentic IDE — the "code with Jarvis" section.
 *
 * Two states in one view:
 *
 * * no workspace open → a five-step wizard: folder, how many terminals, an
 *   aggregate agent allocation, grid or chat view, then start,
 * * workspace open → the terminal grid (see AgenticGrid).
 *
 * The wizard order is deliberate and matches how the decision actually gets
 * made: you know the folder first, the number of panes second, then which agent
 * sits in which pane, and last how you want to read them. Every step is
 * reversible and nothing is started until the last one.
 *
 * i18n note: the section label and view header go through the locale files; the
 * panel copy inside is still English-only source awaiting its i18n keys.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useEventStore } from "@/store/events";
import {
  AgenticGrid,
  rememberViewMode,
  storedViewMode,
  type WorkspaceView,
} from "@/components/agentic/AgenticGrid";
import {
  VoiceBubble,
  storedVoiceBubbleOpen,
  storeVoiceBubbleOpen,
} from "@/components/agentic/VoiceBubble";
import {
  WorkspaceLauncher,
  type PlannedTerminal,
} from "@/components/agentic/WorkspaceLauncher";
import { Button } from "@/components/agentic/controls";
import {
  type AgentAccount,
  type AgentAccountsResponse,
  fetchAgentAccounts,
  groupFor,
} from "@/lib/agentAccountsApi";
import { openScratchProject } from "@/lib/chatLibraryApi";
import { TopBarActions } from "@/components/layout/TopBar";
import { WorkspaceBar } from "@/components/agentic/WorkspaceBar";
import {
  isEmptyPayload,
  type PaneDropPayload,
} from "@/components/agentic/paneDrop";
import {
  activateWorkspace,
  attachToTerminal,
  closeWorkspace,
  endIdeSession,
  fetchIdeAgents,
  fetchIdeState,
  fetchResumeOffer,
  forgetResumeOffer,
  renameWorkspace,
  resumeWorkspace,
  setFocusMode,
  startIdeSession,
  type AgentsResponse,
  type IdeAccountState,
  type IdeState,
  type ResumeOffer,
  type SessionState,
  type WorkspaceCard,
} from "@/lib/agenticIdeApi";

/**
 * Terminal plan for ``count`` panes, preserving whatever the user already chose
 * for the panes that still exist.
 *
 * A pure function called from the click handlers on purpose. The first version
 * of this was a `useEffect` that synchronised `planned` against `count` — and
 * one of its dependencies was `meta?.suggested_names ?? []`, a fresh array on
 * every render. Setting state from an effect whose dependency is recreated each
 * render is an infinite loop; it froze the tab and only showed up because a test
 * ran out of heap. Deriving on the event that actually changes the plan has no
 * such failure mode.
 */
function buildPlan(
  count: number,
  previous: PlannedTerminal[],
  agent: string,
  names: string[],
): PlannedTerminal[] {
  return Array.from({ length: count }, (_, i) => {
    const kept = previous[i];
    if (kept) return kept;
    return { agent, name: names[i] ?? `T${i + 1}` };
  });
}

export interface AgenticIdeViewProps {
  /**
   * Is this view the section currently on screen?
   *
   * Unlike every other section, this one is HIDDEN rather than unmounted when
   * the user goes elsewhere (see MainView) — its panes are live terminals, and
   * tearing them down to rebuild them seconds later is what put an onboarding
   * wizard in front of a running workspace. Being hidden with CSS means the
   * view cannot measure whether anyone can see it, so the shell says so.
   *
   * Defaults to true: rendered on its own (tests, any future embedding), a view
   * that is there IS on screen, which is how this behaved before it could hide.
   */
  onScreen?: boolean;
}

export function AgenticIdeView({ onScreen = true }: AgenticIdeViewProps) {
  const pushToast = useEventStore((s) => s.pushToast);
  const setActiveSection = useEventStore((s) => s.setActiveSection);

  const [loading, setLoading] = useState(true);
  const [meta, setMeta] = useState<AgentsResponse | null>(null);
  const [session, setSession] = useState<SessionState | null>(null);
  const [promptTarget, setPromptTarget] = useState("");
  const [focusMode, setFocus] = useState(false);
  const [busy, setBusy] = useState(false);
  /*
   * The floating voice bubble — held HERE, not in the grid, because the
   * conversation belongs to the app: the grid is keyed by workspace, and a
   * bubble mounted inside it would reset the orb mid-sentence on every tab
   * switch (the same reason the old voice column lived outside the grid).
   * The choice survives restarts so the bubble greets its user where they
   * left it.
   */
  const [voiceOpen, setVoiceOpen] = useState(storedVoiceBubbleOpen);
  const toggleVoiceBubble = useCallback(() => {
    setVoiceOpen((current) => {
      storeVoiceBubbleOpen(!current);
      return !current;
    });
  }, []);
  const closeVoiceBubble = useCallback(() => {
    storeVoiceBubbleOpen(false);
    setVoiceOpen(false);
  }, []);

  // Every open workspace, for the bar above. The one on screen is `session`;
  // these are the others, still running, one click away.
  const [workspaces, setWorkspaces] = useState<WorkspaceCard[]>([]);
  const [maxWorkspaces, setMaxWorkspaces] = useState<number | null>(6);
  // Which subscription new terminals open on, per coding CLI. Carried on the
  // workspace state rather than fetched separately, so the toolbar and the
  // panes can never disagree about which plan the next one will spend.
  const [ideAccounts, setIdeAccounts] = useState<IdeAccountState[]>([]);
  /*
   * The wizard is showing for an ADDITIONAL workspace.
   *
   * Kept as its own flag rather than inferred from `session === null`, because
   * the two mean different things once several workspaces exist: no session can
   * also be "everything was closed", and the bar has to know whether the +
   * button is the selected tab. Pressing + clears the front on the BACKEND too
   * (see `startAdding`), which is what stops the outgoing panes from being read
   * as a close.
   */
  const [addingNew, setAddingNew] = useState(false);

  const [folder, setFolder] = useState<string | null>(null);
  const [count, setCount] = useState(2);
  const [planned, setPlanned] = useState<PlannedTerminal[]>([]);
  // Grid or chat, the wizard's last decision. Starts from the remembered
  // reading preference so returning users see their usual answer preselected;
  // it is written back only when a workspace actually opens (see `start`).
  const [view, setView] = useState<WorkspaceView>(
    () => storedViewMode() ?? "grid",
  );
  // The registered subscriptions per CLI. Only ever used to OFFER a choice, so
  // a failed load costs the picker, never the wizard: with none loaded every
  // pane simply opens on the active account, exactly as before.
  const [accounts, setAccounts] = useState<AgentAccountsResponse | null>(null);
  // The workspace that was open when the window last closed, if it can come
  // back. Null both when there is nothing to offer and after the user has
  // answered the offer either way.
  const [offer, setOffer] = useState<ResumeOffer | null>(null);

  /**
   * Re-read the agent sweep alone, without disturbing the workspace state.
   *
   * The user just added, edited or removed a CLI of their own, and that is the
   * ONE thing this read answers differently than a second ago. Going through
   * `refresh()` would also re-read the session and the workspace list — several
   * hundred milliseconds of work whose answer has not changed, and one of them
   * closes the wizard when it finds a workspace open.
   */
  const reloadAgents = useCallback(
    () =>
      fetchIdeAgents().then(setMeta, () => {
        /* Same bargain as the sweep in `refresh`: the entry is already stored
           and registered, so a failed re-read costs an out-of-date list, not
           the change itself. */
      }),
    [],
  );

  const refresh = useCallback(async () => {
    /*
     * Both reads start together, but the workspace is applied WITHOUT waiting
     * for the agent sweep.
     *
     * `/agents` starts one subprocess per registered CLI — 1.1-1.4 s on a cold
     * cache, and on Windows every one of those is an npm shim (see
     * `_DETECTION_TTL_S` in jarvis/workspace/agents.py). Awaiting the two
     * together meant a running workspace could not be drawn until a question
     * about which CLIs are installed had been answered, which is a question the
     * grid never asks. The wizard needs the answer; the grid does not, and the
     * grid is what the user is coming back to.
     */
    const sweep = fetchIdeAgents().then(setMeta, () => {
      // Swallowed on purpose, and it costs exactly what it did when this was
      // one `Promise.all`: `meta` stays as it was, so the wizard opens without
      // its install callouts and the agent step offers whatever it had before.
      // A failed sweep must not take the workspace state down with it — that
      // state is the half of this read that the user is here for.
    });
    try {
      const state = await fetchIdeState();
      setSession(state.session);
      setWorkspaces(state.workspaces ?? []);
      setMaxWorkspaces(
        state.max_workspaces === undefined ? 6 : state.max_workspaces,
      );
      setIdeAccounts(state.accounts ?? []);
      setFocus(Boolean(state.session?.focus_mode));
      // The backend is the authority on whether a workspace is on screen. It
      // says so by carrying one in `session`, so a fetch that finds one ends
      // the "adding" state — otherwise a workspace opened in another tab (or
      // by voice) would come up behind a wizard nobody asked for.
      if (state.session !== null) setAddingNew(false);
      // Only worth asking about when NOTHING is open. With a workspace around,
      // its panes reconnect and continue by themselves — the offer is for the
      // case where the workspaces themselves did not survive.
      if ((state.workspaces ?? []).length === 0) {
        try {
          const previous = await fetchResumeOffer();
          setOffer(previous.workspaces.length > 0 ? previous : null);
        } catch {
          /* no offer is a perfectly good answer — the wizard still works */
        }
      } else {
        setOffer(null);
      }
    } catch {
      /* backend still warming or headless — keep whatever we had */
    } finally {
      // What `loading` gates is the WIZARD (see the render below), and a wizard
      // shown before the sweep lands is one whose agent step has nothing to
      // offer — so the placeholder holds until it is in. A workspace that is
      // already open never waits for this: the running branch is chosen before
      // `loading` is read at all.
      await sweep;
      setLoading(false);
    }
  }, []);

  /** Apply a state the backend just handed back, without a second round-trip. */
  const applyState = useCallback((state: IdeState) => {
    setSession(state.session);
    setWorkspaces(state.workspaces ?? []);
    setMaxWorkspaces(
      state.max_workspaces === undefined ? 6 : state.max_workspaces,
    );
    setFocus(Boolean(state.session?.focus_mode));
    // Absent on a backend that predates the switcher — keeping what we had
    // beats blanking the toolbar over a field that simply was not sent.
    if (state.accounts) setIdeAccounts(state.accounts);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Loaded once, and deliberately NOT part of `refresh`: the wizard must open
  // even when this fails. Without it every pane opens on the active account,
  // which is exactly what happened before the switcher existed.
  useEffect(() => {
    fetchAgentAccounts()
      .then(setAccounts)
      .catch(() => setAccounts(null));
  }, []);

  /*
   * A state the workspace's settings panel handed back.
   *
   * Same as `applyState`, plus a re-read of the subscription list: that panel
   * can ADD an account, and the wizard's per-pane picker was loaded once on
   * mount — without this the next workspace would be offered a list one account
   * short until the app is reloaded.
   */
  const applyStateFromSettings = useCallback(
    (state: IdeState) => {
      applyState(state);
      fetchAgentAccounts()
        .then(setAccounts)
        .catch(() => undefined);
    },
    [applyState],
  );

  /** The registered subscriptions of one CLI, for the wizard's per-pane picker. */
  const accountsFor = useCallback(
    // No id guard: the backend already answers only for the CLIs that HAVE
    // switchable subscriptions, so a list here could only ever disagree with
    // it — and it would disagree silently, by hiding a picker for a CLI whose
    // seats the API just returned.
    (platform: string): AgentAccount[] =>
      groupFor(accounts, platform)?.accounts ?? [],
    [accounts],
  );

  /*
   * Panes can appear without this view asking for them.
   *
   * Saying "spawn five more Claude Code terminals" adds them in the backend; the
   * grid below renders whatever the last fetch returned, so without this listener
   * the panes exist, their agents are told to start — and the user sees nothing.
   * The event carries the new call-signs, but they are deliberately ignored here:
   * re-fetching keeps ONE source of truth for the layout instead of patching the
   * session object from a payload that could be a step behind.
   */
  useEffect(() => {
    const onChanged = () => void refresh();
    window.addEventListener("jarvis:agentic-ide-changed", onChanged);
    return () =>
      window.removeEventListener("jarvis:agentic-ide-changed", onChanged);
  }, [refresh]);

  /*
   * Being in this section IS the coding mode.
   *
   * Focus mode started as a switch you had to remember to flip, and the result
   * was predictable: the workspace was open, agents were running, and Jarvis
   * still answered as the general-purpose assistant because nobody had pressed
   * the button. Standing in a room full of running coding agents and having to
   * announce that you are now coding is not a mode, it is paperwork. So opening
   * a workspace turns it on.
   *
   * The switch stays, and it stays honest: turning it OFF here is respected for
   * as long as this workspace is open (`optedOutRef`), so "I want the normal
   * assistant for a minute" works and does not get overridden a second later.
   */
  const optedOutRef = useRef(false);
  /*
   * Was the mode switched on BY this screen, or by the user?
   *
   * It decides what happens when the section is left. An auto-enabled mode is
   * on loan — it belongs to this screen and goes back when you walk away. A
   * mode the user switched on by hand is theirs, and stays on everywhere, which
   * is how "ask Jarvis about my terminals from the kitchen" keeps working.
   *
   * Without this distinction the mode simply never turned off: this view
   * switched it on, nothing switched it off, and because the view is sticky
   * (see MainView) there was not even an unmount to hang the cleanup on. The
   * result was an assistant permanently in coding mode on every screen, with
   * only a small badge admitting it.
   */
  const autoEnabledRef = useRef(false);
  const [modeIntroFor, setModeIntroFor] = useState<string | null>(null);

  /*
   * How wide the workspace will be — measured here, in the wizard, because
   * the preview must promise the arrangement the grid will actually produce.
   *
   * Width is what stops panes becoming unreadably narrow, and it is the ONLY
   * thing that wraps a workspace: which panes share a row is otherwise the
   * user's own choice, made through the split buttons. A preview computing
   * columns from the count alone drifts apart from the running grid. The
   * wizard shell sits in the same slot the grid will occupy, so measuring it
   * here answers the same question.
   *
   * The readout names the width condition rather than silently assuming it —
   * see WorkspaceShape. Measuring makes the picture correct for the window it
   * was shown in; saying so is what keeps it honest after a resize.
   */
  const shellRef = useRef<HTMLDivElement | null>(null);
  const [shellWidth, setShellWidth] = useState(0);
  useEffect(() => {
    const node = shellRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    setShellWidth(node.clientWidth);
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? node.clientWidth;
      // Same 16 px step the grid rounds to, so both sides flip at one width.
      setShellWidth(Math.round(width / 16) * 16);
    });
    observer.observe(node);
    return () => observer.disconnect();
    // Both states decide WHICH element (if any) carries `shellRef`, so both have
    // to re-run the measurement. `loading` is easy to forget and silent when it
    // is: the first read finds no node, gives up, and the preview then promises
    // an arrangement computed from a width of zero for the rest of the session.
  }, [session, loading]);

  useEffect(() => {
    // Gated on the LOCAL flag, not `session.focus_mode`: `session` is only
    // refetched by `refresh`, so after this effect's counterpart hands the mode
    // back on leaving, the cached session still claims the mode is on and
    // returning to the section would never switch it on again. The local flag
    // is the one that tracks what actually happened — and it is trustworthy
    // here because `session` is non-null only once a refresh has landed, and
    // that same refresh sets the flag from the backend.
    if (!onScreen || !session || focusMode || optedOutRef.current) return;
    let cancelled = false;
    void (async () => {
      try {
        const on = await setFocusMode(true);
        if (cancelled) return;
        setFocus(on);
        autoEnabledRef.current = on;
        // First workspace on this machine: say once what just changed. A mode
        // that switches silently is indistinguishable from a bug.
        if (on && !hasSeenModeIntro()) setModeIntroFor(session.id);
      } catch {
        /* the workspace still works; the mode toggle stays available */
      }
    })();
    return () => {
      cancelled = true;
    };
    // `onScreen` belongs here: this view is sticky and stays mounted, so coming
    // back to the section is a prop change, not a remount. Without it the mode
    // would be given back on leaving and never picked up again on return.
  }, [session, onScreen, focusMode]);

  /*
   * Leaving the section gives the mode back.
   *
   * The signal is `onScreen`, not an unmount — MainView keeps this view mounted
   * on purpose so a dozen terminals survive a trip to Settings, which means
   * there is no unmount to clean up on. Only an auto-enabled mode is returned;
   * one the user switched on by hand is left alone.
   *
   * Failure is deliberately silent. This runs while the user is already looking
   * at another section, and a toast about a mode belonging to the screen they
   * just left would be noise pointing at nothing. The app-wide badge keeps
   * telling the truth either way, because it re-reads the backend on every
   * section change (useCodingMode, path 4).
   */
  useEffect(() => {
    if (onScreen) return;
    if (!autoEnabledRef.current) return;
    autoEnabledRef.current = false;
    void setFocusMode(false)
      .then(() => setFocus(false))
      .catch(() => {});
  }, [onScreen]);

  // Memoised so these arrays keep a stable identity across renders — see the
  // note on buildPlan for what an unstable one costs.
  const agents = useMemo(() => meta?.agents ?? [], [meta]);
  const suggested = useMemo(() => meta?.suggested_names ?? [], [meta]);
  const installed = useMemo(() => agents.filter((a) => a.installed), [agents]);
  const defaultAgent = installed[0]?.name ?? "claude";
  const maxTerminals = meta?.max_terminals ?? 12;

  // What the grid's split menus offer — every entry the backend registered, so
  // the coding CLIs and the plain terminal, and anything registered later
  // without a change here. Uninstalled entries stay in the list but disabled,
  // so their absence is visible rather than silently missing.
  const splitChoices = useMemo(
    () =>
      agents.map((a) => ({
        name: a.name,
        displayName: a.display_name,
        installed: a.installed,
        kind: a.kind ?? "cli",
        logoUrl: a.logo_url || undefined,
        // For a plain terminal the useful second line is WHICH shell opens; a
        // CLI's name already says what it is, so it gets no line of its own.
        description:
          a.kind === "shell" && a.version
            ? `${a.version} — no agent, just a prompt`
            : (a.description ?? ""),
      })),
    [agents],
  );

  const chooseCount = (n: number) => {
    const next = Math.max(1, Math.min(maxTerminals, Math.trunc(n)));
    setCount(next);
    setPlanned((prev) => buildPlan(next, prev, "", suggested));
  };

  const replayRecent = (recent: {
    path: string;
    terminals: number;
    agents: Record<string, number>;
  }) => {
    const total = Math.max(1, Math.min(recent.terminals, maxTerminals));
    setCount(total);
    const entries = Object.entries(recent.agents ?? {}).filter(
      ([, n]) => n > 0,
    );
    if (entries.length === 0) {
      setPlanned(buildPlan(total, [], defaultAgent, suggested));
      return;
    }
    // Expand the remembered split back into one pane per terminal, grouped the
    // way it was, then re-apply the call-sign pool in grid order.
    const expanded: string[] = [];
    for (const [agent, n] of entries) {
      for (let i = 0; i < n && expanded.length < total; i += 1)
        expanded.push(agent);
    }
    while (expanded.length < total) expanded.push(defaultAgent);
    setPlanned(
      expanded.map((agent, index) => ({
        agent,
        name: suggested[index] ?? `T${index + 1}`,
      })),
    );
  };

  /*
   * The plan is built when the count changes rather than held in sync with it.
   *
   * It is also built once the agent sweep lands so the staged launcher already
   * has useful defaults when the user reaches Agents. Guarding on length keeps
   * backward navigation from overwriting choices already made.
   */
  useEffect(() => {
    // Not before the sweep lands. `suggested` is empty until it does, so an
    // earlier run would fill the plan with T1/T2 placeholders — and the guard
    // below, which exists to protect choices the user has made, would then
    // faithfully protect those placeholders from the real call-signs.
    if (!meta) return;
    setPlanned((previous) =>
      previous.length === count
        ? previous
        : buildPlan(count, previous, "", suggested),
    );
  }, [meta, count, suggested]);

  const start = async () => {
    if (!folder) return;
    setBusy(true);
    // Recorded BEFORE the grid can mount: the grid reads the stored preference
    // once, when it appears, so the wizard's answer has to be on disk by then.
    rememberViewMode(view);
    try {
      // The open answers with the whole state — the new workspace AND the bar.
      // Re-fetching instead would be a race that can blank what was just opened.
      const next = await startIdeSession(folder, planned);
      applyState(next);
      setAddingNew(false);
      // The launcher is reusable — someone who opens a third workspace should
      // not land on the leftovers of the second one's plan.
      setFolder(null);
      const names = next.session?.terminals.map((x) => x.name).join(", ") ?? "";
      pushToast("success", `Workspace open — ${names}`);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const close = async () => {
    setBusy(true);
    try {
      await endIdeSession();
      setFocus(false);
      // Closing on purpose withdraws the offer, so the wizard comes back clean
      // rather than immediately proposing the workspace just shut down.
      setOffer(null);
      // Whatever is still open takes the front; only a full refresh knows what
      // that is, so the view asks rather than guessing it is now empty.
      await refresh();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  /*
   * ------------------------------------------------------------ workspace bar
   *
   * All three actions share one rule, and it is the whole reason they are
   * awaited rather than fired off: **the backend has to know the front workspace
   * changed BEFORE the outgoing panes come down.**
   *
   * A pane that disappears releases its viewer, and the backend decides what
   * that means from which workspace the pane belongs to. Ordering the state
   * change after the round-trip keeps that decision correct; doing it the other
   * way round would tear down panes while their workspace was still the front
   * one, which is the difference between "switched tab" and "closed".
   */
  const switchTo = async (id: string) => {
    if (busy || (id === session?.id && !addingNew)) return;
    setBusy(true);
    try {
      applyState(await activateWorkspace(id));
      setAddingNew(false);
      setOffer(null);
    } catch (e) {
      pushToast("error", (e as Error).message);
      // The tab may have been closed elsewhere — re-read rather than leave a
      // bar showing a workspace that is not there.
      void refresh();
    } finally {
      setBusy(false);
    }
  };

  /*
   * "Jump to a pane in another workspace", from the header bell.
   *
   * The grid cannot do this half itself: it is keyed by workspace, so switching
   * tab REPLACES it — a maximize applied before the switch would land on the
   * grid that is about to be thrown away, and one applied after it has no
   * component left to call. So the wish is parked here, survives the swap, and
   * is handed to the grid that comes up as `jumpTo`.
   *
   * The nonce is what lets the same pane be jumped to twice in a row: an effect
   * cannot re-fire on a value that has not changed.
   */
  const [jumpTo, setJumpTo] = useState<{ pane: string; nonce: number } | null>(null);
  const jumpToPane = async (workspaceId: string, pane: string) => {
    if (workspaceId !== session?.id) await switchTo(workspaceId);
    setJumpTo({ pane, nonce: Date.now() });
  };

  /*
   * A file dropped on a workspace TAB.
   *
   * It goes to that workspace's first pane, and the pane types the reference
   * rather than swallowing it: a tab is a coarse target — it names a project,
   * not an agent — so the honest reading is "put this in front of the agents
   * over there", and the user finishes the thought when they switch to it.
   * That is why this does not analyse or compose: the prompt bar is where a
   * dropped file becomes part of an instruction, and this is not that.
   *
   * Dropping on the tab you are already on is the same gesture, so it is not
   * special-cased away; switching to the target afterwards is what makes the
   * result visible rather than something the user has to go looking for.
   */
  const dropOnWorkspace = async (id: string, payload: PaneDropPayload) => {
    if (isEmptyPayload(payload)) return;
    const label = workspaces.find((w) => w.id === id)?.name ?? "that workspace";
    try {
      // Switch FIRST, and not as a courtesy: a workspace card carries no pane
      // names, so activating it is how the target pane becomes knowable at all.
      // It also puts the user in front of the result instead of leaving the
      // file somewhere they would have to go and find.
      let active = session;
      if (id !== session?.id || addingNew) {
        const state = await activateWorkspace(id);
        applyState(state);
        setAddingNew(false);
        setOffer(null);
        active = state.session;
      }
      const pane = active?.terminals[0]?.name;
      if (!pane) {
        pushToast("warning", `${label} has no agent running to give the file to.`);
        return;
      }
      const result = await attachToTerminal(pane, payload);
      pushToast("success", `${result.files.join(", ")} → ${pane} in ${label}.`);
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  };

  /**
   * Open a workspace without asking which folder.
   *
   * A question, a quick script, "what is in this file" are all worth asking
   * without first naming a repository to ask them in. The agent still runs
   * somewhere — a coding CLI is a process with a working directory and there is
   * no such thing as nowhere — so it runs in the home folder, which exists on
   * every account on every OS. The backend owns that choice (it is the same
   * folder the chat library calls a session), so nothing here has to guess a
   * path or spell one out per platform.
   */
  const startSession = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const home = await openScratchProject();
      const next = await startIdeSession(home.path, [{ agent: defaultAgent }]);
      applyState(next);
      setAddingNew(false);
      setFolder(null);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const startAdding = async () => {
    if (busy) return;
    setBusy(true);
    try {
      applyState(await activateWorkspace(null));
      setAddingNew(true);
      // A fresh plan, not the previous workspace's.
      setFolder(null);
      setOffer(null);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const closeOne = async (id: string) => {
    if (busy) return;
    setBusy(true);
    try {
      const next = await closeWorkspace(id);
      applyState(next);
      if (next.session === null && (next.workspaces ?? []).length === 0) {
        // Nothing left — the wizard is the screen again, and the offer for the
        // workspace just shut down has been withdrawn with it.
        setAddingNew(false);
        setOffer(null);
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
      void refresh();
    } finally {
      setBusy(false);
    }
  };

  const renameOne = async (id: string, name: string) => {
    if (busy) return false;
    setBusy(true);
    try {
      applyState(await renameWorkspace(id, name));
      return true;
    } catch (e) {
      pushToast("error", (e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const resumeAll = async () => {
    setBusy(true);
    try {
      const result = await resumeWorkspace();
      applyState(result.state);
      setAddingNew(false);
      setOffer(null);
      // Report what actually came back, not what was hoped for. A pane that
      // reopened empty looks exactly like one that continued until it is asked
      // a follow-up question, so the counts have to be said out loud — and so
      // does any workspace that could not come back at all.
      const spaces = result.workspace_count;
      const panes = result.terminal_count;
      const head =
        `Resumed ${spaces} workspace${spaces === 1 ? "" : "s"}` +
        ` · ${panes} terminal${panes === 1 ? "" : "s"}`;
      const conversations =
        result.started_fresh === 0
          ? "every conversation continued"
          : `${result.resumable_count} continued, ${result.started_fresh} started fresh`;
      pushToast("success", `${head} — ${conversations}.`);
      for (const missing of result.skipped) {
        pushToast("warning", `${missing.folder}: ${missing.detail}`);
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const dismissOffer = async () => {
    // Cleared on screen first: the user said no, and that should not wait for a
    // round-trip. A failed delete only means the offer returns next visit.
    setOffer(null);
    try {
      await forgetResumeOffer();
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  };

  const toggleFocus = async (enabled: boolean) => {
    // Remember a deliberate opt-out, or the auto-enable effect above would turn
    // the mode straight back on and the switch would look broken.
    optedOutRef.current = !enabled;
    // Touching the switch takes ownership of the mode either way: switching it
    // on by hand means "keep it on when I leave", switching it off means there
    // is nothing left for the leave-handler to undo.
    autoEnabledRef.current = false;
    try {
      setFocus(await setFocusMode(enabled));
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  };

  /*
   * The workspace bar sits above BOTH states, always in the same place.
   *
   * It is the one part of this view that is not about the workspace you are in
   * — it is about which one you are in — so it stays put whether the grid or
   * the wizard is below it. A bar that moved (or vanished while adding one)
   * would leave the user without a way back to the workspaces still running.
   *
   * With a workspace open it is handed to the grid rather than rendered here,
   * so the tabs and that workspace's controls share ONE row (see the grid's
   * `workspaceBar` prop). Same bar, same place on screen — one line instead of
   * two, which in a view full of terminals is a pane's worth of output.
   *
   * Pane actions return the updated session, while the compact workspace cards
   * remain the snapshot from the last full-state request. Replace the active
   * card's count from that live session so closing or adding a terminal updates
   * the badge immediately instead of leaving the original spawn count behind.
   */
  const barWorkspaces = workspaces.map((workspace) =>
    session && workspace.id === session.id
      ? { ...workspace, terminals: session.terminals.length }
      : workspace,
  );
  /*
   * `actions` only in the standalone bar.
   *
   * With a workspace open the grid owns the row and puts the app's actions at
   * its far right itself; the wizard has no grid, so the bar carries them — and
   * carries them even with nothing open, which is why it renders at all in that
   * case. Passing them here as well would put Restart on screen twice.
   */
  const renderBar = (embedded: boolean) => (
    <WorkspaceBar
      embedded={embedded}
      actions={embedded ? undefined : <TopBarActions />}
      workspaces={barWorkspaces}
      activeId={session?.id ?? null}
      addingNew={addingNew}
      maxWorkspaces={maxWorkspaces}
      onSelect={(id) => void switchTo(id)}
      onAdd={() => void startAdding()}
      onRename={renameOne}
      onClose={(id) => void closeOne(id)}
      onDropFiles={(id, payload) => void dropOnWorkspace(id, payload)}
      busy={busy}
    />
  );

  // ------------------------------------------------------------ running mode
  if (session && !addingNew) {
    return (
      <div className="flex h-full flex-col">
        <div className="min-h-0 min-w-0 flex-1">
          {/*
            Keyed by workspace, so switching tabs REPLACES the grid instead of
            re-using it. That is deliberate: each pane's terminal is wired to
            one call-sign for its whole life, and re-using the component across
            workspaces would leave xterm instances pointed at the panes of the
            workspace that just left.
          */}
          <AgenticGrid
            key={session.id}
            session={session}
            workspaceBar={renderBar(true)}
            appActions={<TopBarActions />}
            onAddProject={() => void startAdding()}
            onNewSession={() => void startSession()}
            onJumpToWorkspace={(id, pane) => void jumpToPane(id, pane)}
            jumpTo={jumpTo}
            focusMode={focusMode}
            onToggleFocus={(v) => void toggleFocus(v)}
            onClose={() => void close()}
            busy={busy}
            maxTerminals={maxTerminals}
            agents={splitChoices}
            onSessionChanged={setSession}
            accounts={ideAccounts}
            onStateChanged={applyStateFromSettings}
            onScreen={onScreen}
            onPromptTargetChange={setPromptTarget}
            voiceOpen={voiceOpen}
            onToggleVoice={toggleVoiceBubble}
          />
        </div>
        {/*
          The floating voice bubble: talk to the assistant while the agents
          work. A fixed overlay rather than a column, so the terminals keep
          the whole width in every view mode; OUTSIDE the per-workspace grid
          on purpose — the conversation belongs to the app, not to a
          workspace, so switching tabs must not reset the orb mid-sentence.
        */}
        <VoiceBubble
          open={voiceOpen}
          onClose={closeVoiceBubble}
          onScreen={onScreen}
          // The same jump the bell's list performs, from the bubble's update
          // card: switch workspace if the pane lives in another one, then bring
          // that pane into view.
          onJumpToPane={(id, pane) => void jumpToPane(id, pane)}
          promptTarget={
            session.terminals.some(
              (terminal) =>
                terminal.name === promptTarget && terminal.accepts_prompts !== false,
            )
              ? promptTarget
              : session.terminals.find(
                  (terminal) => terminal.accepts_prompts !== false,
                )?.name ?? ""
          }
        />
        {modeIntroFor === session.id && (
          <CodingModeIntro
            terminals={session.terminals.map((x) => x.name)}
            project={session.project.name}
            onDismiss={() => {
              rememberModeIntro();
              setModeIntroFor(null);
            }}
          />
        )}
      </div>
    );
  }

  /*
   * ------------------------------------------------------------- still asking
   *
   * Before the first read lands, this view does not know whether a workspace is
   * open — and "I do not know yet" must not be drawn as "there is none".
   *
   * It used to fall straight through to the wizard and merely add a line of
   * "Checking this machine…" to it, which put the four-step ONBOARDING flow
   * ("Which folder should the agents work in?") in front of a user whose eleven
   * agents were running the whole time (maintainer report 2026-07-29). Being
   * asked to start over is a far worse answer than being told to wait a moment,
   * and the two states are indistinguishable to whoever is reading the screen.
   *
   * `loading` is a first-read flag: it is set once, cleared in `refresh`, and
   * never raised again — so this never covers a workspace that is already up,
   * and later refreshes redraw in place.
   */
  if (loading) {
    return (
      <div
        className="flex h-full w-full items-center justify-center"
        data-testid="agentic-ide-loading"
        aria-busy="true"
      >
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Checking this machine…
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------- launcher
  return (
    <div className="flex h-full min-h-0 flex-col" ref={shellRef}>
      {renderBar(false)}
      <div className="min-h-0 flex-1">
        <WorkspaceLauncher
          addingNew={addingNew}
          busy={busy}
          folder={folder}
          onSelectFolder={setFolder}
          onSelectRecent={replayRecent}
          count={count}
          maxTerminals={maxTerminals}
          suggestedNames={suggested}
          workspaceWidthPx={shellWidth}
          onCount={chooseCount}
          planned={planned}
          onPlanned={setPlanned}
          agents={agents}
          accountsFor={accountsFor}
          onAgentsChanged={() => void reloadAgents()}
          /* Before the sweep lands `meta` is null, and this screen is not
             reached in that state at all (see the `loading` branch above). The
             optimistic default is still the right one: a machine is assumed
             capable until it has said otherwise, never accused on no evidence. */
          terminalAvailable={meta ? meta.terminal_available : true}
          nothingInstalled={Boolean(meta) && installed.length === 0}
          onOpenClis={() => setActiveSection("clis")}
          offer={offer}
          onResume={() => void resumeAll()}
          onDismissOffer={() => void dismissOffer()}
          view={view}
          onView={setView}
          onStart={() => void start()}
        />
      </div>
    </div>
  );
}

/*
 * One-time explanation of what entering a workspace changes.
 *
 * Shown once per machine, not once per session: after the first time the user
 * knows, and a modal that reappears every time is the thing people learn to
 * dismiss without reading. Stored in localStorage rather than in config —
 * "has this person seen a UI hint" is a property of this browser profile, not
 * something worth a round-trip and a config write.
 */
const MODE_INTRO_KEY = "jarvis.agenticIde.codingModeIntroSeen";

function hasSeenModeIntro(): boolean {
  try {
    return window.localStorage.getItem(MODE_INTRO_KEY) === "1";
  } catch {
    // Private mode / storage disabled: showing the hint again is a far smaller
    // problem than crashing the view.
    return false;
  }
}

function rememberModeIntro(): void {
  try {
    window.localStorage.setItem(MODE_INTRO_KEY, "1");
  } catch {
    /* nothing to do — the hint will simply appear again */
  }
}

/**
 * What just changed, once, in four sentences.
 *
 * It used to be four bullets with bolded lead-ins — "**Talk to the agents by
 * name.**", "**Jarvis writes the prompt.**" — each followed by a sentence
 * explaining the lead-in. That is feature-list prose: it advertises a thing the
 * reader has already chosen and opened, and the bolding makes the eye skip
 * precisely the half that carries the information. The same four facts as four
 * plain sentences take a third of the room and are actually read.
 *
 * It stays a dialog rather than becoming a toast because it appears exactly once
 * per machine and describes a mode change the user did not ask for — which is
 * the one thing on this screen worth interrupting for.
 */
function CodingModeIntro({
  terminals,
  project,
  onDismiss,
}: {
  terminals: string[];
  project: string;
  onDismiss: () => void;
}) {
  const first = terminals[0] ?? "T1";
  const second = terminals[1] ?? terminals[0] ?? "T2";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 p-6 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-surface border border-border bg-card p-5 shadow-2xl">
        <h3 className="font-display text-base font-semibold">
          Coding mode is on
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          While this workspace is open, Jarvis works inside {project}.
        </p>

        <ul className="mt-4 space-y-2 text-sm leading-relaxed text-foreground/90">
          <li>
            Talk to the agents by name — “tell {first} to look at the wake word
            code” goes straight into that terminal.
          </li>
          <li>
            What you say is turned into a proper instruction, with the relevant
            files of this repository attached as{" "}
            <code className="font-mono text-xs">@</code> references.
          </li>
          <li>
            “What is {second} up to?” is answered from what that terminal
            actually printed.
          </li>
          <li>
            Nothing is started in the background: work you give a terminal stays
            with that terminal. Turn the mode off any time from the toolbar.
          </li>
        </ul>

        <div className="mt-5 flex justify-end">
          <Button variant="primary" onClick={onDismiss}>
            Got it
          </Button>
        </div>
      </div>
    </div>
  );
}
