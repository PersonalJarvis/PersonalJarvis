/**
 * Opening a workspace — one screen.
 *
 * ## Why this stopped being a wizard
 *
 * Opening a workspace is three decisions: which folder, how many terminals, and
 * which coding agent runs in each. They used to be four steps with Back/Next
 * between them and a fifth screen that showed the three answers back and asked
 * for one more click.
 *
 * A wizard is the right shape when a step's answer changes what the NEXT step
 * may ask. None of these do. The count does not depend on the folder, the agents
 * do not depend on the count, and nothing depends on the confirmation screen at
 * all. What the steps bought was hiding: three quiet decisions became three
 * full-screen episodes, each padded out with a heading, a sentence of guidance
 * and a pair of navigation buttons, so the interface spent its space on
 * scaffolding around the decisions rather than on the decisions.
 *
 * They also cost changes of mind. Realising at the agent step that you wanted a
 * different folder was two Back presses and losing the plan on the way.
 * Everything here is visible together and stays live: pick a different folder
 * with the terminals already named and the names stay.
 *
 * ## What each half is for
 *
 * Left is WHERE — the folder browser, which needs a tall column because it is a
 * list you scroll. Right is WHAT — the number of terminals, the arrangement they
 * will land in, and one row per terminal. That split is also why the panel with
 * the scrolling list is on the left: reading order puts the decision that
 * everything else describes first, and the right column is the one that grows
 * with the count.
 *
 * The single primary action lives in the header, next to a one-line summary of
 * all three answers. That line is the confirmation step, and it costs no click.
 */
import { useEffect } from "react";
import { cn } from "@/lib/utils";
import { FolderPicker } from "./FolderPicker";
import { ResumeCard } from "./ResumeCard";
import { Button, Field, Notice, Panel, Select, SectionLabel } from "./controls";
import { CountStepper, CountTrack, WorkspaceShape } from "./WorkspaceShape";
import type { AgentAccount } from "@/lib/agentAccountsApi";
import type {
  AgentStatus,
  RecentWorkspace,
  ResumeOffer,
} from "@/lib/agenticIdeApi";

/** One pane the workspace will open with, as the launcher holds it. */
export interface PlannedTerminal {
  agent: string;
  name: string;
  /**
   * Which subscription of `agent` this pane opens on, or undefined for the
   * active one. Per pane rather than per workspace on purpose: that is what lets
   * two seats of the same plan run side by side in one folder — which is the
   * entire reason for holding two.
   */
  account?: string;
}

export interface WorkspaceLauncherProps {
  /** True while this is opening an ADDITIONAL workspace beside running ones. */
  addingNew: boolean;
  /** A request is in flight — every control that starts one is disabled. */
  busy: boolean;

  folder: string | null;
  onSelectFolder: (path: string) => void;
  onSelectRecent: (recent: RecentWorkspace) => void;

  count: number;
  maxTerminals: number;
  suggestedNames: string[];
  /** Width the workspace grid will occupy, measured by the view. */
  workspaceWidthPx: number;
  /** Height it will occupy — panes wrap on shape, not on width alone. */
  workspaceHeightPx: number;
  onCount: (next: number) => void;

  planned: PlannedTerminal[];
  onPlanned: (
    update: (previous: PlannedTerminal[]) => PlannedTerminal[],
  ) => void;
  /** Every registered entry — coding CLIs and the plain shell. */
  agents: AgentStatus[];
  /** The registered subscriptions of one CLI, for the per-pane picker. */
  accountsFor: (platform: string) => AgentAccount[];

  /** False when this machine has no PTY backend, so no pane could run. */
  terminalAvailable: boolean;
  /** True when the agent sweep landed and found no coding CLI installed. */
  nothingInstalled: boolean;
  onOpenClis: () => void;

  /** Workspaces from a previous session that can come back, or null. */
  offer: ResumeOffer | null;
  onResume: () => void;
  onDismissOffer: () => void;

  onStart: () => void;
}

export function WorkspaceLauncher({
  addingNew,
  busy,
  folder,
  onSelectFolder,
  onSelectRecent,
  count,
  maxTerminals,
  suggestedNames,
  workspaceWidthPx,
  workspaceHeightPx,
  onCount,
  planned,
  onPlanned,
  agents,
  accountsFor,
  terminalAvailable,
  nothingInstalled,
  onOpenClis,
  offer,
  onResume,
  onDismissOffer,
  onStart,
}: WorkspaceLauncherProps) {
  const ready =
    Boolean(folder) &&
    planned.length > 0 &&
    planned.every((pane) => pane.name.trim() && pane.agent);
  const canStart = ready && !busy;

  /*
   * Ctrl/Cmd + Enter opens, from anywhere on the screen.
   *
   * Not plain Enter: the folder path field uses it to mean "go to this path",
   * and the call-sign fields are ordinary text inputs where Enter would be a
   * surprise. A modified chord has no such collision and is what every terminal
   * on this screen already teaches.
   */
  useEffect(() => {
    if (!canStart) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Enter" || !(event.metaKey || event.ctrlKey)) return;
      event.preventDefault();
      onStart();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canStart, onStart]);

  const setPane = (index: number, patch: Partial<PlannedTerminal>) =>
    onPlanned((previous) =>
      previous.map((pane, i) => (i === index ? { ...pane, ...patch } : pane)),
    );

  return (
    <div
      data-testid="workspace-launcher"
      className="flex h-full min-h-0 flex-col"
    >
      <Header
        addingNew={addingNew}
        folder={folder}
        planned={planned}
        agents={agents}
        canStart={canStart}
        busy={busy}
        onStart={onStart}
      />

      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis">
        <div className="flex flex-col gap-3 px-4 pb-4">
          {!terminalAvailable && (
            <Notice tone="error">
              <span>
                This machine has no usable terminal backend, so agent panes
                cannot run here — <code className="font-mono">pywinpty</code> on
                Windows, <code className="font-mono">ptyprocess</code> on macOS
                and Linux. Both ship with the desktop extra.
              </span>
            </Notice>
          )}

          {nothingInstalled && (
            <Notice tone="warning">
              <span>
                No coding-agent CLI was found on this machine’s PATH. Install one
                and it is picked up automatically.
              </span>
              <Button
                variant="subtle"
                className="h-6 px-2 text-amber-200/90"
                onClick={onOpenClis}
              >
                Open CLIs
              </Button>
            </Notice>
          )}

          {offer && (
            <ResumeCard
              offer={offer}
              busy={busy}
              onResume={onResume}
              onDismiss={onDismissOffer}
            />
          )}

          {/*
            Two columns that become one on a narrow window. The folder column is
            given slightly less than half: it holds one list, while the right
            column holds the arrangement AND a row per terminal.
          */}
          <div className="grid min-h-0 items-start gap-3 lg:grid-cols-[minmax(0,7fr)_minmax(0,9fr)]">
            <Panel title="Folder" className="min-h-0">
              <FolderPicker
                selected={folder}
                onSelect={onSelectFolder}
                onSelectRecent={onSelectRecent}
              />
            </Panel>

            <div className="flex min-w-0 flex-col gap-3">
              <Panel
                title="Terminals"
                aside={
                  <CountStepper
                    count={count}
                    max={maxTerminals}
                    onChange={onCount}
                  />
                }
              >
                <WorkspaceShape
                  count={count}
                  names={suggestedNames}
                  workspaceWidthPx={workspaceWidthPx}
                  workspaceHeightPx={workspaceHeightPx}
                />
                <CountTrack
                  count={count}
                  max={maxTerminals}
                  onChange={onCount}
                />
              </Panel>

              <PanePlan
                planned={planned}
                agents={agents}
                accountsFor={accountsFor}
                onChange={setPane}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The screen's one heading, its one-line summary, and its one primary action.
 *
 * The summary is what the old fourth step was: folder, count, and the agents in
 * play, read back before anything starts. Here it is a line rather than a screen
 * — the same information, none of the ceremony, and it stays true while the
 * choices underneath it change instead of being a snapshot taken at step four.
 */
function Header({
  addingNew,
  folder,
  planned,
  agents,
  canStart,
  busy,
  onStart,
}: {
  addingNew: boolean;
  folder: string | null;
  planned: PlannedTerminal[];
  agents: AgentStatus[];
  canStart: boolean;
  busy: boolean;
  onStart: () => void;
}) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3">
      <div className="min-w-0">
        <h2 className="font-display text-base font-semibold leading-tight">
          {addingNew ? "Open another project" : "Open a project"}
        </h2>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          {folder ? (
            <Summary folder={folder} planned={planned} agents={agents} />
          ) : addingNew ? (
            "The workspaces you already have keep running."
          ) : (
            "Coding agents run in named terminals inside the folder you pick."
          )}
        </p>
      </div>

      <Button
        variant="primary"
        disabled={!canStart}
        onClick={onStart}
        className="px-4"
      >
        {busy ? "Opening…" : "Open workspace"}
        {/*
          The chord is shown on the button rather than explained in a line of
          prose underneath it — that is where someone looks for it, and it costs
          no space anyone was using.
        */}
        <kbd className="ml-1 hidden font-mono text-[10px] font-normal opacity-60 sm:inline">
          ⌘↵
        </kbd>
      </Button>
    </header>
  );
}

/** Folder, count and agents in one line — the confirmation step, uncharged. */
function Summary({
  folder,
  planned,
  agents,
}: {
  folder: string;
  planned: PlannedTerminal[];
  agents: AgentStatus[];
}) {
  const nameOf = (id: string) =>
    agents.find((agent) => agent.name === id)?.display_name ?? id;
  // Named in the order they first appear, so the line reads like the grid does.
  const used: string[] = [];
  for (const pane of planned) {
    const label = nameOf(pane.agent);
    if (!used.includes(label)) used.push(label);
  }
  return (
    <>
      <span className="font-mono text-foreground">{folder}</span>
      <span className="px-1.5 opacity-50">·</span>
      {planned.length} terminal{planned.length === 1 ? "" : "s"}
      {used.length > 0 && (
        <>
          <span className="px-1.5 opacity-50">·</span>
          {used.join(", ")}
        </>
      )}
    </>
  );
}

/**
 * One row per terminal: its call-sign, its coding agent, and — only when the
 * user actually holds several — which subscription it opens on.
 *
 * A table rather than a stack of bordered cards. Twelve cards is twelve frames
 * drawn around three controls each; twelve rows under one set of column labels
 * is the same information with the repetition carried by alignment instead of
 * by outlines.
 */
function PanePlan({
  planned,
  agents,
  accountsFor,
  onChange,
}: {
  planned: PlannedTerminal[];
  agents: AgentStatus[];
  accountsFor: (platform: string) => AgentAccount[];
  onChange: (index: number, patch: Partial<PlannedTerminal>) => void;
}) {
  return (
    <Panel
      title="Who runs where"
      aside={
        <span className="text-[11px] text-muted-foreground">
          Say “what is {planned[1]?.name || planned[0]?.name || "T2"} doing?”
        </span>
      }
    >
      <div className="max-h-[22rem] overflow-y-auto scrollbar-jarvis">
        <table className="w-full border-separate border-spacing-0">
          <thead className="sticky top-0 z-10 bg-card">
            <tr>
              <th className="w-8 px-3 py-1.5 text-left">
                <SectionLabel>#</SectionLabel>
              </th>
              <th className="px-1 py-1.5 text-left">
                <SectionLabel>Call-sign</SectionLabel>
              </th>
              <th className="px-1 py-1.5 text-left">
                <SectionLabel>Agent</SectionLabel>
              </th>
            </tr>
          </thead>
          <tbody>
            {planned.map((pane, index) => {
              const accounts = accountsFor(pane.agent);
              return (
                <tr key={index} className="group">
                  <td className="px-3 py-1 align-middle font-mono text-xs tabular-nums text-muted-foreground">
                    {index + 1}
                  </td>
                  <td className="px-1 py-1 align-middle">
                    <Field
                      value={pane.name}
                      onChange={(event) =>
                        onChange(index, { name: event.target.value })
                      }
                      aria-label={`Call-sign for terminal ${index + 1}`}
                      className="w-28 font-mono"
                      spellCheck={false}
                    />
                  </td>
                  <td className="px-1 py-1 align-middle">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Select
                        value={pane.agent}
                        aria-label={`Agent for terminal ${index + 1}`}
                        onChange={(event) =>
                          onChange(index, {
                            agent: event.target.value,
                            // The account belongs to the OLD CLI — an id from
                            // one CLI means nothing to the other.
                            account: undefined,
                          })
                        }
                        className="w-40"
                      >
                        {agents.map((agent) => (
                          <option
                            key={agent.name}
                            value={agent.name}
                            /* Kept in the list but unselectable, so a missing
                               CLI is visible rather than silently absent. */
                            disabled={!agent.installed}
                          >
                            {agent.display_name}
                            {agent.installed ? "" : " — not installed"}
                          </option>
                        ))}
                      </Select>
                      {/*
                        Renders NOTHING with a single login, which is almost
                        everybody: a control that answers a question the user
                        does not have is noise. It appears the moment a second
                        seat is registered, and then it is per pane, so one
                        folder can hold panes on both plans at once.
                      */}
                      {accounts.length >= 2 && (
                        <Select
                          value={pane.account ?? ""}
                          aria-label={`Subscription for the ${pane.agent} terminal`}
                          onChange={(event) =>
                            onChange(index, {
                              account: event.target.value || undefined,
                            })
                          }
                          className="w-36 text-xs"
                        >
                          <option value="">Active account</option>
                          {accounts.map((account) => (
                            <option key={account.id} value={account.id}>
                              {account.label}
                              {account.connected ? "" : " (not signed in)"}
                            </option>
                          ))}
                        </Select>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p
        className={cn(
          "border-t border-border/70 px-3 py-2 text-[11px] text-muted-foreground",
        )}
      >
        Terminals are numbered by position, so a call-sign is what you say out
        loud. Coding mode turns on when the workspace opens, and off again from
        the toolbar.
      </p>
    </Panel>
  );
}
