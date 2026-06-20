import { useMemo, useState } from "react";
import {
  Orbit,
  RefreshCw,
  Play,
  Plus,
  Trash2,
  Clock,
  Calendar,
  Timer,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Globe,
  Sparkles,
  TerminalSquare,
  Webhook,
  X,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { translate, useT } from "@/i18n";
import {
  useConductorDashboard,
  useCreateConductorJob,
  useDeleteConductorJob,
  useRunConductorJob,
  useToggleConductorJob,
  type JobSummary,
  type RunRow,
} from "@/hooks/useConductor";

// ---------------------------------------------------------------------
// Icons + Labels
// ---------------------------------------------------------------------

const TYPE_ICON: Record<string, typeof Clock> = {
  shell: TerminalSquare,
  http: Globe,
  agent: Sparkles,
};

const TYPE_LABEL: Record<string, string> = {
  shell: "Shell",
  http: "HTTP",
  agent: "Agent",
};

const SCHED_ICON: Record<string, typeof Clock> = {
  cron: Calendar,
  interval: Timer,
  manual: Play,
  webhook: Webhook,
};

// ---------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------

export function ConductorView() {
  const t = useT();
  const { data, isLoading, error, refetch, isRefetching } =
    useConductorDashboard();
  const [editorOpen, setEditorOpen] = useState(false);

  const jobs = data?.jobs ?? [];
  const runs = data?.recent_runs ?? [];
  const summary = data?.summary;
  const jobById = useMemo(() => {
    const m = new Map<string, JobSummary>();
    for (const j of jobs) m.set(j.id, j);
    return m;
  }, [jobs]);

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Orbit className="h-4 w-4 text-primary" />}
        title="Conductor"
        subtitle="Schedule Tasks + Agentic Workflows — Open-Source, self-hosted, code-first."
        right={
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setEditorOpen(true)}
            >
              <Plus className="mr-1 h-4 w-4" />
              {t("conductor_view.new_job")}
            </Button>
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
          </div>
        }
      />

      <SummaryBar summary={summary} jobCount={jobs.length} runCount={runs.length} />

      <div className="flex flex-1 overflow-hidden">
        {/* Links: Job-Katalog */}
        <div className="flex w-[46%] flex-col border-r border-border">
          <div className="px-5 py-2 text-[10px] uppercase tracking-wider text-muted-foreground">
            Jobs
          </div>
          <ScrollArea className="flex-1">
            <div className="space-y-2 px-5 pb-6">
              {isLoading && (
                <div className="text-sm text-muted-foreground">{t("conductor_view.loading_jobs")}</div>
              )}
              {error && (
                <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
                  {t("conductor_view.api_unreachable")}: {(error as Error).message}
                </div>
              )}
              {!isLoading && !error && jobs.length === 0 && <EmptyJobs />}
              {jobs.map((j) => (
                <JobCard key={j.id} job={j} />
              ))}
            </div>
          </ScrollArea>
        </div>

        {/* Rechts: Timeline */}
        <div className="flex flex-1 flex-col">
          <div className="px-5 py-2 text-[10px] uppercase tracking-wider text-muted-foreground">
            Timeline
          </div>
          <ScrollArea className="flex-1">
            <div className="space-y-1.5 px-5 pb-6">
              {runs.length === 0 ? (
                <EmptyTimeline />
              ) : (
                runs.map((r) => (
                  <TimelineRow
                    key={r.id}
                    run={r}
                    job={jobById.get(r.job_id)}
                  />
                ))
              )}
            </div>
          </ScrollArea>
        </div>
      </div>

      {editorOpen && <JobEditorModal onClose={() => setEditorOpen(false)} />}
    </div>
  );
}

// ---------------------------------------------------------------------
// SummaryBar
// ---------------------------------------------------------------------

function SummaryBar({
  summary,
  jobCount,
  runCount,
}: {
  summary?: { total: number; enabled: number; by_type: Record<string, number> };
  jobCount: number;
  runCount: number;
}) {
  const t = useT();
  return (
    <div className="flex items-center gap-3 border-b border-border bg-background/30 px-5 py-2.5">
      <StatChip
        icon={<Orbit className="h-3.5 w-3.5" />}
        label="Jobs"
        value={String(summary?.total ?? jobCount)}
      />
      <StatChip
        icon={<Play className="h-3.5 w-3.5 text-emerald-400" />}
        label={t("conductor_view.stat_active")}
        value={String(summary?.enabled ?? 0)}
      />
      <StatChip
        icon={<Sparkles className="h-3.5 w-3.5 text-primary" />}
        label="Agent"
        value={String(summary?.by_type?.agent ?? 0)}
      />
      <StatChip
        icon={<Globe className="h-3.5 w-3.5" />}
        label="HTTP"
        value={String(summary?.by_type?.http ?? 0)}
      />
      <StatChip
        icon={<TerminalSquare className="h-3.5 w-3.5" />}
        label="Shell"
        value={String(summary?.by_type?.shell ?? 0)}
      />
      <div className="ml-auto text-xs text-muted-foreground">
        {runCount} {t("conductor_view.runs_last_30")}
      </div>
    </div>
  );
}

function StatChip({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-1.5 rounded-md border border-border bg-card/40 px-2 py-1">
      {icon}
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className="font-mono text-sm">{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------
// JobCard
// ---------------------------------------------------------------------

function JobCard({ job }: { job: JobSummary }) {
  const t = useT();
  const runMut = useRunConductorJob();
  const toggleMut = useToggleConductorJob();
  const deleteMut = useDeleteConductorJob();
  const TypeIcon = TYPE_ICON[job.type] ?? TerminalSquare;
  const SchedIcon = SCHED_ICON[job.schedule_type] ?? Clock;
  const isRunning = runMut.isPending && runMut.variables?.id === job.id;

  return (
    <article className="card-outline overflow-hidden">
      <div className="flex items-start gap-3 p-3.5">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-primary/30 bg-primary/5">
          <TypeIcon className="h-4 w-4 text-primary" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-sm font-semibold">{job.name}</h3>
            <Badge variant="outline" className="text-[10px]">
              {TYPE_LABEL[job.type] ?? job.type}
            </Badge>
            <Badge
              variant={job.schedule_type === "cron" ? "default" : "secondary"}
              className="text-[10px]"
            >
              <SchedIcon className="mr-1 h-3 w-3" />
              {job.schedule_expr ?? job.schedule_type}
            </Badge>
            {job.last_run_state && (
              <LastRunChip
                state={job.last_run_state}
                at={job.last_run_at_ns}
              />
            )}
          </div>
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
            {job.description || "—"}
          </p>
          {job.tags.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {job.tags.map((tag) => (
                <span
                  key={tag}
                  className="rounded-full border border-border bg-background/60 px-2 py-0.5 text-[10px] text-muted-foreground"
                >
                  #{tag}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Switch
            checked={job.enabled}
            onCheckedChange={(v) =>
              toggleMut.mutate({ id: job.id, enabled: v })
            }
            disabled={toggleMut.isPending}
          />
          <Button
            size="sm"
            variant="ghost"
            onClick={() => runMut.mutate({ id: job.id, input: {} })}
            disabled={isRunning}
          >
            {isRunning ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              if (
                window.confirm(
                  `${t("conductor_view.delete_confirm_prefix")} "${job.name}" ${t(
                    "conductor_view.delete_confirm_suffix",
                  )}`,
                )
              )
                deleteMut.mutate(job.id);
            }}
            disabled={deleteMut.isPending}
            title={t("common.delete")}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </article>
  );
}

function LastRunChip({
  state,
  at,
}: {
  state: "completed" | "failed";
  at: number | null;
}) {
  const ok = state === "completed";
  return (
    <Badge
      variant={ok ? "outline" : "destructive"}
      className="text-[10px]"
      title={at ? new Date(at / 1e6).toLocaleString() : ""}
    >
      {ok ? (
        <CheckCircle2 className="mr-1 h-3 w-3" />
      ) : (
        <AlertCircle className="mr-1 h-3 w-3" />
      )}
      {at ? formatDeltaPast(at) : state}
    </Badge>
  );
}

// ---------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------

function TimelineRow({
  run,
  job,
}: {
  run: RunRow;
  job: JobSummary | undefined;
}) {
  const [open, setOpen] = useState(false);
  const StateIcon =
    run.state === "completed"
      ? CheckCircle2
      : run.state === "failed"
        ? AlertCircle
        : run.state === "running"
          ? Loader2
          : Clock;
  const color =
    run.state === "completed"
      ? "text-emerald-400"
      : run.state === "failed"
        ? "text-destructive"
        : run.state === "running"
          ? "text-primary"
          : "text-muted-foreground";

  let metrics: Record<string, unknown> = {};
  try {
    metrics = JSON.parse(run.metrics_json || "{}");
  } catch {
    /* ignore */
  }
  const duration = metrics.duration_ms as number | undefined;

  return (
    <div className="rounded-md border border-border/60 bg-card/30">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 p-2.5 text-left text-xs transition-colors hover:bg-background/30"
      >
        <StateIcon
          className={cn(
            "h-3.5 w-3.5 shrink-0",
            color,
            run.state === "running" && "animate-spin",
          )}
        />
        <span className="font-mono text-muted-foreground">
          {formatShortTime(run.started_at_ns)}
        </span>
        <span className="truncate font-medium text-foreground">
          {job?.name ?? run.job_id.slice(0, 8)}
        </span>
        <Badge variant="outline" className="text-[10px]">
          {run.trigger}
        </Badge>
        {duration !== undefined && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {duration}ms
          </span>
        )}
        {run.exit_code !== null && run.exit_code !== 0 && (
          <span className="font-mono text-[11px] text-destructive">
            exit {run.exit_code}
          </span>
        )}
        {run.error && (
          <span className="ml-auto line-clamp-1 max-w-[40%] text-destructive/80">
            {run.error}
          </span>
        )}
      </button>
      {open && (
        <div className="space-y-2 border-t border-border/60 p-3 text-[11px]">
          {run.output && (
            <div>
              <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                Output
              </div>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-md bg-background/40 p-2 font-mono text-[11px] leading-snug">
                {run.output.slice(0, 8000)}
              </pre>
            </div>
          )}
          {Object.keys(metrics).length > 0 && (
            <div>
              <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                Metrics
              </div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(metrics).map(([k, v]) => (
                  <span
                    key={k}
                    className="rounded-md border border-border bg-background/40 px-2 py-0.5 font-mono"
                  >
                    <span className="text-muted-foreground">{k}:</span>{" "}
                    {String(v)}
                  </span>
                ))}
              </div>
            </div>
          )}
          {run.error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-destructive">
              {run.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------
// Job-Editor-Modal
// ---------------------------------------------------------------------

const EXAMPLE_SHELL_JSON = `{
  "name": "URL-Healthcheck",
  "description": "Pingt alle 5 Minuten ein API-Endpoint an.",
  "spec": {
    "type": "http",
    "method": "GET",
    "url": "https://api.github.com/zen",
    "expect_status": "2xx"
  },
  "schedule": { "type": "interval", "seconds": 300 },
  "tags": ["monitor", "http"]
}`;

function JobEditorModal({ onClose }: { onClose: () => void }) {
  const t = useT();
  const [text, setText] = useState(EXAMPLE_SHELL_JSON);
  const [err, setErr] = useState<string | null>(null);
  const createMut = useCreateConductorJob();

  const submit = async () => {
    setErr(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      setErr(`${t("conductor_view.json_syntax_error")}: ${(e as Error).message}`);
      return;
    }
    try {
      await createMut.mutateAsync(parsed);
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-[80vh] w-[min(860px,90vw)] flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">{t("conductor_view.new_job")}</h2>
            <Badge variant="outline" className="text-[10px]">
              JSON
            </Badge>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-auto p-4">
          <p className="mb-3 text-xs text-muted-foreground">
            {t("conductor_view.editor_intro")}{" "}
            <code className="rounded bg-background/60 px-1">shell</code>,{" "}
            <code className="rounded bg-background/60 px-1">http</code>,{" "}
            <code className="rounded bg-background/60 px-1">agent</code>.
            Schedule:{" "}
            <code className="rounded bg-background/60 px-1">cron</code>,{" "}
            <code className="rounded bg-background/60 px-1">interval</code>,{" "}
            <code className="rounded bg-background/60 px-1">manual</code>,{" "}
            <code className="rounded bg-background/60 px-1">webhook</code>.
          </p>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            spellCheck={false}
            className="h-64 w-full resize-none rounded-md border border-border bg-background/40 p-3 font-mono text-xs leading-relaxed focus:border-primary/40 focus:outline-none"
          />
          {err && (
            <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
              {err}
            </div>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-border bg-background/30 px-4 py-3">
          <Button variant="ghost" size="sm" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button size="sm" onClick={submit} disabled={createMut.isPending}>
            {createMut.isPending ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Plus className="mr-1 h-3.5 w-3.5" />
            )}
            {t("conductor_view.create")}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------
// Empty States
// ---------------------------------------------------------------------

function EmptyJobs() {
  const t = useT();
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border/60 bg-background/30 p-8 text-center">
      <Orbit className="h-6 w-6 text-muted-foreground/50" />
      <p className="text-sm text-muted-foreground">
        {t("conductor_view.empty_jobs")}
      </p>
      <p className="text-xs text-muted-foreground/60">
        {t("conductor_view.empty_jobs_hint")}
      </p>
    </div>
  );
}

function EmptyTimeline() {
  const t = useT();
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border/60 bg-background/30 p-8 text-center">
      <Clock className="h-6 w-6 text-muted-foreground/50" />
      <p className="text-sm text-muted-foreground">
        {t("conductor_view.empty_timeline")}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------
// Time helpers
// ---------------------------------------------------------------------

function formatDeltaPast(ns: number): string {
  const delta = Date.now() * 1e6 - ns;
  if (delta <= 0) return translate("conductor_view.now");
  const sec = Math.round(delta / 1e9);
  if (sec < 60) return `${sec}s`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}min`;
  const hr = Math.round(min / 60);
  if (hr < 48) return `${hr}h`;
  return `${Math.round(hr / 24)}d`;
}

function formatShortTime(ns: number): string {
  try {
    return new Date(ns / 1e6).toLocaleTimeString();
  } catch {
    return "—";
  }
}
