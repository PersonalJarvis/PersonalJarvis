/**
 * The agent's runtime: Jarvis (default), Hermes or OpenClaw
 * (docs/agent-runtimes.md). It is chosen once, in the create dialog
 * (`RuntimeChoice`), and fixed for the agent's life; the model menu only
 * shows it (`RuntimeStatusRow`). Hermes and OpenClaw are installed and
 * updated with their own official tools, only when the person presses the
 * button.
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Loader2 } from "lucide-react";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useT } from "@/i18n";
import {
  fetchAgentRuntimes,
  startAgentRuntimeSetup,
  type AgentRuntimeStatus,
} from "@/lib/agentRuntimesApi";
import { AGENT_RUNTIMES, type AgentRuntime } from "@/lib/societyApi";
import { cn } from "@/lib/utils";

export const AGENT_RUNTIMES_QUERY_KEY = ["agent-runtimes"] as const;

/** Status of both external runtimes; polls while an install or update runs. */
export function useAgentRuntimes() {
  return useQuery({
    queryKey: AGENT_RUNTIMES_QUERY_KEY,
    queryFn: () => fetchAgentRuntimes(),
    staleTime: 60_000,
    refetchInterval: (query) =>
      query.state.data?.runtimes?.some((row) => row.job?.state === "running") ? 2_000 : false,
  });
}

function fill(text: string, ...values: string[]): string {
  return values.reduce((out, value, index) => out.replace(`{${index}}`, value), text);
}

function statusesByName(data: ReturnType<typeof useAgentRuntimes>["data"]) {
  const rows = Array.isArray(data?.runtimes) ? data.runtimes : [];
  return new Map<string, AgentRuntimeStatus>(rows.map((row) => [row.runtime, row]));
}

function RuntimeMark({ runtime, label }: { runtime: AgentRuntime; label: string }) {
  return runtime === "jarvis"
    ? <Bot className="size-4 shrink-0 text-muted-foreground" aria-hidden />
    : <ProviderLogo providerId={runtime} label={label} size="sm" />;
}

/** Install / update offer for one external runtime that is not ready. */
function RuntimeSetup({ status }: { status: AgentRuntimeStatus }) {
  const t = useT();
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const job = status.job;
  const running = job?.state === "running";
  // OpenClaw's own installer also adds the Node.js it needs.
  const action: "install" | "update" =
    status.installed && status.problem_kind !== "node" ? "update" : "install";
  const label = t(`society.runtime.${status.runtime}`);
  const problem =
    status.problem_kind === "outdated"
      ? fill(t("society.runtime.needs_update"), label, status.minimum_version)
      : status.problem_kind === "node"
        ? t("society.runtime.node_hint")
        : status.problem_kind === "no_version"
          ? fill(t("society.runtime.no_version"), label)
          : fill(t("society.runtime.not_installed"), label);

  async function setup() {
    setError(null);
    try {
      await startAgentRuntimeSetup(status.runtime, action);
    } catch (exc) {
      setError(fill(t("society.runtime.setup_failed"), exc instanceof Error ? exc.message : String(exc)));
    }
    await client.invalidateQueries({ queryKey: AGENT_RUNTIMES_QUERY_KEY });
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span>{problem}</span>
      <button
        type="button"
        disabled={running}
        onClick={() => void setup()}
        className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-xs text-foreground hover:bg-secondary disabled:opacity-60"
      >
        {running ? <Loader2 className="size-3 animate-spin" aria-hidden /> : null}
        {running
          ? t(job?.kind === "update" ? "society.runtime.updating" : "society.runtime.installing")
          : t(`society.runtime.${action}`)}
      </button>
      {job?.state === "failed" ? (
        <span role="alert" className="w-full text-destructive">
          {fill(t("society.runtime.setup_failed"), job.message || job.log_tail.at(-1) || "")}
        </span>
      ) : null}
      {running && job?.log_tail.length ? (
        <span className="w-full truncate font-mono text-[11px]">{job.log_tail.at(-1)}</span>
      ) : null}
      {error ? <span role="alert" className="w-full text-destructive">{error}</span> : null}
    </div>
  );
}

/** Whether a runtime can run a turn right now (Jarvis always can). */
export function runtimeReady(
  runtime: AgentRuntime,
  data: ReturnType<typeof useAgentRuntimes>["data"],
): boolean {
  return runtime === "jarvis" || Boolean(statusesByName(data).get(runtime)?.ready);
}

/** The create dialog's choice: three cards, and setup for one that is not ready. */
export function RuntimeChoice({ value, onChange, disabled = false }: {
  value: AgentRuntime; onChange: (runtime: AgentRuntime) => void; disabled?: boolean;
}) {
  const t = useT();
  const runtimes = useAgentRuntimes();
  const byName = statusesByName(runtimes.data);
  const chosen = value === "jarvis" ? null : byName.get(value);
  return (
    <div className="flex flex-col gap-2" data-testid="runtime-choice">
      <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label={t("society.runtime.title")}>
        {AGENT_RUNTIMES.map((runtime) => {
          const status = runtime === "jarvis" ? null : byName.get(runtime);
          const label = t(`society.runtime.${runtime}`);
          return (
            <button
              key={runtime}
              type="button"
              role="radio"
              aria-checked={value === runtime}
              aria-label={label}
              disabled={disabled}
              onClick={() => onChange(runtime)}
              className={cn(
                "flex flex-col items-start gap-1 rounded-lg border p-2.5 text-left transition-colors disabled:opacity-50",
                value === runtime
                  ? "border-border-strong bg-secondary text-foreground"
                  : "border-border text-muted-foreground hover:bg-secondary",
              )}
            >
              <span className="inline-flex items-center gap-1.5 text-sm font-medium text-foreground">
                <RuntimeMark runtime={runtime} label={label} />
                {label}
              </span>
              <span className="text-[11px] leading-snug">
                {status
                  ? status.ready
                    ? fill(t("society.runtime.version"), status.version)
                    : t(status.installed ? "society.runtime.needs_setup" : "society.runtime.not_installed_short")
                  : t("society.runtime.built_in")}
              </span>
            </button>
          );
        })}
      </div>
      <p className="text-xs text-muted-foreground">{t(`society.runtime.${value}_hint`)}</p>
      {chosen && !chosen.ready ? <RuntimeSetup status={chosen} /> : null}
    </div>
  );
}

/** The model menu's line: the runtime the agent runs on, and its update when needed. */
export function RuntimeStatusRow({ runtime }: { runtime: AgentRuntime }) {
  const t = useT();
  const runtimes = useAgentRuntimes();
  const status = runtime === "jarvis" ? null : statusesByName(runtimes.data).get(runtime);
  const label = t(`society.runtime.${runtime}`);
  return (
    <div className="flex flex-col gap-1.5" data-testid="runtime-status">
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <RuntimeMark runtime={runtime} label={label} />
        {fill(t("society.runtime.runs_on"), label)}
        {status?.version ? ` · ${fill(t("society.runtime.version"), status.version)}` : ""}
      </span>
      {status && !status.ready ? <RuntimeSetup status={status} /> : null}
    </div>
  );
}
