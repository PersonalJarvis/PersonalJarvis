/**
 * Agentic IDE — the "code with Jarvis" section.
 *
 * Two states in one view:
 *
 * * no workspace open → a four-step wizard: folder, how many terminals, which
 *   agent + call-sign per terminal, then start,
 * * workspace open → the terminal grid (see AgenticGrid).
 *
 * The wizard order is deliberate and matches how the decision actually gets
 * made: you know the folder first, the number of panes second, and only then
 * care which agent sits in which pane. Every step is reversible and nothing is
 * started until the last one.
 *
 * i18n note: the section label and view header go through the locale files; the
 * panel copy inside is still English-only source awaiting its i18n keys.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Brain,
  Check,
  Download,
  FolderOpen,
  Loader2,
  Mic,
  Rocket,
  Sparkles,
  Terminal,
  Users,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { AgenticGrid } from "@/components/agentic/AgenticGrid";
import { TerminalCountStep } from "@/components/agentic/TerminalCountStep";
import {
  type AgentAccount,
  type AgentAccountsResponse,
  fetchAgentAccounts,
  groupFor,
} from "@/lib/agentAccountsApi";
import { TopBarActions } from "@/components/layout/TopBar";
import { FolderPicker } from "@/components/agentic/FolderPicker";
import { ResumeCard } from "@/components/agentic/ResumeCard";
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
  type AgentStatus,
  type AgentsResponse,
  type IdeAccountState,
  type IdeState,
  type ResumeOffer,
  type SessionState,
  type WorkspaceCard,
} from "@/lib/agenticIdeApi";

type Step = 0 | 1 | 2 | 3;

interface PlannedTerminal {
  agent: string;
  name: string;
  /**
   * Which subscription of `agent` this pane opens on, or undefined for the
   * active one. Per pane rather than per workspace on purpose: that is what
   * lets two seats of the same plan run side by side in one folder — which is
   * the entire reason for holding two.
   */
  account?: string;
}

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

export function AgenticIdeView() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const setActiveSection = useEventStore((s) => s.setActiveSection);

  const [loading, setLoading] = useState(true);
  const [meta, setMeta] = useState<AgentsResponse | null>(null);
  const [session, setSession] = useState<SessionState | null>(null);
  const [focusMode, setFocus] = useState(false);
  const [busy, setBusy] = useState(false);

  // Every open workspace, for the bar above. The one on screen is `session`;
  // these are the others, still running, one click away.
  const [workspaces, setWorkspaces] = useState<WorkspaceCard[]>([]);
  const [maxWorkspaces, setMaxWorkspaces] = useState(6);
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

  const [step, setStep] = useState<Step>(0);
  const [folder, setFolder] = useState<string | null>(null);
  const [count, setCount] = useState(2);
  const [planned, setPlanned] = useState<PlannedTerminal[]>([]);
  // The registered subscriptions per CLI. Only ever used to OFFER a choice, so
  // a failed load costs the picker, never the wizard: with none loaded every
  // pane simply opens on the active account, exactly as before.
  const [accounts, setAccounts] = useState<AgentAccountsResponse | null>(null);
  // The workspace that was open when the window last closed, if it can come
  // back. Null both when there is nothing to offer and after the user has
  // answered the offer either way.
  const [offer, setOffer] = useState<ResumeOffer | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [state, agents] = await Promise.all([
        fetchIdeState(),
        fetchIdeAgents(),
      ]);
      setMeta(agents);
      setSession(state.session);
      setWorkspaces(state.workspaces ?? []);
      setMaxWorkspaces(state.max_workspaces ?? 6);
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
      setLoading(false);
    }
  }, []);

  /** Apply a state the backend just handed back, without a second round-trip. */
  const applyState = useCallback((state: IdeState) => {
    setSession(state.session);
    setWorkspaces(state.workspaces ?? []);
    setMaxWorkspaces(state.max_workspaces ?? 6);
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
  const [modeIntroFor, setModeIntroFor] = useState<string | null>(null);

  /*
   * How wide the workspace will be — measured here, in the wizard, because the
   * preview dots must promise the arrangement the grid will actually produce.
   *
   * The grid decides its band width from its own width (a pane below
   * MIN_PANE_WIDTH_PX is unreadable), so a preview computing columns from the
   * count alone drifts apart from it the moment the window is narrow: it drew
   * "12 → 6 above, 6 below" while the grid, in the same window, produced three
   * columns and four rows. The wizard shell sits in the same slot the grid will
   * occupy, so measuring it here answers the same question.
   *
   * This is the width the preview then NAMES rather than silently assumes — see
   * TerminalCountStep. Measuring it right only makes the preview correct for the
   * window it was shown in; saying so is what makes it correct after a maximise.
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
  }, [session]);

  useEffect(() => {
    if (!session || session.focus_mode || optedOutRef.current) return;
    let cancelled = false;
    void (async () => {
      try {
        const on = await setFocusMode(true);
        if (cancelled) return;
        setFocus(on);
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
  }, [session]);

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
    setPlanned((prev) => buildPlan(next, prev, defaultAgent, suggested));
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

  const goToStep = (next: Step) => {
    // Entering the agent step is the moment the plan has to exist and the
    // suggested call-signs have certainly arrived.
    if (next === 2) {
      setPlanned((prev) => buildPlan(count, prev, defaultAgent, suggested));
    }
    setStep(next);
  };

  const start = async () => {
    if (!folder) return;
    setBusy(true);
    try {
      // The open answers with the whole state — the new workspace AND the bar.
      // Re-fetching instead would be a race that can blank what was just opened.
      const next = await startIdeSession(folder, planned);
      applyState(next);
      setAddingNew(false);
      // The wizard is reusable — someone who opens a third workspace should not
      // land on the leftovers of the second one's plan.
      setStep(0);
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
      setStep(0);
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

  const startAdding = async () => {
    if (busy) return;
    setBusy(true);
    try {
      applyState(await activateWorkspace(null));
      setAddingNew(true);
      // A fresh plan, not the previous workspace's.
      setStep(0);
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
        setStep(0);
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
        <div className="min-h-0 flex-1">
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
            focusMode={focusMode}
            onToggleFocus={(v) => void toggleFocus(v)}
            onClose={() => void close()}
            busy={busy}
            maxTerminals={maxTerminals}
            agents={splitChoices}
            onSessionChanged={setSession}
            accounts={ideAccounts}
            onStateChanged={applyStateFromSettings}
          />
        </div>
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

  // ------------------------------------------------------------------ wizard
  const canAdvance =
    (step === 0 && Boolean(folder)) ||
    (step === 1 && count > 0) ||
    (step === 2 && planned.every((p) => p.name.trim() && p.agent));

  return (
    <div className="flex h-full flex-col" ref={shellRef}>
      {renderBar(false)}
      <ViewHeader
        icon={<Sparkles className="h-4 w-4 text-primary" />}
        title={
          t("nav.agentic_ide") === "nav.agentic_ide"
            ? "Agentic IDE"
            : t("nav.agentic_ide")
        }
        subtitle={
          addingNew
            ? "Open another folder — the workspaces you already have keep running."
            : "Open a folder, run coding agents in named terminals, and talk to them."
        }
      />

      <div className="flex-1 overflow-y-auto scrollbar-jarvis">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-6">
          {/*
            Above the wizard, not in front of it. Someone who came here to open
            a different folder can walk straight past; someone who lost a window
            full of running agents sees the way back first.
          */}
          {offer && (
            <ResumeCard
              offer={offer}
              busy={busy}
              onResume={() => void resumeAll()}
              onDismiss={() => void dismissOffer()}
            />
          )}

          <StepRail step={step} />

          {loading && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Checking this machine…
            </div>
          )}

          {meta && !meta.terminal_available && (
            <Callout tone="error">
              This machine has no usable terminal backend, so agent panes cannot
              run here. On Windows that means <code>pywinpty</code>, on macOS
              and Linux <code>ptyprocess</code> — both ship with the desktop
              extra.
            </Callout>
          )}

          {meta && installed.length === 0 && (
            <Callout tone="warn">
              No coding-agent CLI was found on this machine’s PATH. Install
              one from the CLIs page, then come back — the wizard picks it up
              automatically.
              <button
                type="button"
                className="btn-ghost mt-2"
                onClick={() => setActiveSection("clis")}
              >
                <Download className="h-4 w-4" />
                Open CLIs
              </button>
            </Callout>
          )}

          {step === 0 && (
            <Section
              title="Which folder should the agents work in?"
              hint="Pick the project root. Everything the agents do happens inside it."
            >
              <FolderPicker
                selected={folder}
                onSelect={setFolder}
                onSelectRecent={replayRecent}
              />
            </Section>
          )}

          {step === 1 && (
            <Section
              title="How many terminals?"
              hint="Each terminal runs its own agent. Below is the workspace you are about to open — the panes sit exactly like that."
            >
              <TerminalCountStep
                count={count}
                max={maxTerminals}
                names={suggested}
                workspaceWidthPx={shellWidth}
                onChange={chooseCount}
              />
            </Section>
          )}

          {step === 2 && (
            <Section
              title="Who runs where?"
              hint="Terminals are numbered by position — T1, T2, T3 — so you can just say “what is T2 doing?”. Rename one if you prefer."
            >
              <ul className="space-y-2">
                {planned.map((pane, index) => (
                  <li
                    key={index}
                    className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card/60 p-3"
                  >
                    <span className="w-6 shrink-0 text-center font-mono text-xs text-muted-foreground">
                      {index + 1}
                    </span>
                    <input
                      value={pane.name}
                      onChange={(e) =>
                        setPlanned((prev) =>
                          prev.map((p, i) =>
                            i === index ? { ...p, name: e.target.value } : p,
                          ),
                        )
                      }
                      aria-label={`Call-sign for terminal ${index + 1}`}
                      className="w-32 shrink-0 rounded-lg border border-border bg-background/60 px-3 py-1.5 text-sm outline-none focus:border-primary/50"
                      spellCheck={false}
                    />
                    <div className="flex flex-wrap items-center gap-2">
                      {agents.map((agent) => (
                        <AgentChoice
                          key={agent.name}
                          agent={agent}
                          selected={pane.agent === agent.name}
                          onSelect={() =>
                            setPlanned((prev) =>
                              prev.map((p, i) =>
                                i === index
                                  ? // The account belongs to the OLD CLI — an id
                                    // from one CLI means nothing to the other.
                                    {
                                      ...p,
                                      agent: agent.name,
                                      account: undefined,
                                    }
                                  : p,
                              ),
                            )
                          }
                        />
                      ))}
                    </div>
                    <AccountChoice
                      platform={pane.agent}
                      accounts={accountsFor(pane.agent)}
                      value={pane.account}
                      onSelect={(id) =>
                        setPlanned((prev) =>
                          prev.map((p, i) =>
                            i === index ? { ...p, account: id } : p,
                          ),
                        )
                      }
                    />
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {step === 3 && (
            <Section title="Ready to open" hint="Nothing has started yet.">
              <div className="space-y-4 rounded-xl border border-border bg-card/60 p-5">
                <div className="flex items-center gap-2 text-sm">
                  <FolderOpen className="h-4 w-4 shrink-0 text-primary" />
                  <code className="min-w-0 truncate font-mono text-xs">
                    {folder}
                  </code>
                </div>
                <ul className="flex flex-wrap gap-2">
                  {planned.map((pane, i) => (
                    <li
                      key={i}
                      className="flex items-center gap-2 rounded-lg bg-primary/10 px-3 py-1.5 text-sm text-primary"
                    >
                      <Terminal className="h-3.5 w-3.5" />
                      <span className="font-medium">{pane.name}</span>
                      <span className="text-primary/70">
                        {agents.find((a) => a.name === pane.agent)
                          ?.display_name ?? pane.agent}
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="text-xs text-muted-foreground">
                  Once the panes are up you can turn on coding mode, which lets
                  Jarvis answer inside this workspace — and switch it off again
                  at any time.
                </p>
              </div>
            </Section>
          )}

          <div className="flex items-center justify-between pt-2">
            <button
              type="button"
              className={cn("btn-ghost", step === 0 && "invisible")}
              onClick={() => goToStep((step > 0 ? step - 1 : step) as Step)}
            >
              <ArrowLeft className="h-4 w-4" />
              Back
            </button>
            {step < 3 ? (
              <button
                type="button"
                className="btn-primary disabled:cursor-not-allowed disabled:opacity-40"
                disabled={!canAdvance}
                onClick={() => goToStep((step + 1) as Step)}
              >
                Next
                <ArrowRight className="h-4 w-4" />
              </button>
            ) : (
              <button
                type="button"
                className="btn-primary disabled:cursor-not-allowed disabled:opacity-40"
                disabled={busy || !folder || planned.length === 0}
                onClick={() => void start()}
              >
                {busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Rocket className="h-4 w-4" />
                )}
                {busy ? "Opening…" : "Open workspace"}
              </button>
            )}
          </div>
        </div>
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

function CodingModeIntro({
  terminals,
  project,
  onDismiss,
}: {
  terminals: string[];
  project: string;
  onDismiss: () => void;
}) {
  const first = terminals[0] ?? "Mika";
  const second = terminals[1] ?? "Nova";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 p-6 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-2xl border border-primary/30 bg-card p-6 shadow-2xl">
        <div className="mb-4 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/15">
            <Brain className="h-5 w-5 text-primary" />
          </span>
          <div>
            <h3 className="font-display text-lg font-semibold">
              Coding mode is on
            </h3>
            <p className="text-xs text-muted-foreground">
              While this workspace is open, Jarvis works inside {project}.
            </p>
          </div>
        </div>

        <ul className="space-y-3 text-sm">
          <li className="flex gap-3">
            <Mic className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <span>
              <strong className="font-medium">
                Talk to the agents by name.
              </strong>{" "}
              Say “tell {first} to look at the wake word code” and it goes
              straight into {first}’s terminal — you never type the prompt
              yourself.
            </span>
          </li>
          <li className="flex gap-3">
            <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <span>
              <strong className="font-medium">Jarvis writes the prompt.</strong>{" "}
              What you say gets turned into a proper instruction, with the
              relevant files of this repository attached as <code>@</code>{" "}
              references.
            </span>
          </li>
          <li className="flex gap-3">
            <Terminal className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <span>
              <strong className="font-medium">Ask what they are doing.</strong>{" "}
              “What is {second} up to?” is answered from what that terminal
              actually printed.
            </span>
          </li>
          <li className="flex gap-3">
            <Brain className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <span>
              <strong className="font-medium">
                Think out loud with Jarvis.
              </strong>{" "}
              It knows this codebase — the stack, the branch, the instruction
              files — so planning happens here instead of in a chat window.
            </span>
          </li>
        </ul>

        <p className="mt-4 text-xs text-muted-foreground">
          No background agents are started while you are in here: work you give
          a terminal stays with that terminal. Turn the mode off any time with
          the button in the toolbar.
        </p>

        <div className="mt-5 flex justify-end">
          <button type="button" className="btn-primary" onClick={onDismiss}>
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}

function StepRail({ step }: { step: Step }) {
  const labels = ["Folder", "Terminals", "Agents", "Start"];
  return (
    <ol className="flex items-center gap-2">
      {labels.map((label, index) => (
        <li key={label} className="flex flex-1 items-center gap-2">
          <span
            className={cn(
              "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold",
              index < step
                ? "bg-primary/20 text-primary"
                : index === step
                  ? "bg-primary text-background"
                  : "bg-muted text-muted-foreground",
            )}
          >
            {index < step ? <Check className="h-3.5 w-3.5" /> : index + 1}
          </span>
          <span
            className={cn(
              "truncate text-xs",
              index === step ? "text-foreground" : "text-muted-foreground",
            )}
          >
            {label}
          </span>
          {index < labels.length - 1 && (
            <span className="hidden h-px flex-1 bg-border sm:block" />
          )}
        </li>
      ))}
    </ol>
  );
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="font-display text-lg font-semibold">{title}</h3>
        <p className="text-sm text-muted-foreground">{hint}</p>
      </div>
      {children}
    </div>
  );
}

function AgentChoice({
  agent,
  selected,
  onSelect,
}: {
  agent: AgentStatus;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={!agent.installed}
      aria-pressed={selected}
      title={
        agent.installed
          ? (agent.version ?? undefined)
          : "Not installed on this machine"
      }
      className={cn(
        "flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm transition-colors",
        selected
          ? "border-primary/60 bg-primary/10 text-primary"
          : "border-border text-muted-foreground hover:border-primary/40",
        !agent.installed && "cursor-not-allowed opacity-40",
      )}
    >
      <Terminal className="h-3.5 w-3.5" />
      {agent.display_name}
    </button>
  );
}

/**
 * Which of several subscriptions of one CLI this pane opens on.
 *
 * Renders NOTHING when the user has only the one login, which is almost
 * everybody — the wizard must not grow a control that answers a question the
 * user does not have. It appears the moment a second seat is registered, and
 * then it is per pane, so one folder can hold panes on both plans at once.
 */
function AccountChoice({
  platform,
  accounts,
  value,
  onSelect,
}: {
  platform: string;
  accounts: AgentAccount[];
  value: string | undefined;
  onSelect: (id: string | undefined) => void;
}) {
  if (accounts.length < 2) return null;
  return (
    <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <Users className="h-3.5 w-3.5 shrink-0" />
      <span className="sr-only">Subscription for this terminal</span>
      <select
        value={value ?? ""}
        onChange={(e) => onSelect(e.target.value || undefined)}
        aria-label={`Subscription for the ${platform} terminal`}
        className="rounded-lg border border-border bg-background/60 px-2 py-1.5 text-xs outline-none focus:border-primary/50"
      >
        <option value="">Active account</option>
        {accounts.map((account) => (
          <option key={account.id} value={account.id}>
            {account.label}
            {account.connected ? "" : " (not signed in)"}
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * The arrangement ``n`` terminals will actually open in.
 *
 * Both numbers behind this come from the grid itself: `perBand` is what fits in
 * the measured width, and `paneColumns` is the function the grid lays itself out
 * with. The preview used to have its own formula (three per line), so choosing 4
 * previewed 3 above and 1 below and then opened 4 side by side; then it dropped
 * the width and promised 6 + 6 where a narrow window gave three columns and four
 * rows. A preview is only worth showing while it cannot disagree with the real
 * thing, and that means sharing every input, not just the function.
 */
function Callout({
  tone,
  children,
}: {
  tone: "error" | "warn";
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-lg border px-4 py-3 text-sm",
        tone === "error"
          ? "border-destructive/40 bg-destructive/10 text-destructive"
          : "border-amber-500/40 bg-amber-500/10 text-amber-200",
      )}
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="flex flex-col items-start">{children}</div>
    </div>
  );
}
