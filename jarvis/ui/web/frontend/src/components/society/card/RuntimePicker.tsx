/**
 * The agent's runtime: Jarvis (default), Hermes or OpenClaw
 * (docs/agent-runtimes.md). It is chosen once, in the create dialog
 * (`RuntimeChoice`), and fixed for the agent's life; the model menu only
 * shows it (`RuntimeStatusRow`). Nobody installs Hermes or OpenClaw by hand:
 * picking one asks the backend to set it up (`ensure`), and Jarvis keeps it
 * up to date on its own. The UI only says whether it is ready or still being
 * set up — never a version number.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { RuntimeMark } from "@/components/society/RuntimeBadge";
import { useT } from "@/i18n";
import {
  ensureAgentRuntime,
  fetchAgentRuntimes,
  type AgentRuntimeStatus,
  type ExternalRuntime,
} from "@/lib/agentRuntimesApi";
import { AGENT_RUNTIMES, type AgentRuntime } from "@/lib/societyApi";
import { cn } from "@/lib/utils";

export const AGENT_RUNTIMES_QUERY_KEY = ["agent-runtimes"] as const;

/** Status of both external runtimes; polls while a setup or update runs. */
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

type SetupState = "ready" | "setting_up" | "failed" | "pending";

function setupState(status: AgentRuntimeStatus | undefined): SetupState {
  if (!status) return "pending";
  if (status.job?.state === "running") return "setting_up";
  if (status.ready) return "ready";
  return status.job?.state === "failed" ? "failed" : "pending";
}

/** Ask the backend to set a runtime up; refreshes the status either way. */
function useEnsureRuntime() {
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const ensure = useCallback(async (runtime: ExternalRuntime) => {
    setError(null);
    try {
      await ensureAgentRuntime(runtime);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
    await client.invalidateQueries({ queryKey: AGENT_RUNTIMES_QUERY_KEY });
  }, [client]);
  return { ensure, error };
}

/** What a runtime that is not ready yet is doing, and a retry when its setup failed. */
function RuntimeSetupNote({ status, onRetry, error }: {
  status: AgentRuntimeStatus | undefined; onRetry: () => void; error: string | null;
}) {
  const t = useT();
  const state = setupState(status);
  if (state === "ready" || !status) return null;
  const label = t(`society.runtime.${status.runtime}`);
  const failure = error ?? (state === "failed" ? status.job?.message || status.job?.log_tail.at(-1) || status.problem : null);
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground" data-testid="runtime-setup">
      {state === "setting_up" ? <Loader2 className="size-3 animate-spin" aria-hidden /> : null}
      <span>
        {fill(t(state === "setting_up" ? "society.runtime.setting_up_hint" : "society.runtime.auto_setup_hint"), label)}
      </span>
      {failure ? (
        <>
          <span role="alert" className="w-full text-destructive">{fill(t("society.runtime.setup_failed"), failure)}</span>
          <button
            type="button"
            onClick={onRetry}
            className="rounded-full border border-border px-2 py-0.5 text-xs text-foreground hover:bg-secondary"
          >
            {t("society.runtime.retry")}
          </button>
        </>
      ) : null}
    </div>
  );
}

/** The create dialog's choice: three cards; picking Hermes or OpenClaw sets it up. */
export function RuntimeChoice({ value, onChange, disabled = false }: {
  value: AgentRuntime; onChange: (runtime: AgentRuntime) => void; disabled?: boolean;
}) {
  const t = useT();
  const runtimes = useAgentRuntimes();
  const byName = statusesByName(runtimes.data);
  const { ensure, error } = useEnsureRuntime();
  const chosen = value === "jarvis" ? undefined : byName.get(value);
  const chosenState = value === "jarvis" ? "ready" : setupState(chosen);

  const asked = useRef(new Set<string>());
  const loaded = Boolean(runtimes.data);
  useEffect(() => {
    // Picking a runtime starts its setup at once (once per runtime), so it is
    // usually done by the time the agent gets its first message.
    if (value === "jarvis" || !loaded || chosenState !== "pending" || asked.current.has(value)) return;
    asked.current.add(value);
    void ensure(value);
  }, [value, loaded, chosenState, ensure]);

  return (
    <div className="flex flex-col gap-2" data-testid="runtime-choice">
      <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label={t("society.runtime.title")}>
        {AGENT_RUNTIMES.map((runtime) => {
          const label = t(`society.runtime.${runtime}`);
          const state = runtime === "jarvis" ? null : setupState(byName.get(runtime));
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
              <span className="inline-flex items-center gap-1 text-[11px] leading-snug">
                {state === "setting_up" ? <Loader2 className="size-3 animate-spin" aria-hidden /> : null}
                {t(
                  state === null
                    ? "society.runtime.built_in"
                    : state === "ready"
                      ? "society.runtime.ready"
                      : state === "setting_up"
                        ? "society.runtime.setting_up"
                        : "society.runtime.auto_setup",
                )}
              </span>
            </button>
          );
        })}
      </div>
      <p className="text-xs text-muted-foreground">{t(`society.runtime.${value}_hint`)}</p>
      {value !== "jarvis" ? (
        <RuntimeSetupNote status={chosen} error={error} onRetry={() => void ensure(value)} />
      ) : null}
    </div>
  );
}

/** The model menu's line: the runtime the agent runs on, and its setup while one runs. */
export function RuntimeStatusRow({ runtime }: { runtime: AgentRuntime }) {
  const t = useT();
  const runtimes = useAgentRuntimes();
  const { ensure, error } = useEnsureRuntime();
  const status = runtime === "jarvis" ? undefined : statusesByName(runtimes.data).get(runtime);
  const label = t(`society.runtime.${runtime}`);
  return (
    <div className="flex flex-col gap-1.5" data-testid="runtime-status">
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <RuntimeMark runtime={runtime} label={label} />
        {fill(t("society.runtime.runs_on"), label)}
      </span>
      {runtime !== "jarvis" ? (
        <RuntimeSetupNote status={status} error={error} onRetry={() => void ensure(runtime)} />
      ) : null}
    </div>
  );
}
