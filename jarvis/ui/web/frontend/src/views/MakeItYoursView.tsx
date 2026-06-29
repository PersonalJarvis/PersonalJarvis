import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Wand2,
  Terminal,
  Check,
  Plus,
  Minus,
  Loader2,
  FolderOpen,
  RefreshCw,
  Rocket,
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Download,
  X,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { WorkspaceTerminal } from "@/components/workspace/WorkspaceTerminal";
import {
  fetchWorkspaceAgents,
  launchWorkspace,
  type AgentsResponse,
  type Slot,
  type WorkspaceAgent,
} from "@/lib/workspaceApi";

// Columns used to draw each layout tile's dot-grid preview (mirrors BridgeSpace).
const TILE_COLS: Record<number, number> = { 1: 1, 2: 2, 4: 2, 6: 3, 8: 4, 10: 5, 12: 4 };

type Counts = Record<string, number>;
type Step = 0 | 1 | 2;
type Session =
  | { kind: "grid"; slots: Slot[]; cwd: string }
  | { kind: "install"; name: string; display: string }
  | null;

function gridCols(n: number): number {
  if (n <= 1) return 1;
  if (n <= 2) return 2;
  return Math.min(4, Math.ceil(Math.sqrt(n)));
}

export function MakeItYoursView() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);

  const [data, setData] = useState<AgentsResponse | null>(null);
  const [step, setStep] = useState<Step>(0);
  const [layout, setLayout] = useState(1);
  const [counts, setCounts] = useState<Counts>({ claude: 1, codex: 0 });
  const [launching, setLaunching] = useState(false);
  const [session, setSession] = useState<Session>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await fetchWorkspaceAgents());
    } catch {
      /* offline / headless — keep prior state */
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const agentsByName = useMemo(() => {
    const m: Record<string, WorkspaceAgent> = {};
    for (const a of data?.agents ?? []) m[a.name] = a;
    return m;
  }, [data]);

  const layoutChoices = data?.layout_choices ?? [1, 2, 4, 6, 8, 10, 12];
  const maxLayout = Math.max(...layoutChoices);
  const assigned = Object.values(counts).reduce((s, n) => s + n, 0);
  const selectedNames = Object.keys(counts).filter((n) => counts[n] > 0);
  const missing = selectedNames.filter((n) => !agentsByName[n]?.installed);
  const canLaunch =
    assigned === layout && missing.length === 0 && (data?.terminal_available ?? true);

  const chooseLayout = (n: number) => {
    setLayout(n);
    setCounts({ claude: n, codex: 0 }); // default: all Claude, user re-splits
  };

  const inc = (name: string) =>
    setCounts((c) => (assigned < layout ? { ...c, [name]: (c[name] ?? 0) + 1 } : c));
  const dec = (name: string) =>
    setCounts((c) => ({ ...c, [name]: Math.max(0, (c[name] ?? 0) - 1) }));

  const quickFill = (mode: "even" | "claude" | "codex") => {
    if (mode === "claude") setCounts({ claude: layout, codex: 0 });
    else if (mode === "codex") setCounts({ claude: 0, codex: layout });
    else setCounts({ claude: Math.ceil(layout / 2), codex: Math.floor(layout / 2) });
  };

  const onInstall = (name: string) => {
    const a = agentsByName[name];
    setSession({ kind: "install", name, display: a?.display_name ?? name });
  };

  const onLaunch = async () => {
    setLaunching(true);
    try {
      const plan = await launchWorkspace(layout, counts);
      if (plan.ok && plan.slots.length) {
        setSession({ kind: "grid", slots: plan.slots, cwd: plan.cwd });
      } else {
        pushToast("error", plan.detail || t("make_it_yours.launch_failed"));
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setLaunching(false);
    }
  };

  const endSession = () => {
    const wasInstall = session?.kind === "install";
    setSession(null);
    if (wasInstall) void refresh();
  };

  // ----- Running modes (terminals embedded in-app) -----
  if (session?.kind === "grid") {
    return (
      <RunningGrid t={t} slots={session.slots} cwd={session.cwd} onEnd={endSession} />
    );
  }
  if (session?.kind === "install") {
    return (
      <InstallPanel t={t} name={session.name} display={session.display} onDone={endSession} />
    );
  }

  // ----- Setup flow -----
  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Wand2 className="h-4 w-4 text-primary" />}
        title={t("make_it_yours.title")}
        subtitle={t("make_it_yours.subtitle")}
      />
      <div className="flex-1 overflow-y-auto scrollbar-jarvis">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-6">
          {/* Workspace-size meter: fills with the chosen terminal count (selected / max). */}
          <div className="flex items-center gap-3">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
              <div
                data-testid="terminal-fill"
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${Math.min(100, (layout / maxLayout) * 100)}%` }}
              />
            </div>
            <span className="font-mono text-xs text-muted-foreground">
              {layout} / {maxLayout}
            </span>
          </div>

          {!data?.terminal_available && data != null && (
            <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{t("make_it_yours.no_terminal")}</span>
            </div>
          )}

          {step === 0 && (
            <LayoutStep t={t} choices={layoutChoices} selected={layout} onSelect={chooseLayout} />
          )}
          {step === 1 && (
            <AgentsStep
              t={t}
              layout={layout}
              assigned={assigned}
              counts={counts}
              agents={data?.agents ?? []}
              onInc={inc}
              onDec={dec}
              onQuickFill={quickFill}
              onInstall={onInstall}
              onRecheck={refresh}
            />
          )}
          {step === 2 && (
            <ConfirmStep t={t} counts={counts} agentsByName={agentsByName} cwd={data?.cwd ?? ""} />
          )}

          <div className="flex items-center justify-between pt-2">
            <button
              type="button"
              className={cn("btn-ghost", step === 0 && "invisible")}
              onClick={() => setStep((s) => (s > 0 ? ((s - 1) as Step) : s))}
            >
              <ArrowLeft className="h-4 w-4" />
              {t("make_it_yours.back")}
            </button>

            {step < 2 ? (
              <button
                type="button"
                className="btn-primary disabled:cursor-not-allowed disabled:opacity-40"
                disabled={step === 1 && (assigned !== layout || missing.length > 0)}
                onClick={() => setStep((s) => ((s + 1) as Step))}
              >
                {t("make_it_yours.next")}
                <ArrowRight className="h-4 w-4" />
              </button>
            ) : (
              <button
                type="button"
                className="btn-primary disabled:cursor-not-allowed disabled:opacity-40"
                disabled={!canLaunch || launching}
                onClick={onLaunch}
              >
                {launching ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Rocket className="h-4 w-4" />
                )}
                {launching ? t("make_it_yours.launching") : t("make_it_yours.launch")}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function RunningGrid({
  t,
  slots,
  cwd,
  onEnd,
}: {
  t: (k: string) => string;
  slots: Slot[];
  cwd: string;
  onEnd: () => void;
}) {
  const cols = gridCols(slots.length);
  // Per-agent numbering for the titles ("Claude Code 1", "Claude Code 2", …).
  const perAgent: Record<string, number> = {};
  const titled = slots.map((s) => {
    perAgent[s.agent] = (perAgent[s.agent] ?? 0) + 1;
    return { ...s, title: `${s.display_name} ${perAgent[s.agent]}` };
  });

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <Wand2 className="h-4 w-4 shrink-0 text-primary" />
          <span className="font-display text-sm font-semibold">
            {slots.length} {t("make_it_yours.terminals")}
          </span>
          <code className="truncate font-mono text-xs text-muted-foreground">{cwd}</code>
        </div>
        <button type="button" className="btn-ghost" onClick={onEnd}>
          <X className="h-4 w-4" />
          {t("make_it_yours.end_session")}
        </button>
      </div>
      <div
        className="grid flex-1 gap-2 overflow-hidden p-2"
        style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gridAutoRows: "1fr" }}
      >
        {titled.map((s) => (
          <WorkspaceTerminal
            key={s.index}
            paneKey={`slot-${s.index}`}
            agentName={s.agent}
            title={s.title}
          />
        ))}
      </div>
    </div>
  );
}

function InstallPanel({
  t,
  name,
  display,
  onDone,
}: {
  t: (k: string) => string;
  name: string;
  display: string;
  onDone: () => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <div className="flex items-center gap-2">
          <Download className="h-4 w-4 text-primary" />
          <span className="font-display text-sm font-semibold">
            {t("make_it_yours.install")} · {display}
          </span>
        </div>
        <button type="button" className="btn-primary" onClick={onDone}>
          <Check className="h-4 w-4" />
          {t("make_it_yours.install_done")}
        </button>
      </div>
      <div className="flex-1 overflow-hidden p-3">
        <WorkspaceTerminal paneKey={`install-${name}`} installName={name} title={`install ${name}`} />
      </div>
    </div>
  );
}

function LayoutStep({
  t,
  choices,
  selected,
  onSelect,
}: {
  t: (k: string) => string;
  choices: number[];
  selected: number;
  onSelect: (n: number) => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="font-display text-lg font-semibold">{t("make_it_yours.step_layout")}</h3>
        <p className="text-sm text-muted-foreground">{t("make_it_yours.step_layout_hint")}</p>
      </div>
      <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 lg:grid-cols-7">
        {choices.map((n) => (
          <button
            key={n}
            type="button"
            aria-pressed={selected === n}
            onClick={() => onSelect(n)}
            className={cn(
              "flex aspect-square flex-col items-center justify-center gap-2 rounded-xl border p-3 transition-colors",
              selected === n
                ? "border-primary/50 bg-primary/5 shadow-[0_0_0_1px_hsl(var(--primary)/0.2)]"
                : "border-border bg-card/60 hover:border-primary/30",
            )}
          >
            <DotGrid n={n} active={selected === n} />
            <span
              className={cn(
                "text-sm font-semibold",
                selected === n ? "text-primary" : "text-foreground",
              )}
            >
              {n}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function DotGrid({ n, active }: { n: number; active: boolean }) {
  const cols = TILE_COLS[n] ?? Math.min(n, 4);
  return (
    <div className="grid gap-1" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
      {Array.from({ length: n }).map((_, i) => (
        <span
          key={i}
          className={cn("h-2 w-2 rounded-[3px]", active ? "bg-primary" : "bg-muted-foreground/40")}
        />
      ))}
    </div>
  );
}

function AgentsStep({
  t,
  layout,
  assigned,
  counts,
  agents,
  onInc,
  onDec,
  onQuickFill,
  onInstall,
  onRecheck,
}: {
  t: (k: string) => string;
  layout: number;
  assigned: number;
  counts: Counts;
  agents: WorkspaceAgent[];
  onInc: (name: string) => void;
  onDec: (name: string) => void;
  onQuickFill: (mode: "even" | "claude" | "codex") => void;
  onInstall: (name: string) => void;
  onRecheck: () => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="font-display text-lg font-semibold">{t("make_it_yours.step_agents")}</h3>
        <p className="text-sm text-muted-foreground">{t("make_it_yours.step_agents_hint")}</p>
      </div>

      <div className="flex items-center gap-3">
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
          <div
            className={cn(
              "h-full rounded-full transition-all",
              assigned === layout ? "bg-primary" : "bg-primary/60",
            )}
            style={{ width: `${Math.min(100, (assigned / layout) * 100)}%` }}
          />
        </div>
        <span
          className={cn(
            "font-mono text-sm",
            assigned === layout ? "text-primary" : "text-muted-foreground",
          )}
        >
          {assigned} / {layout}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          {t("make_it_yours.quick_fill")}
        </span>
        <button type="button" className="chip hover:border-primary/40" onClick={() => onQuickFill("even")}>
          {t("make_it_yours.split_evenly")}
        </button>
        <button type="button" className="chip hover:border-primary/40" onClick={() => onQuickFill("claude")}>
          {t("make_it_yours.all_claude")}
        </button>
        <button type="button" className="chip hover:border-primary/40" onClick={() => onQuickFill("codex")}>
          {t("make_it_yours.all_codex")}
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {agents.map((a) => (
          <AgentCard
            key={a.name}
            t={t}
            agent={a}
            count={counts[a.name] ?? 0}
            canInc={assigned < layout}
            onInc={() => onInc(a.name)}
            onDec={() => onDec(a.name)}
            onInstall={() => onInstall(a.name)}
            onRecheck={onRecheck}
          />
        ))}
      </div>
    </div>
  );
}

function AgentCard({
  t,
  agent,
  count,
  canInc,
  onInc,
  onDec,
  onInstall,
  onRecheck,
}: {
  t: (k: string) => string;
  agent: WorkspaceAgent;
  count: number;
  canInc: boolean;
  onInc: () => void;
  onDec: () => void;
  onInstall: () => void;
  onRecheck: () => void;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-xl border p-4",
        count > 0 ? "border-primary/40 bg-primary/5" : "border-border bg-card/60",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Terminal className="h-4 w-4 text-primary" />
          <span className="font-medium">{agent.display_name}</span>
        </div>
        {agent.installed ? (
          <span className="flex items-center gap-1 text-xs text-emerald-400">
            <Check className="h-3.5 w-3.5" />
            {agent.version ?? t("make_it_yours.installed")}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">{t("make_it_yours.not_installed")}</span>
        )}
      </div>

      {agent.installed ? (
        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            aria-label="decrease"
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-border hover:border-primary/40 disabled:opacity-30"
            disabled={count <= 0}
            onClick={onDec}
          >
            <Minus className="h-4 w-4" />
          </button>
          <span className="w-8 text-center font-mono text-base font-semibold">{count}</span>
          <button
            type="button"
            aria-label="increase"
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-border hover:border-primary/40 disabled:opacity-30"
            disabled={!canInc}
            onClick={onInc}
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <button type="button" className="btn-ghost flex-1 justify-center" onClick={onInstall}>
            <Download className="h-4 w-4" />
            {t("make_it_yours.install")}
          </button>
          <button
            type="button"
            aria-label="re-check"
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-border hover:border-primary/40"
            onClick={onRecheck}
          >
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  );
}

function ConfirmStep({
  t,
  counts,
  agentsByName,
  cwd,
}: {
  t: (k: string) => string;
  counts: Counts;
  agentsByName: Record<string, WorkspaceAgent>;
  cwd: string;
}) {
  const lines = Object.keys(counts)
    .filter((n) => counts[n] > 0)
    .map((n) => `${counts[n]} × ${agentsByName[n]?.display_name ?? n}`);

  return (
    <div className="space-y-4">
      <h3 className="font-display text-lg font-semibold">{t("make_it_yours.step_confirm")}</h3>
      <div className="rounded-xl border border-border bg-card/60 p-5">
        <div className="flex flex-wrap items-center gap-2 text-lg font-medium">
          {lines.map((l, i) => (
            <span key={i} className="rounded-lg bg-primary/10 px-3 py-1 text-primary">
              {l}
            </span>
          ))}
        </div>
        <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
          <FolderOpen className="h-4 w-4 shrink-0 text-primary" />
          <span>{t("make_it_yours.folder")}:</span>
          <code className="truncate font-mono text-xs text-foreground">{cwd}</code>
        </div>
      </div>
    </div>
  );
}
