/**
 * The staged workspace launcher.
 *
 * Folder, layout, terminal assignments, the reading view and review are
 * separate decisions. The values live in AgenticIdeView rather than in the
 * active step, so moving back never throws work away. Visually this is one
 * continuous work surface: steps are separated by typography and rules, not by
 * stacking cards inside cards.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  ArrowRight,
  AudioLines,
  LayoutGrid,
  Loader2,
  MessagesSquare,
} from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { FolderPicker } from "./FolderPicker";
import { ResumeCard } from "./ResumeCard";
import { AgentAllocation, type PlannedTerminal } from "./AgentAllocation";
import { Button, Notice, SectionLabel } from "./controls";
import { CountStepper, CountTrack, WorkspaceShape } from "./WorkspaceShape";
import type { WorkspaceView } from "./AgenticGrid";
import type { AgentAccount } from "@/lib/agentAccountsApi";
import type {
  AgentStatus,
  RecentWorkspace,
  ResumeOffer,
} from "@/lib/agenticIdeApi";

export type { PlannedTerminal } from "./AgentAllocation";

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

  /** How the workspace opens: the full terminal grid, or the chat view. */
  view: WorkspaceView;
  onView: (next: WorkspaceView) => void;

  onStart: () => void;
}

type LauncherStep = 0 | 1 | 2 | 3 | 4;

const STEPS = [
  {
    id: "folder",
    label: "workspace_launcher.wizard.steps.folder.label",
    title: "workspace_launcher.wizard.steps.folder.title",
    hint: "workspace_launcher.wizard.steps.folder.hint",
  },
  {
    id: "layout",
    label: "workspace_launcher.wizard.steps.layout.label",
    title: "workspace_launcher.wizard.steps.layout.title",
    hint: "workspace_launcher.wizard.steps.layout.hint",
  },
  {
    id: "agents",
    label: "workspace_launcher.wizard.steps.agents.label",
    title: "workspace_launcher.agents.step_title",
    hint: "workspace_launcher.agents.step_hint",
  },
  {
    id: "view",
    label: "workspace_launcher.wizard.steps.view.label",
    title: "workspace_launcher.wizard.steps.view.title",
    hint: "workspace_launcher.wizard.steps.view.hint",
  },
  {
    id: "review",
    label: "workspace_launcher.wizard.steps.review.label",
    title: "workspace_launcher.wizard.steps.review.title",
    hint: "workspace_launcher.wizard.steps.review.hint",
  },
] as const;

export function workspaceLaunchShortcut(platform = navigator.platform): string {
  return /mac/i.test(platform) ? "⌘↵" : "Ctrl+↵";
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
  view,
  onView,
  onStart,
}: WorkspaceLauncherProps) {
  const t = useT();
  const [step, setStep] = useState<LauncherStep>(0);
  const planReady =
    planned.length > 0 &&
    planned.every((pane) => pane.name.trim() && pane.agent);
  const ready = Boolean(folder) && planReady;

  const canLeaveCurrent =
    (step === 0 && Boolean(folder)) ||
    (step === 1 && count > 0) ||
    (step === 2 && planReady) ||
    // The view step always carries a preselected answer, and review is last.
    step >= 3;

  const canVisit = (target: LauncherStep) => {
    if (target <= step) return true;
    if (!folder) return false;
    if (target <= 2) return true;
    return planReady;
  };

  /* The launch chord is intentionally limited to the review step. */
  useEffect(() => {
    if (step !== 4 || !ready || busy) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Enter" || !(event.metaKey || event.ctrlKey)) return;
      event.preventDefault();
      onStart();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onStart, ready, step]);

  const active = STEPS[step];
  const activeTitle = t(active.title);
  const activeHint = t(active.hint);

  return (
    <div
      data-testid="workspace-launcher"
      className="flex h-full min-h-0 flex-col font-display"
    >
      <header className="shrink-0 border-b border-border/70 px-5 py-5 sm:px-8">
        <div className="mx-auto flex w-full max-w-6xl items-start justify-between gap-6">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary/80">
              {t(
                addingNew
                  ? "workspace_launcher.wizard.additional_workspace"
                  : "workspace_launcher.wizard.new_workspace",
              )}
              <span className="px-2 text-muted-foreground/50">/</span>
              {t("workspace_launcher.wizard.step_progress")
                .replace("{0}", String(step + 1))
                .replace("{1}", String(STEPS.length))}
            </p>
            <h2
              id="workspace-launcher-title"
              className="mt-2 text-xl font-semibold tracking-tight text-foreground sm:text-2xl"
            >
              {activeTitle}
            </h2>
            <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">
              {activeHint}
              {addingNew && step === 0
                ? ` ${t("workspace_launcher.wizard.running_notice")}`
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
                    {t("workspace_launcher.wizard.terminal_unavailable_before")} {" "}
                    <code className="font-mono">pywinpty</code> (Windows),{" "}
                    <code className="font-mono">ptyprocess</code> (macOS/Linux).{" "}
                    {t("workspace_launcher.wizard.terminal_unavailable_after")}
                  </span>
                </Notice>
              )}

              {nothingInstalled && (
                <Notice tone="warning">
                  <span>
                    {t("workspace_launcher.wizard.no_cli")}
                  </span>
                  <Button
                    variant="subtle"
                    className="h-6 px-2 text-amber-200/90"
                    onClick={onOpenClis}
                  >
                    {t("workspace_launcher.wizard.open_clis")}
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
              view={view}
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
                      <SectionLabel>
                        {t("workspace_launcher.wizard.terminal_panes")}
                      </SectionLabel>
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
                  />
                  <CountTrack
                    count={count}
                    max={maxTerminals}
                    onChange={onCount}
                  />
                </div>
              )}

              {step === 2 && (
                <AgentAllocation
                  planned={planned}
                  agents={agents}
                  accountsFor={accountsFor}
                  onPlanned={onPlanned}
                />
              )}

              {step === 3 && <ViewChoice view={view} onView={onView} />}

              {step === 4 && folder && (
                <WorkspaceReview
                  folder={folder}
                  planned={planned}
                  agents={agents}
                  view={view}
                />
              )}

              <footer className="mt-7 flex min-h-10 items-center justify-between border-t border-border/70 pt-5">
                {step > 0 ? (
                  <Button
                    variant="subtle"
                    onClick={() => setStep((step - 1) as LauncherStep)}
                  >
                    <ArrowLeft className="h-3.5 w-3.5" />
                    {t("workspace_launcher.wizard.back")}
                  </Button>
                ) : (
                  <span />
                )}

                {step < 4 ? (
                  <Button
                    variant="primary"
                    disabled={!canLeaveCurrent}
                    onClick={() => setStep((step + 1) as LauncherStep)}
                    className="px-4"
                  >
                    {step === 0
                      ? t("workspace_launcher.wizard.continue_layout")
                      : step === 1
                        ? t("workspace_launcher.wizard.continue_agents")
                        : step === 2
                          ? t("workspace_launcher.wizard.choose_view")
                          : t("workspace_launcher.wizard.review_workspace")}
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
                    {busy
                      ? t("workspace_launcher.wizard.opening")
                      : t("workspace_launcher.wizard.open_workspace")}
                    {!busy && (
                      <kbd className="ml-1 hidden font-mono text-[10px] font-normal opacity-60 sm:inline">
                        {workspaceLaunchShortcut()}
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
  view,
  canVisit,
  onStep,
}: {
  step: LauncherStep;
  folder: string | null;
  count: number;
  planned: PlannedTerminal[];
  view: WorkspaceView;
  canVisit: (target: LauncherStep) => boolean;
  onStep: (step: LauncherStep) => void;
}) {
  const t = useT();
  const assigned = planned.filter((pane) => Boolean(pane.agent)).length;
  const summaries = useMemo(
    () => [
      folder ? leafName(folder) : t("workspace_launcher.wizard.not_chosen"),
      t(
        count === 1
          ? "workspace_launcher.wizard.one_terminal"
          : "workspace_launcher.wizard.many_terminals",
      ).replace("{0}", String(count)),
      t("workspace_launcher.agents.nav_summary")
        .replace("{0}", String(assigned))
        .replace("{1}", String(planned.length)),
      t(`workspace_launcher.wizard.views.${view}.title`),
      t("workspace_launcher.wizard.check_and_open"),
    ],
    [assigned, count, folder, planned.length, t, view],
  );

  return (
    <nav
      aria-label={t("workspace_launcher.wizard.setup_label")}
      className="min-w-0"
    >
      <ol className="grid grid-cols-5 border-b border-border/70 lg:flex lg:flex-col lg:border-b-0 lg:border-r lg:pr-6">
        {STEPS.map((item, index) => {
          const target = index as LauncherStep;
          const selected = target === step;
          const enabled = canVisit(target);
          return (
            <li key={item.id}>
              <button
                type="button"
                data-testid={`launcher-step-${item.id}`}
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
                  {t(item.label)}
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

/**
 * The last decision before review: how the workspace is read.
 *
 * Three ways of looking at the SAME panes (see WorkspaceView in AgenticGrid) —
 * the grid shows every terminal at once, chat puts one agent on a stage like a
 * conversation, and the deck hands the floor to Jarvis. A choice of
 * presentation, not of substance, which is why the step needs no gate: every
 * answer is valid and one is preselected.
 *
 * The deck's card says what it DOES differently rather than what it looks
 * like, because it is the one option that changes the assistant's behaviour:
 * pick it and Jarvis starts speaking finished work back to you.
 */
function ViewChoice({
  view,
  onView,
}: {
  view: WorkspaceView;
  onView: (next: WorkspaceView) => void;
}) {
  const t = useT();
  return (
    <div>
      <SectionLabel>{t("workspace_launcher.wizard.reading_mode")}</SectionLabel>
      <div
        role="radiogroup"
        aria-label={t("workspace_launcher.wizard.reading_mode_label")}
        className="mt-4 grid gap-4 sm:grid-cols-3"
      >
        <ViewOption
          selected={view === "grid"}
          onSelect={() => onView("grid")}
          testId="view-choice-grid"
          icon={<LayoutGrid className="h-4 w-4 shrink-0" />}
          title={t("workspace_launcher.wizard.views.grid.title")}
          description={t("workspace_launcher.wizard.views.grid.description")}
          preview={<GridPreview />}
        />
        <ViewOption
          selected={view === "chat"}
          onSelect={() => onView("chat")}
          testId="view-choice-chat"
          icon={<MessagesSquare className="h-4 w-4 shrink-0" />}
          title={t("workspace_launcher.wizard.views.chat.title")}
          description={t("workspace_launcher.wizard.views.chat.description")}
          preview={<ChatPreview />}
        />
        <ViewOption
          selected={view === "deck"}
          onSelect={() => onView("deck")}
          testId="view-choice-deck"
          icon={<AudioLines className="h-4 w-4 shrink-0" />}
          title={t("workspace_launcher.wizard.views.deck.title")}
          description={t("workspace_launcher.wizard.views.deck.description")}
          preview={<DeckPreview />}
        />
      </div>
      <p className="mt-5 max-w-2xl text-xs leading-relaxed text-muted-foreground">
        {t("workspace_launcher.wizard.view_hint")}
      </p>
    </div>
  );
}

function ViewOption({
  selected,
  onSelect,
  testId,
  icon,
  title,
  description,
  preview,
}: {
  selected: boolean;
  onSelect: () => void;
  testId: string;
  icon: ReactNode;
  title: string;
  description: string;
  preview: ReactNode;
}) {
  const t = useT();
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      data-testid={testId}
      onClick={onSelect}
      className={cn(
        "group min-w-0 border px-5 py-5 text-left transition-colors",
        selected
          ? "border-primary/70 bg-primary/[0.04]"
          : "border-border/70 hover:border-border",
      )}
    >
      {preview}
      <span
        className={cn(
          "mt-4 flex items-center gap-2 text-sm font-medium",
          selected ? "text-foreground" : "text-muted-foreground",
        )}
      >
        {icon}
        {title}
        {selected && (
          <span className="ml-auto text-[10px] font-semibold uppercase tracking-[0.14em] text-primary">
            {t("workspace_launcher.wizard.selected")}
          </span>
        )}
      </span>
      <span className="mt-1.5 block text-xs leading-relaxed text-muted-foreground">
        {description}
      </span>
    </button>
  );
}

/*
 * Miniatures of the three modes, drawn with rules like the rest of the wizard.
 *
 * These panes use the foreground token at low opacity instead of the already
 * dim border/muted tokens. On the matte dark theme, tinting a 14%-light border
 * again made the geometry nearly disappear; this keeps the contrast local to
 * the diagrams and gives the outlines roughly twice their former luminance.
 */
const VIEW_PREVIEW_PANE =
  "border-foreground/20 bg-foreground/[0.08]";
const VIEW_PREVIEW_PANE_ACTIVE =
  "border-foreground/20 bg-foreground/[0.14]";

function GridPreview() {
  return (
    <span aria-hidden className="grid grid-cols-2 gap-1.5">
      {[0, 1, 2, 3].map((i) => (
        <span
          key={i}
          data-view-preview-pane
          className={cn("block h-9 border", VIEW_PREVIEW_PANE)}
        />
      ))}
    </span>
  );
}

function ChatPreview() {
  return (
    <span aria-hidden className="flex gap-1.5">
      <span className="flex w-1/4 flex-col gap-1.5">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            data-view-preview-pane
            className={cn(
              "block flex-1 border",
              i === 0 ? VIEW_PREVIEW_PANE_ACTIVE : VIEW_PREVIEW_PANE,
            )}
          />
        ))}
      </span>
      <span
        data-view-preview-pane
        className={cn("block h-[4.875rem] flex-1 border", VIEW_PREVIEW_PANE)}
      />
    </span>
  );
}

/*
 * The deck miniature is the odd one out on purpose.
 *
 * The other two draw their layout, because their layout IS the difference. The
 * deck's difference is that the conversation is at the centre and the
 * terminals have receded to cards around it — so it draws a ring with the
 * agents arranged about it, which is the one glance that distinguishes it from
 * "chat view with bigger panes".
 */
function DeckPreview() {
  return (
    <span aria-hidden className="flex h-[4.875rem] items-center justify-center gap-2">
      <span className="flex flex-col gap-1.5">
        {[0, 1].map((i) => (
          <span
            key={i}
            data-view-preview-pane
            className={cn("block h-6 w-8 border", VIEW_PREVIEW_PANE)}
          />
        ))}
      </span>
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-primary/60 bg-primary/10">
        <span className="block h-4 w-4 rounded-full border border-primary/70 bg-primary/25" />
      </span>
      <span className="flex flex-col gap-1.5">
        {[0, 1].map((i) => (
          <span
            key={i}
            data-view-preview-pane
            className={cn("block h-6 w-8 border", VIEW_PREVIEW_PANE)}
          />
        ))}
      </span>
    </span>
  );
}

function WorkspaceReview({
  folder,
  planned,
  agents,
  view,
}: {
  folder: string;
  planned: PlannedTerminal[];
  agents: AgentStatus[];
  view: WorkspaceView;
}) {
  const t = useT();
  const displayName = (agentId: string) =>
    agents.find((agent) => agent.name === agentId)?.display_name ?? agentId;

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1.1fr)_minmax(18rem,0.9fr)]">
      <div className="min-w-0">
        <SectionLabel>{t("workspace_launcher.wizard.workspace")}</SectionLabel>
        <h4 className="mt-3 truncate text-2xl font-semibold tracking-tight text-foreground">
          {leafName(folder)}
        </h4>
        <code className="mt-2 block break-all font-mono text-xs leading-relaxed text-muted-foreground">
          {folder}
        </code>

        <dl className="mt-7 border-y border-border/70">
          <div className="flex items-center justify-between gap-5 border-b border-border/50 py-3 text-sm">
            <dt className="text-muted-foreground">
              {t("workspace_launcher.wizard.terminal_panes")}
            </dt>
            <dd className="font-mono tabular-nums text-foreground">
              {planned.length}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-5 border-b border-border/50 py-3 text-sm">
            <dt className="text-muted-foreground">
              {t("workspace_launcher.wizard.opens_as")}
            </dt>
            <dd className="text-foreground" data-testid="review-view-mode">
              {t(`workspace_launcher.wizard.views.${view}.title`)}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-5 py-3 text-sm">
            <dt className="text-muted-foreground">
              {t("workspace_launcher.wizard.coding_mode")}
            </dt>
            <dd className="text-foreground">
              {t("workspace_launcher.wizard.coding_mode_on_open")}
            </dd>
          </div>
        </dl>
      </div>

      <div className="min-w-0">
        <SectionLabel>{t("workspace_launcher.wizard.terminal_plan")}</SectionLabel>
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
