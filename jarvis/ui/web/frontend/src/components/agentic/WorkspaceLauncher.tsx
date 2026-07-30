/**
 * The staged workspace launcher.
 *
 * Folder, layout, terminal assignments and review are separate decisions. The
 * values live in AgenticIdeView rather than in the active step, so moving back
 * never throws work away. Visually this is one continuous work surface: steps
 * are separated by typography and rules, not by stacking cards inside cards.
 */
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { FolderPicker } from "./FolderPicker";
import { ResumeCard } from "./ResumeCard";
import { Button, Field, Notice, Select, SectionLabel } from "./controls";
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
  /** Which subscription of `agent` this pane opens on. */
  account?: string;
}

export interface WorkspaceLauncherProps {
  /** True while this is opening an additional workspace beside running ones. */
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

type LauncherStep = 0 | 1 | 2 | 3;

const STEPS = [
  {
    label: "Folder",
    title: "Choose the project folder",
    hint: "Everything the agents do stays inside this folder.",
  },
  {
    label: "Layout",
    title: "Shape the workspace",
    hint: "Choose how many independent terminal panes should open.",
  },
  {
    label: "Agents",
    title: "Assign the terminals",
    hint: "Name each pane and choose the coding agent that runs there.",
  },
  {
    label: "Review",
    title: "Review before opening",
    hint: "Nothing starts until you open the workspace.",
  },
] as const;

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
  const [step, setStep] = useState<LauncherStep>(0);
  const planReady =
    planned.length > 0 &&
    planned.every((pane) => pane.name.trim() && pane.agent);
  const ready = Boolean(folder) && planReady;

  const setPane = (index: number, patch: Partial<PlannedTerminal>) =>
    onPlanned((previous) =>
      previous.map((pane, i) => (i === index ? { ...pane, ...patch } : pane)),
    );

  const canLeaveCurrent =
    (step === 0 && Boolean(folder)) ||
    (step === 1 && count > 0) ||
    (step === 2 && planReady) ||
    step === 3;

  const canVisit = (target: LauncherStep) => {
    if (target <= step) return true;
    if (!folder) return false;
    if (target <= 2) return true;
    return planReady;
  };

  /* The launch chord is intentionally limited to the review step. */
  useEffect(() => {
    if (step !== 3 || !ready || busy) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Enter" || !(event.metaKey || event.ctrlKey)) return;
      event.preventDefault();
      onStart();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onStart, ready, step]);

  const active = STEPS[step];

  return (
    <div
      data-testid="workspace-launcher"
      className="flex h-full min-h-0 flex-col font-display"
    >
      <header className="shrink-0 border-b border-border/70 px-5 py-5 sm:px-8">
        <div className="mx-auto flex w-full max-w-6xl items-start justify-between gap-6">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary/80">
              {addingNew ? "Additional workspace" : "New workspace"}
              <span className="px-2 text-muted-foreground/50">/</span>
              Step {step + 1} of {STEPS.length}
            </p>
            <h2
              id="workspace-launcher-title"
              className="mt-2 text-xl font-semibold tracking-tight text-foreground sm:text-2xl"
            >
              {active.title}
            </h2>
            <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">
              {active.hint}
              {addingNew && step === 0
                ? " Your open workspaces keep running."
                : ""}
            </p>
          </div>
          {folder && (
            <code
              className="hidden max-w-[36%] truncate pt-1 font-mono text-[11px] text-muted-foreground xl:block"
              title={folder}
            >
              {folder}
            </code>
          )}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis">
        <div className="mx-auto w-full max-w-6xl px-5 pb-7 sm:px-8">
          {(offer || !terminalAvailable || nothingInstalled) && (
            <div className="space-y-4 pt-5">
              {!terminalAvailable && (
                <Notice tone="error">
                  <span>
                    This machine has no usable terminal backend, so agent panes
                    cannot run here —{" "}
                    <code className="font-mono">pywinpty</code> on Windows,{" "}
                    <code className="font-mono">ptyprocess</code> on macOS and
                    Linux. Both ship with the desktop extra.
                  </span>
                </Notice>
              )}

              {nothingInstalled && (
                <Notice tone="warning">
                  <span>
                    No coding-agent CLI was found on this machine’s PATH.
                    Install one and it is picked up automatically.
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
            </div>
          )}

          <div className="grid min-h-0 gap-7 py-6 lg:grid-cols-[12rem_minmax(0,1fr)] lg:gap-10">
            <StepNavigation
              step={step}
              folder={folder}
              count={count}
              planned={planned}
              canVisit={canVisit}
              onStep={setStep}
            />

            <section
              className="min-w-0"
              aria-labelledby="workspace-launcher-title"
            >
              {step === 0 && (
                <div className="flex min-h-[28rem] flex-col border-y border-border/70">
                  <FolderPicker
                    selected={folder}
                    onSelect={onSelectFolder}
                    onSelectRecent={onSelectRecent}
                  />
                </div>
              )}

              {step === 1 && (
                <div>
                  <div className="flex items-end justify-between gap-5 border-b border-border/70 pb-5">
                    <div>
                      <SectionLabel>Terminal panes</SectionLabel>
                      <p className="mt-2 font-mono text-4xl font-medium tabular-nums text-foreground">
                        {count.toString().padStart(2, "0")}
                      </p>
                    </div>
                    <CountStepper
                      count={count}
                      max={maxTerminals}
                      onChange={onCount}
                    />
                  </div>
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
                </div>
              )}

              {step === 2 && (
                <PanePlan
                  planned={planned}
                  agents={agents}
                  accountsFor={accountsFor}
                  onChange={setPane}
                />
              )}

              {step === 3 && folder && (
                <WorkspaceReview
                  folder={folder}
                  planned={planned}
                  agents={agents}
                />
              )}

              <footer className="mt-7 flex min-h-10 items-center justify-between border-t border-border/70 pt-5">
                {step > 0 ? (
                  <Button
                    variant="subtle"
                    onClick={() => setStep((step - 1) as LauncherStep)}
                  >
                    <ArrowLeft className="h-3.5 w-3.5" />
                    Back
                  </Button>
                ) : (
                  <span />
                )}

                {step < 3 ? (
                  <Button
                    variant="primary"
                    disabled={!canLeaveCurrent}
                    onClick={() => setStep((step + 1) as LauncherStep)}
                    className="px-4"
                  >
                    {step === 0
                      ? "Continue to layout"
                      : step === 1
                        ? "Continue to agents"
                        : "Review workspace"}
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Button>
                ) : (
                  <Button
                    variant="primary"
                    disabled={!ready || busy}
                    onClick={onStart}
                    className="min-w-40 px-4"
                  >
                    {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                    {busy ? "Opening…" : "Open workspace"}
                    {!busy && (
                      <kbd className="ml-1 hidden font-mono text-[10px] font-normal opacity-60 sm:inline">
                        ⌘↵
                      </kbd>
                    )}
                  </Button>
                )}
              </footer>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}

function StepNavigation({
  step,
  folder,
  count,
  planned,
  canVisit,
  onStep,
}: {
  step: LauncherStep;
  folder: string | null;
  count: number;
  planned: PlannedTerminal[];
  canVisit: (target: LauncherStep) => boolean;
  onStep: (step: LauncherStep) => void;
}) {
  const summaries = useMemo(
    () => [
      folder ? leafName(folder) : "Not chosen",
      `${count} terminal${count === 1 ? "" : "s"}`,
      `${planned.length} assigned`,
      "Check and open",
    ],
    [count, folder, planned.length],
  );

  return (
    <nav aria-label="Workspace setup" className="min-w-0">
      <ol className="grid grid-cols-4 border-b border-border/70 lg:flex lg:flex-col lg:border-b-0 lg:border-r lg:pr-6">
        {STEPS.map((item, index) => {
          const target = index as LauncherStep;
          const selected = target === step;
          const enabled = canVisit(target);
          return (
            <li key={item.label}>
              <button
                type="button"
                data-testid={`launcher-step-${item.label.toLowerCase()}`}
                aria-current={selected ? "step" : undefined}
                disabled={!enabled}
                onClick={() => onStep(target)}
                className={cn(
                  "group relative w-full min-w-0 px-2 py-3 text-left transition-colors lg:px-0 lg:py-4",
                  "disabled:cursor-not-allowed disabled:opacity-35",
                  selected ? "text-foreground" : "text-muted-foreground",
                )}
              >
                <span
                  className={cn(
                    "absolute bottom-[-1px] left-0 right-0 h-0.5 lg:bottom-0 lg:left-auto lg:right-[-25px] lg:top-0 lg:h-auto lg:w-0.5",
                    selected ? "bg-primary" : "bg-transparent",
                  )}
                />
                <span className="block font-mono text-[10px] tabular-nums text-muted-foreground/70">
                  0{index + 1}
                </span>
                <span className="mt-1 block truncate text-sm font-medium">
                  {item.label}
                </span>
                <span className="mt-0.5 hidden truncate text-[11px] text-muted-foreground lg:block">
                  {summaries[index]}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** One row per terminal, aligned as a table rather than framed as cards. */
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
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3 pb-4">
        <div>
          <SectionLabel>Terminal assignments</SectionLabel>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            Call-signs are what you say aloud when addressing a pane.
          </p>
        </div>
        <p className="text-[11px] text-muted-foreground">
          “What is {planned[1]?.name || planned[0]?.name || "T2"} doing?”
        </p>
      </div>
      <div className="max-h-[28rem] overflow-y-auto scrollbar-jarvis border-y border-border/70">
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10 bg-background/95 backdrop-blur">
            <tr className="border-b border-border/70">
              <th className="w-12 px-3 py-2 text-left">
                <SectionLabel>#</SectionLabel>
              </th>
              <th className="px-2 py-2 text-left">
                <SectionLabel>Call-sign</SectionLabel>
              </th>
              <th className="px-2 py-2 text-left">
                <SectionLabel>Agent</SectionLabel>
              </th>
            </tr>
          </thead>
          <tbody>
            {planned.map((pane, index) => {
              const accounts = accountsFor(pane.agent);
              return (
                <tr
                  key={index}
                  className="border-b border-border/50 last:border-b-0"
                >
                  <td className="px-3 py-2 align-middle font-mono text-xs tabular-nums text-muted-foreground">
                    {(index + 1).toString().padStart(2, "0")}
                  </td>
                  <td className="px-2 py-2 align-middle">
                    <Field
                      value={pane.name}
                      onChange={(event) =>
                        onChange(index, { name: event.target.value })
                      }
                      aria-label={`Call-sign for terminal ${index + 1}`}
                      className="w-full min-w-24 font-mono sm:w-40"
                      spellCheck={false}
                    />
                  </td>
                  <td className="px-2 py-2 align-middle">
                    <div className="flex flex-wrap items-center gap-2">
                      <Select
                        value={pane.agent}
                        aria-label={`Agent for terminal ${index + 1}`}
                        onChange={(event) =>
                          onChange(index, {
                            agent: event.target.value,
                            account: undefined,
                          })
                        }
                        className="w-44"
                      >
                        {agents.map((agent) => (
                          <option
                            key={agent.name}
                            value={agent.name}
                            disabled={!agent.installed}
                          >
                            {agent.display_name}
                            {agent.installed ? "" : " — not installed"}
                          </option>
                        ))}
                      </Select>
                      {accounts.length >= 2 && (
                        <Select
                          value={pane.account ?? ""}
                          aria-label={`Subscription for the ${pane.agent} terminal`}
                          onChange={(event) =>
                            onChange(index, {
                              account: event.target.value || undefined,
                            })
                          }
                          className="w-40 text-xs"
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
    </div>
  );
}

function WorkspaceReview({
  folder,
  planned,
  agents,
}: {
  folder: string;
  planned: PlannedTerminal[];
  agents: AgentStatus[];
}) {
  const displayName = (agentId: string) =>
    agents.find((agent) => agent.name === agentId)?.display_name ?? agentId;

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1.1fr)_minmax(18rem,0.9fr)]">
      <div className="min-w-0">
        <SectionLabel>Workspace</SectionLabel>
        <h4 className="mt-3 truncate text-2xl font-semibold tracking-tight text-foreground">
          {leafName(folder)}
        </h4>
        <code className="mt-2 block break-all font-mono text-xs leading-relaxed text-muted-foreground">
          {folder}
        </code>

        <dl className="mt-7 border-y border-border/70">
          <div className="flex items-center justify-between gap-5 border-b border-border/50 py-3 text-sm">
            <dt className="text-muted-foreground">Terminal panes</dt>
            <dd className="font-mono tabular-nums text-foreground">
              {planned.length}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-5 py-3 text-sm">
            <dt className="text-muted-foreground">Coding mode</dt>
            <dd className="text-foreground">Turns on when opened</dd>
          </div>
        </dl>
      </div>

      <div className="min-w-0">
        <SectionLabel>Terminal plan</SectionLabel>
        <ol className="mt-3 border-t border-border/70">
          {planned.map((pane, index) => (
            <li
              key={`${pane.name}-${index}`}
              className="grid grid-cols-[2rem_minmax(0,1fr)_auto] items-baseline gap-2 border-b border-border/50 py-2.5 text-sm"
            >
              <span className="font-mono text-[10px] tabular-nums text-muted-foreground/70">
                {(index + 1).toString().padStart(2, "0")}
              </span>
              <span className="truncate font-mono text-foreground">
                {pane.name}
              </span>
              <span className="truncate text-xs text-muted-foreground">
                {displayName(pane.agent)}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function leafName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}
