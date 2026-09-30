/**
 * The visible connection check behind "Connect". It shows the four things the
 * backend really does, in order: reach the server, log in, run a test command,
 * then save (planting the assistant's key when a password was used). Each row
 * resolves from a real answer — the dry-run test settles the first three, the
 * save call the last — so a green tick always means that step happened.
 *
 * On success the machine's own facts (host name, OS, cores, memory, response
 * time) prove the login reached the right computer; on failure the failed row
 * says what to fix and the user goes back to the form with everything kept.
 */
import type { ReactNode } from "react";
import { ArrowRight, Circle, CircleAlert, CircleCheck, CircleX, Loader2, RotateCcw, Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { Computer, ComputerFacts } from "@/lib/computersApi";
import { formatMemory } from "./parts";

export type StepState = "wait" | "run" | "ok" | "warn" | "fail";
export type StepId = "reach" | "login" | "probe" | "save";

export interface CheckState {
  steps: Record<StepId, StepState>;
  /** The line under the step that failed or warned. */
  notes: Partial<Record<StepId, string>>;
  facts: ComputerFacts | null;
  latencyMs: number | null;
  computer: Computer | null;
}

export const STEP_ORDER: StepId[] = ["reach", "login", "probe", "save"];

export function startCheck(): CheckState {
  return {
    steps: { reach: "run", login: "wait", probe: "wait", save: "wait" },
    notes: {},
    facts: null,
    latencyMs: null,
    computer: null,
  };
}

export function checkFailed(check: CheckState): boolean {
  return STEP_ORDER.some((id) => check.steps[id] === "fail");
}

export function checkRunning(check: CheckState): boolean {
  return STEP_ORDER.some((id) => check.steps[id] === "run");
}

function StepIcon({ state }: { state: StepState }) {
  const cls = "h-[18px] w-[18px] shrink-0";
  switch (state) {
    case "run":
      return <Loader2 className={cn(cls, "animate-spin text-foreground-secondary")} aria-hidden />;
    case "ok":
      return <CircleCheck className={cn(cls, "text-success")} aria-hidden />;
    case "warn":
      return <CircleAlert className={cn(cls, "text-warning")} aria-hidden />;
    case "fail":
      return <CircleX className={cn(cls, "text-destructive")} aria-hidden />;
    default:
      return <Circle className={cn(cls, "text-foreground-faint")} aria-hidden />;
  }
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate text-sm text-foreground-strong">{children}</dd>
    </div>
  );
}

export function ConnectCheck({
  check,
  labels,
  onEdit,
  onRetry,
  onOpen,
}: {
  check: CheckState;
  labels: Record<StepId, string>;
  onEdit: () => void;
  onRetry: () => void;
  onOpen: (computer: Computer, tab: "overview" | "agents") => void;
}) {
  const t = useT();
  const failed = checkFailed(check);
  const running = checkRunning(check);
  const facts = check.facts;
  const computer = check.computer;

  return (
    <div className="space-y-5" data-testid="cx-check">
      <ol className="space-y-1" aria-live="polite">
        {STEP_ORDER.map((id) => {
          const state = check.steps[id];
          return (
            <li
              key={id}
              data-testid={`cx-step-${id}`}
              data-state={state}
              className={cn(
                "flex items-start gap-3 rounded-md px-3 py-2.5",
                state === "fail" && "bg-destructive/10",
                state === "warn" && "bg-warning/10",
              )}
            >
              <span className="mt-px">
                <StepIcon state={state} />
              </span>
              <div className="min-w-0 flex-1">
                <div
                  className={cn(
                    "text-sm",
                    state === "wait" ? "text-muted-foreground" : "text-foreground-strong",
                    state === "run" && "font-medium",
                  )}
                >
                  {labels[id]}
                </div>
                {check.notes[id] && (
                  <p role={state === "fail" ? "alert" : undefined} className="mt-0.5 text-sm text-foreground-secondary">
                    {check.notes[id]}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      {computer && facts && (
        <dl
          className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-lg border border-border bg-secondary/40 p-4"
          data-testid="cx-facts"
        >
          <Fact label={t("computers.fact_hostname")}>
            <span className="font-mono">{facts.hostname ?? computer.host}</span>
          </Fact>
          <Fact label={t("computers.fact_os")}>{facts.os_name ?? facts.os_id ?? "—"}</Fact>
          <Fact label={t("computers.fact_cpu")}>
            {facts.cpu_count ? `${facts.cpu_count} ${t("computers.unit_cpu")}` : "—"}
            {facts.arch ? ` · ${facts.arch}` : ""}
          </Fact>
          <Fact label={t("computers.fact_memory")}>{formatMemory(facts.mem_total_mb) ?? "—"}</Fact>
          {check.latencyMs !== null && (
            <Fact label={t("computers.latency")}>
              <span className="tabular-nums">{Math.round(check.latencyMs)} ms</span>
            </Fact>
          )}
        </dl>
      )}

      {failed && (
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" onClick={onEdit} data-testid="cx-edit">
            <Wrench />
            {t("computers.cx_edit")}
          </Button>
          <Button type="button" onClick={onRetry} data-testid="cx-retry">
            <RotateCcw />
            {t("computers.cx_retry")}
          </Button>
        </div>
      )}

      {computer && !running && !failed && (
        <div className="flex flex-col gap-2 sm:flex-row">
          <Button type="button" className="sm:flex-1" onClick={() => onOpen(computer, "overview")} data-testid="cx-open">
            {fill(t("computers.wz_open"), { computer: computer.name })}
            <ArrowRight />
          </Button>
          <Button type="button" variant="outline" className="sm:flex-1" onClick={() => onOpen(computer, "agents")}>
            {t("computers.wz_next_agents")}
          </Button>
        </div>
      )}
    </div>
  );
}
