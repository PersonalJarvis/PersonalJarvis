/**
 * The agent's runtime: Jarvis (default), Hermes or OpenClaw
 * (docs/agent-runtimes.md). Hermes and OpenClaw are installed and updated
 * here with their own official tools, only when the person presses the
 * button; switching keeps the agent's model, memory and one chat.
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useT } from "@/i18n";
import {
  fetchAgentRuntimes,
  startAgentRuntimeSetup,
  type AgentRuntimeStatus,
  type ExternalRuntime,
} from "@/lib/agentRuntimesApi";
import { AGENT_RUNTIMES, type AgentRuntime } from "@/lib/societyApi";
import { cn } from "@/lib/utils";
import { useUpdateAgentModel, type SocietyAgent } from "../data";

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

export function RuntimePicker({ agent }: { agent: SocietyAgent }) {
  const t = useT();
  const client = useQueryClient();
  const updateModel = useUpdateAgentModel();
  const runtimes = useAgentRuntimes();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const current: AgentRuntime = agent.runtime ?? "jarvis";
  const byName = new Map<string, AgentRuntimeStatus>(
    (Array.isArray(runtimes.data?.runtimes) ? runtimes.data.runtimes : []).map((row) => [row.runtime, row]),
  );
  const supported = new Set(runtimes.data?.supported_providers ?? []);
  const remote = Boolean(agent.computerId);
  // Unknown until the list loaded: no hint flashes up meanwhile.
  const modelFits = !runtimes.data?.supported_providers || !agent.provider || supported.has(agent.provider);

  async function choose(runtime: AgentRuntime) {
    if (runtime === current || saving) return;
    setSaving(true);
    setError(null);
    try {
      await updateModel(agent.agentId, {
        provider: agent.provider,
        model: agent.model,
        effort: agent.effort,
        account_id: agent.accountId ?? "",
        runtime,
      });
    } catch (exc) {
      setError(fill(t("society.runtime.switch_failed"), exc instanceof Error ? exc.message : String(exc)));
    } finally {
      setSaving(false);
    }
  }

  async function setup(runtime: ExternalRuntime, action: "install" | "update") {
    setError(null);
    try {
      await startAgentRuntimeSetup(runtime, action);
    } catch (exc) {
      setError(fill(t("society.runtime.setup_failed"), exc instanceof Error ? exc.message : String(exc)));
    }
    await client.invalidateQueries({ queryKey: AGENT_RUNTIMES_QUERY_KEY });
  }

  const chosen = byName.get(current);
  return (
    <div className="flex flex-col gap-1.5" data-testid="runtime-picker">
      <span className="text-xs text-muted-foreground">{t("society.runtime.title")}</span>
      <div className="flex flex-wrap gap-1" role="radiogroup" aria-label={t("society.runtime.title")}>
        {AGENT_RUNTIMES.map((runtime) => {
          const status = runtime === "jarvis" ? null : byName.get(runtime);
          const external = runtime !== "jarvis";
          const blocked = external && (remote || !modelFits || !status?.ready);
          return (
            <button
              key={runtime}
              type="button"
              role="radio"
              aria-checked={current === runtime}
              disabled={saving || (blocked && current !== runtime)}
              onClick={() => void choose(runtime)}
              title={t(`society.runtime.${runtime}_hint`)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs transition-colors disabled:opacity-50",
                current === runtime
                  ? "border-border-strong bg-secondary text-foreground"
                  : "border-border text-muted-foreground hover:bg-secondary",
              )}
            >
              {external ? (
                <ProviderLogo providerId={runtime} label={t(`society.runtime.${runtime}`)} size="sm" />
              ) : null}
              {t(`society.runtime.${runtime}`)}
            </button>
          );
        })}
        {saving ? <Loader2 className="size-3.5 animate-spin self-center text-muted-foreground" aria-hidden /> : null}
      </div>
      <p className="text-xs text-muted-foreground">
        {t(`society.runtime.${current}_hint`)}
        {chosen?.version ? ` · ${fill(t("society.runtime.version"), chosen.version)}` : ""}
      </p>
      {remote ? <p className="text-xs text-muted-foreground">{t("society.runtime.remote_hint")}</p> : null}
      {!remote && !modelFits ? (
        <p className="text-xs text-muted-foreground">{t("society.runtime.model_hint")}</p>
      ) : null}
      {(["hermes", "openclaw"] as const).map((runtime) => {
        const status = byName.get(runtime);
        if (!status || status.ready) return null;
        const job = status.job;
        const running = job?.state === "running";
        // OpenClaw's own installer also adds the Node.js it needs.
        const action: "install" | "update" =
          status.installed && status.problem_kind !== "node" ? "update" : "install";
        const label = t(`society.runtime.${runtime}`);
        const problem =
          status.problem_kind === "outdated"
            ? fill(t("society.runtime.needs_update"), label, status.minimum_version)
            : status.problem_kind === "node"
              ? t("society.runtime.node_hint")
              : status.problem_kind === "no_version"
                ? fill(t("society.runtime.no_version"), label)
                : fill(t("society.runtime.not_installed"), label);
        return (
          <div key={runtime} className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{problem}</span>
            <button
              type="button"
              disabled={running}
              onClick={() => void setup(runtime, action)}
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
          </div>
        );
      })}
      {error ? <p role="alert" className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}
