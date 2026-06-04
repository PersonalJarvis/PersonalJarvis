import { useEffect, useMemo, useState } from "react";
import {
  Workflow,
  RefreshCw,
  Play,
  Trash2,
  Clock,
  Calendar,
  CheckCircle2,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  MessageSquare,
  Terminal,
  TerminalSquare,
  Volume2,
  Wrench,
  Tag,
  Loader2,
  PowerOff,
  Power,
  Send,
  Info,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import {
  useDeleteWorkflow,
  useIntegrations,
  useRunDetail,
  useRunWorkflow,
  useToggleWorkflow,
  useWorkflowDetail,
  useWorkflows,
  type IntegrationsResponse,
  type WorkflowSummary,
  type WorkflowRun,
  type WorkflowRunStep,
} from "@/hooks/useWorkflows";

// ---------------------------------------------------------------------
// Icon-Mapping per Step-Kind
// ---------------------------------------------------------------------

const STEP_ICON: Record<string, typeof Clock> = {
  brain_prompt: MessageSquare,
  harness_dispatch: Terminal,
  speak: Volume2,
  tool_call: Wrench,
  shell_cmd: TerminalSquare,
  telegram_send: Send,
};

const STEP_LABEL: Record<string, string> = {
  brain_prompt: "Brain",
  harness_dispatch: "Harness",
  speak: "Sprich",
  tool_call: "Tool",
  shell_cmd: "Shell",
  telegram_send: "Telegram",
};

// ---------------------------------------------------------------------
// View
// ---------------------------------------------------------------------

export function WorkflowsView() {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const { data, isLoading, error, refetch, isRefetching } = useWorkflows();
  const { data: integrations } = useIntegrations();

  const workflows = data?.workflows ?? [];
  const summary = data?.summary;

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Workflow className="h-4 w-4 text-primary" />}
        title="Workflows"
        subtitle="Deine gehosteten AI-Agent-Pipelines — cron-getriggert oder auf Knopfdruck."
        right={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => refetch()}
            disabled={isRefetching}
          >
            <RefreshCw
              className={isRefetching ? "h-4 w-4 animate-spin" : "h-4 w-4"}
            />
          </Button>
        }
      />

      <DashboardStats summary={summary} />
      <IntegrationsBanner integrations={integrations} />

      <ScrollArea className="flex-1">
        <div className="space-y-3 p-6">
          {isLoading && (
            <div className="text-sm text-muted-foreground">
              Lade Workflows…
            </div>
          )}
          {error && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
              Workflows konnten nicht geladen werden: {(error as Error).message}
            </div>
          )}
          {!isLoading && !error && workflows.length === 0 && (
            <EmptyState />
          )}
          {workflows.map((wf) => (
            <WorkflowCard
              key={wf.id}
              workflow={wf}
              isExpanded={!!expanded[wf.id]}
              onToggleExpand={() =>
                setExpanded((prev) => ({ ...prev, [wf.id]: !prev[wf.id] }))
              }
            />
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}

// ---------------------------------------------------------------------
// IntegrationsBanner — zeigt fehlende externe Konfigurationen
// ---------------------------------------------------------------------

function IntegrationsBanner({
  integrations,
}: {
  integrations: IntegrationsResponse | undefined;
}) {
  const [dismissed, setDismissed] = useState(false);
  if (!integrations || dismissed) return null;

  const issues: Array<{ name: string; hint: string; has_partial?: string }> =
    [];
  if (!integrations.telegram.configured) {
    let partial: string | undefined;
    if (integrations.telegram.has_token && !integrations.telegram.has_chat_id) {
      partial = "Token ✓ — Chat-ID fehlt";
    } else if (
      !integrations.telegram.has_token &&
      integrations.telegram.has_chat_id
    ) {
      partial = "Chat-ID ✓ — Token fehlt";
    }
    issues.push({
      name: "Telegram",
      hint: integrations.telegram.setup_hint,
      has_partial: partial,
    });
  }
  if (!integrations.gws_cli.configured) {
    issues.push({
      name: "Google Workspace (gws-CLI)",
      hint: integrations.gws_cli.setup_hint,
    });
  }

  if (issues.length === 0) return null;

  return (
    <div className="border-b border-border bg-primary/5 px-6 py-3">
      <div className="flex items-start gap-3">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold text-foreground">
            Integrationen brauchen Setup ({issues.length})
          </div>
          <ul className="mt-1 space-y-1.5 text-[11px] text-muted-foreground">
            {issues.map((iss) => (
              <li key={iss.name}>
                <span className="font-medium text-foreground">
                  {iss.name}:
                </span>{" "}
                {iss.has_partial && (
                  <span className="text-amber-300">
                    {iss.has_partial} —{" "}
                  </span>
                )}
                {iss.hint}
              </li>
            ))}
          </ul>
        </div>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          className="text-xs text-muted-foreground hover:text-foreground"
          aria-label="Banner schließen"
        >
          ×
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------
// DashboardStats
// ---------------------------------------------------------------------

function DashboardStats({
  summary,
}: {
  summary?: {
    total: number;
    enabled: number;
    cron_enabled: number;
    next_run_at_ns: number | null;
  };
}) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const nextRunLabel = useMemo(() => {
    if (!summary?.next_run_at_ns) return "—";
    return formatFutureDelta(summary.next_run_at_ns);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary?.next_run_at_ns, tick]);

  return (
    <div className="flex items-center gap-4 border-b border-border px-6 py-3">
      <StatBadge
        icon={<Workflow className="h-3.5 w-3.5" />}
        label="Workflows"
        value={String(summary?.total ?? 0)}
      />
      <StatBadge
        icon={<Power className="h-3.5 w-3.5 text-emerald-400" />}
        label="Aktiv"
        value={String(summary?.enabled ?? 0)}
      />
      <StatBadge
        icon={<Calendar className="h-3.5 w-3.5 text-primary" />}
        label="Cron"
        value={String(summary?.cron_enabled ?? 0)}
      />
      <StatBadge
        icon={<Clock className="h-3.5 w-3.5 text-muted-foreground" />}
        label="Nächster Lauf"
        value={nextRunLabel}
      />
    </div>
  );
}

function StatBadge({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-border bg-background/40 px-2.5 py-1.5">
      {icon}
      <div className="flex flex-col">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
        <span className="font-mono text-sm">{value}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------
// WorkflowCard
// ---------------------------------------------------------------------

function WorkflowCard({
  workflow,
  isExpanded,
  onToggleExpand,
}: {
  workflow: WorkflowSummary;
  isExpanded: boolean;
  onToggleExpand: () => void;
}) {
  const runMut = useRunWorkflow();
  const toggleMut = useToggleWorkflow();
  const deleteMut = useDeleteWorkflow();

  const isRunning = runMut.isPending && runMut.variables?.id === workflow.id;

  const handleRun = async () => {
    // Simple-Input-Detection: wenn die Def Input-Variable referenziert,
    // prompten wir synchron via browser-prompt. Fuer komplexere Inputs
    // kommt spaeter ein echter Input-Dialog.
    // Momentan einfach: wir wissen es nicht ohne den Definition-Payload zu
    // laden → einfacher Fallback: Empty-Input. Die URL-Zusammenfassung (ein
    // Seed) schlaegt dann mit "URL leer" fehl, was akzeptabel ist als MVP.
    let input: Record<string, unknown> = {};
    if (workflow.name.toLowerCase().includes("url")) {
      const url = window.prompt("URL zum Zusammenfassen:", "https://");
      if (url === null) return;
      input = { url };
    }
    try {
      await runMut.mutateAsync({ id: workflow.id, input });
    } catch (exc) {
      window.alert(`Run fehlgeschlagen: ${(exc as Error).message}`);
    }
  };

  return (
    <article
      className={cn(
        "card-outline overflow-hidden transition-all",
        "hover:shadow-[0_0_24px_rgba(255,214,10,0.08)]",
      )}
    >
      <header className="flex items-start gap-3 p-4">
        <button
          type="button"
          onClick={onToggleExpand}
          className="mt-0.5 rounded-md p-1 text-muted-foreground transition-colors hover:bg-background/60 hover:text-foreground"
          aria-label={isExpanded ? "Einklappen" : "Ausklappen"}
        >
          {isExpanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </button>

        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-primary/30 bg-primary/5">
          <Workflow className="h-4 w-4 text-primary" />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold">{workflow.name}</h3>
            <TriggerBadge workflow={workflow} />
            <LastRunBadge workflow={workflow} />
            {workflow.created_by === "seed" && (
              <Badge variant="outline" className="text-[10px]">
                Seed
              </Badge>
            )}
          </div>
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
            {workflow.description || "Keine Beschreibung."}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
            <span>
              {workflow.step_count} Step{workflow.step_count === 1 ? "" : "s"}
            </span>
            <NextRunLabel ns={workflow.next_run_at_ns} />
            {workflow.tags.length > 0 && (
              <span className="flex items-center gap-1">
                <Tag className="h-3 w-3" />
                {workflow.tags.join(", ")}
              </span>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <Switch
            checked={workflow.enabled}
            onCheckedChange={(v) =>
              toggleMut.mutate({ id: workflow.id, enabled: v })
            }
            disabled={toggleMut.isPending}
            aria-label={workflow.enabled ? "Deaktivieren" : "Aktivieren"}
          />
          <Button
            size="sm"
            variant="ghost"
            onClick={handleRun}
            disabled={isRunning}
            title="Jetzt ausführen"
          >
            {isRunning ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
          </Button>
          {workflow.created_by !== "seed" && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                if (
                  window.confirm(`Workflow "${workflow.name}" wirklich löschen?`)
                ) {
                  deleteMut.mutate(workflow.id);
                }
              }}
              disabled={deleteMut.isPending}
              title="Löschen"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      </header>

      {isExpanded && <WorkflowDetailBody workflowId={workflow.id} />}
    </article>
  );
}

// ---------------------------------------------------------------------
// Detail-Body (Steps + Recent Runs)
// ---------------------------------------------------------------------

function WorkflowDetailBody({ workflowId }: { workflowId: string }) {
  const { data, isLoading, error } = useWorkflowDetail(workflowId);

  if (isLoading) {
    return (
      <div className="border-t border-border bg-background/30 px-5 py-3 text-xs text-muted-foreground">
        Lade Details…
      </div>
    );
  }
  if (error) {
    return (
      <div className="border-t border-border bg-destructive/10 px-5 py-3 text-xs text-destructive">
        Fehler: {(error as Error).message}
      </div>
    );
  }
  if (!data) return null;

  const def = data.definition as {
    steps?: Array<{ kind: string; label?: string; prompt?: string; text?: string }>;
  };
  const steps = def.steps ?? [];

  return (
    <div className="space-y-4 border-t border-border bg-background/30 px-5 py-4">
      <div>
        <div className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
          Steps ({steps.length})
        </div>
        <ol className="space-y-1.5">
          {steps.map((s, idx) => {
            const Icon = STEP_ICON[s.kind] ?? Terminal;
            const label =
              s.label ||
              (s.prompt ? s.prompt.slice(0, 80) : s.text?.slice(0, 80)) ||
              STEP_LABEL[s.kind] ||
              s.kind;
            return (
              <li
                key={idx}
                className="flex items-center gap-3 rounded-md border border-border/60 bg-card/40 p-2 text-xs"
              >
                <span className="w-6 font-mono text-muted-foreground">
                  #{idx + 1}
                </span>
                <Icon className="h-3.5 w-3.5 shrink-0 text-primary" />
                <span className="w-20 text-primary">
                  {STEP_LABEL[s.kind] ?? s.kind}
                </span>
                <span className="flex-1 truncate text-muted-foreground">
                  {label}
                </span>
              </li>
            );
          })}
        </ol>
      </div>

      <div>
        <div className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
          Letzte Runs ({data.recent_runs.length})
        </div>
        {data.recent_runs.length === 0 ? (
          <div className="text-xs text-muted-foreground">
            Noch nie ausgeführt.
          </div>
        ) : (
          <div className="space-y-2">
            {data.recent_runs.slice(0, 5).map((run) => (
              <RunRow key={run.id} run={run} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function RunRow({ run }: { run: WorkflowRun }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border border-border/60 bg-card/30">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 p-2 text-left text-xs"
      >
        {run.state === "completed" ? (
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
        ) : run.state === "failed" ? (
          <AlertCircle className="h-3.5 w-3.5 text-destructive" />
        ) : run.state === "running" ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
        ) : (
          <Clock className="h-3.5 w-3.5 text-muted-foreground" />
        )}
        <span className="font-mono text-muted-foreground">
          {formatShortTime(run.started_at_ns)}
        </span>
        <Badge variant="outline" className="text-[10px]">
          {run.state}
        </Badge>
        <span className="text-muted-foreground">{run.trigger}</span>
        {run.error && (
          <span className="ml-auto line-clamp-1 text-destructive/80">
            {run.error}
          </span>
        )}
      </button>
      {open && <RunStepsDetail runId={run.id} />}
    </div>
  );
}

function RunStepsDetail({ runId }: { runId: string }) {
  const { data, isLoading } = useRunDetail(runId);
  if (isLoading) {
    return (
      <div className="border-t border-border/60 p-2 text-[11px] text-muted-foreground">
        Lade Run-Details…
      </div>
    );
  }
  const steps: WorkflowRunStep[] = data?.steps ?? [];
  return (
    <ol className="space-y-1 border-t border-border/60 p-2 text-[11px]">
      {steps.map((s) => (
        <li key={s.seq} className="flex items-start gap-2">
          <span className="w-6 font-mono text-muted-foreground">#{s.seq}</span>
          <span
            className={cn(
              "w-20 shrink-0",
              s.success === 1
                ? "text-emerald-400"
                : s.success === 0
                  ? "text-destructive"
                  : "text-primary",
            )}
          >
            {s.label || s.kind}
          </span>
          <span className="flex-1 whitespace-pre-wrap break-words font-mono text-muted-foreground">
            {s.error ?? s.output ?? ""}
          </span>
        </li>
      ))}
    </ol>
  );
}

// ---------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------

function TriggerBadge({ workflow }: { workflow: WorkflowSummary }) {
  if (workflow.trigger_type === "cron") {
    return (
      <Badge
        variant={workflow.enabled ? "default" : "outline"}
        className="text-[10px]"
      >
        <Calendar className="mr-1 h-3 w-3" />
        {workflow.cron_expression || "cron"}
      </Badge>
    );
  }
  return (
    <Badge variant="secondary" className="text-[10px]">
      Manual
    </Badge>
  );
}

function LastRunBadge({ workflow }: { workflow: WorkflowSummary }) {
  if (!workflow.last_run_at_ns) return null;
  const ok = workflow.last_run_state === "completed";
  return (
    <Badge
      variant={ok ? "outline" : "destructive"}
      className="text-[10px]"
      title={formatAbsolute(workflow.last_run_at_ns)}
    >
      {ok ? (
        <CheckCircle2 className="mr-1 h-3 w-3" />
      ) : (
        <AlertCircle className="mr-1 h-3 w-3" />
      )}
      {formatPastDelta(workflow.last_run_at_ns)}
    </Badge>
  );
}

function NextRunLabel({ ns }: { ns: number | null }) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((x) => x + 1), 5000);
    return () => clearInterval(id);
  }, []);
  if (!ns) return null;
  return (
    // eslint-disable-next-line react-hooks/exhaustive-deps
    <span className="text-muted-foreground" title={formatAbsolute(ns)} key={tick}>
      <Clock className="mr-1 inline h-3 w-3" />
      Next in {formatFutureDelta(ns)}
    </span>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border/60 bg-background/30 p-10 text-center">
      <Workflow className="h-7 w-7 text-muted-foreground/50" />
      <p className="text-sm text-muted-foreground">
        Noch keine Workflows — beim ersten Start werden 3 Beispiele geseedet.
      </p>
      <p className="text-xs text-muted-foreground/60">
        Ist das Backend frisch? Schau in <code>data/workflows.sqlite</code>.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------
// Time helpers (alle Timestamps in ns)
// ---------------------------------------------------------------------

function formatFutureDelta(ns: number): string {
  const delta = ns - Date.now() * 1e6;
  if (delta <= 0) return "jetzt";
  return formatDelta(delta);
}

function formatPastDelta(ns: number): string {
  const delta = Date.now() * 1e6 - ns;
  if (delta <= 0) return "jetzt";
  return formatDelta(delta) + " her";
}

function formatDelta(ns: number): string {
  const sec = Math.max(1, Math.round(ns / 1e9));
  if (sec < 60) return `${sec}s`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}min`;
  const hr = Math.round(min / 60);
  if (hr < 48) return `${hr}h`;
  const day = Math.round(hr / 24);
  return `${day}d`;
}

function formatShortTime(ns: number): string {
  try {
    const d = new Date(ns / 1e6);
    return d.toLocaleTimeString();
  } catch {
    return "—";
  }
}

function formatAbsolute(ns: number): string {
  try {
    return new Date(ns / 1e6).toLocaleString();
  } catch {
    return "—";
  }
}

// Silence unused-lint for PowerOff — behalten weil spaeter Bulk-Disable.
void PowerOff;
