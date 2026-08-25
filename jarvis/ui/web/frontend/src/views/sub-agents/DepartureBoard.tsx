/**
 * The agent board — every {name}-Agent the assistant has started, what it is
 * working on, and what it left behind.
 *
 * Same surface as Spend, Skills, Plugins, MCPs and CLIs: one column of quiet
 * panels built from `components/extensions/primitives`, so this section cannot
 * drift into a look of its own. It replaces the "departure board" — a
 * train-station metaphor rendered as an edge-to-edge hairline metric strip, a
 * hand-built 1040px grid and monospace in every cell, which was the last screen
 * still wearing a look no other section wears. A finished agent said "arrived",
 * the way a train does; it now says "done", the way a task does.
 *
 * Every row opens the run's own page (`AgentInsight`) — the same way for every
 * row. The board used to expand SOME rows in place (only those the registry
 * still held tool calls for, or that carried a summary), which read as "why
 * does this one have an arrow and that one not?"; the inline drilldown is gone
 * and the chevron now means one thing everywhere: there is a page behind this.
 *
 * The brand comes from `agentBrand`, so the board follows whatever wake word is
 * configured rather than a product name (see lib/agentBrand.ts).
 */
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Bot, CheckCircle2, ChevronRight, CircleAlert, Radio, Wrench } from "lucide-react";

import type { SubAgentNode } from "@/store/jarvisAgents";
import type { SectionHealth } from "@/hooks/useProviders";
import { ExplicitSpawnHint } from "@/components/ExplicitSpawnHint";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Cell,
  type Column,
  Panel,
  PanelHeader,
  StatTile,
  StatusDot,
  Table,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import { agentBrand, agentsBrand } from "@/lib/agentBrand";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { fill, useT } from "@/i18n";
import { failureLabel } from "./failureLabel";
import { formatDuration } from "./format";
import { REASON_LABEL_KEYS, splitReason } from "./outcome";

// The four run states map onto the shared StatusDot tones: a running agent is
// the app's own "busy" (brand primary), a finished one is the token set's
// success green, a failure is `destructive`, and a deliberate stop is a warning
// rather than an error — it is a decision, not a fault.
const STATUS_TONE: Record<SubAgentNode["status"], "busy" | "ok" | "error" | "warn"> = {
  running: "busy",
  completed: "ok",
  failed: "error",
  cancelled: "warn",
};

function startedMs(node: SubAgentNode): number {
  return node.started_ns > 1_000_000_000_000_000
    ? Math.floor(node.started_ns / 1_000_000)
    : node.ui_appeared_at;
}

function runtimeLabel(node: SubAgentNode, nowMs: number): string {
  if (node.duration_ms != null) return formatDuration(node.duration_ms);
  if (node.status === "running") return formatDuration(nowMs - startedMs(node));
  return "—";
}

// User-facing role label only. The underlying engine, provider and model are
// deliberately NOT surfaced here: from the operator's perspective every node is
// just one of the assistant's own agents, branded with the wake-word-derived
// assistant name. `kind` is an internal routing tag (the top-level mission node
// vs. harness == the worker subprocess) and is never shown raw — see the
// subtitle below. The concrete "what is it doing" lives in the Task/Project
// column (`taskLabel`).
function displayAgentName(node: SubAgentNode, assistantName: string, t: (key: string) => string): string {
  return node.kind === "harness" ? t("subagents_view.role_worker") : agentBrand(assistantName);
}

function taskLabel(node: SubAgentNode, t: (key: string) => string): string {
  return node.utterance || node.context_hints.at(0) || node.prompts.at(0) || t("subagents_view.task_none");
}

/** Plain-language label for a mission-level terminal reason, else the raw token. */
function outcomeReasonLabel(node: SubAgentNode, t: (key: string) => string): string | null {
  const { head, tail } = splitReason(node.outcome_reason);
  if (!head) return null;
  const key = REASON_LABEL_KEYS[head];
  const label = key ? t(key) : head;
  return tail ? `${label} (${tail})` : label;
}

function resultLabel(node: SubAgentNode, t: (key: string) => string): string {
  const failure = failureLabel(node, t);
  if (failure) return failure;
  const summary = [...node.prompts].reverse().find((p) => p.startsWith("[summary] "));
  if (summary) return summary.replace("[summary] ", "");
  if (node.status === "completed") return t("subagents_view.result_done");
  if (node.status === "running") return t("subagents_view.result_running");
  // The outputs archive knows WHY a past run ended ("provider quota", "the
  // reviewer ran out of time") where the mission list only knows THAT it did.
  const reason = outcomeReasonLabel(node, t);
  if (reason) return reason;
  // A row from the durable record carries no error text, so without these two
  // a failed or cancelled past run showed an em dash next to a red status —
  // as if the outcome were unknown rather than stated one column to the left.
  if (node.status === "failed") return t("subagents_view.result_failed");
  if (node.status === "cancelled") return t("subagents_view.result_cancelled");
  return "—";
}

interface Props {
  agents?: SubAgentNode[];
  snapshotError?: string | null;
  health?: SectionHealth | null;
  /** The durable half could not be loaded; the live half still renders. */
  historyError?: boolean;
  /** A row was clicked: open that run's insight page. */
  onOpen?: (agent: SubAgentNode) => void;
}

export function DepartureBoard({
  agents = [],
  snapshotError = null,
  health = null,
  historyError = false,
  onOpen,
}: Props) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const sortedAgents = useMemo(
    () => [...agents].sort((a, b) => b.started_ns - a.started_ns),
    [agents],
  );

  const activeCount = agents.filter((a) => a.status === "running").length;
  const doneCount = agents.filter((a) => a.status === "completed").length;
  const failedCount = agents.filter((a) => a.status === "failed").length;
  const toolCount = agents.reduce((sum, a) => sum + a.tool_calls.length, 0);
  const runningTools = agents.reduce(
    (sum, a) => sum + a.tool_calls.filter((c) => c.status === "running").length,
    0,
  );

  const columns: Column[] = [
    { id: "agent", label: t("subagents_view.col_agent"), width: "minmax(0, 1.1fr)" },
    { id: "task", label: t("subagents_view.col_task"), width: "minmax(0, 2.4fr)" },
    { id: "status", label: t("subagents_view.col_status"), width: "124px" },
    { id: "tools", label: t("subagents_view.col_tools"), width: "62px", align: "right" },
    { id: "runtime", label: t("subagents_view.col_runtime"), width: "76px", align: "right" },
    { id: "result", label: t("subagents_view.col_result"), width: "minmax(0, 1.4fr)" },
    { id: "open", label: t("subagents_view.col_open"), width: "28px", srOnly: true },
  ];

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ScrollArea className="flex-1">
        <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-4 px-6 py-6">
          <PanelHeader
            title={t("subagents_view.title")}
            subtitle={fill(t("subagents_view.subtitle"), {
              active: activeCount,
              total: agents.length,
            })}
          />

          {health && (health.status === "needs_setup" || health.status === "error") && (
            <Notice tone={health.status === "error" ? "error" : "warn"}>
              <span className="font-medium">
                {t(
                  health.status === "error"
                    ? "subagents_view.health_error"
                    : "subagents_view.health_degraded",
                )}
              </span>
              {health.detail ? <span className="opacity-80">{health.detail}</span> : null}
            </Notice>
          )}

          {snapshotError && (
            <Notice tone="error">
              {fill(t("subagents_view.snapshot_error"), { detail: snapshotError })}
            </Notice>
          )}

          {historyError && !snapshotError && (
            <Notice tone="warn">{t("subagents_view.history_error")}</Notice>
          )}

          {/* ---------------------------------------------------------------
              Headline numbers. Failed keeps its own tile rather than hiding
              inside "Done": a run that ended and a run that broke are not the
              same outcome, and a cancellation is neither.
          --------------------------------------------------------------- */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            <StatTile
              icon={<Bot className="h-4 w-4" />}
              label={agentsBrand(assistantName)}
              value={String(agents.length)}
              hint={t("subagents_view.stat_agents_hint")}
            />
            <StatTile
              icon={<Radio className="h-4 w-4" />}
              label={t("subagents_view.stat_active_label")}
              value={String(activeCount)}
              tone={activeCount > 0 ? "primary" : "ok"}
              hint={
                runningTools > 0
                  ? fill(t("subagents_view.stat_active_hint"), { tools: runningTools })
                  : t("subagents_view.stat_active_hint_idle")
              }
            />
            <StatTile
              icon={<CheckCircle2 className="h-4 w-4" />}
              label={t("subagents_view.stat_done_label")}
              value={String(doneCount)}
              tone={doneCount > 0 ? "success" : "ok"}
              hint={t("subagents_view.stat_done_hint")}
            />
            <StatTile
              icon={<AlertTriangle className="h-4 w-4" />}
              label={t("subagents_view.stat_failed_label")}
              value={String(failedCount)}
              tone={failedCount > 0 ? "danger" : "ok"}
              hint={
                failedCount > 0
                  ? t("subagents_view.stat_failed_hint")
                  : t("subagents_view.stat_failed_hint_none")
              }
            />
            <StatTile
              icon={<Wrench className="h-4 w-4" />}
              label={t("subagents_view.stat_tools_label")}
              value={String(toolCount)}
              hint={t("subagents_view.stat_tools_hint")}
            />
          </div>

          {/* The board ---------------------------------------------------- */}
          <Panel>
            <div className="px-4 pt-4">
              <PanelHeader
                title={t("subagents_view.board_title")}
                subtitle={t(onOpen ? "subagents_view.board_subtitle_open" : "subagents_view.board_subtitle")}
                actions={
                  <StatusDot
                    tone={activeCount > 0 ? "busy" : "off"}
                    pulse={activeCount > 0}
                    label={t(
                      activeCount > 0
                        ? "subagents_view.live_label"
                        : "subagents_view.standby_label",
                    )}
                  />
                }
              />
            </div>

            {/* The seven columns need room; below that width the table scrolls
                inside the panel rather than pushing the page sideways. */}
            <div className="mt-3 overflow-x-auto">
              <div className="min-w-[820px]">
                <Table label={t("subagents_view.board_title")}>
                  <TableHead columns={columns} />
                  {sortedAgents.length === 0 ? (
                    // No dashed frame here: the panel already draws one, and a
                    // second rule inside it reads as an empty input rather than
                    // as a calm "nothing yet".
                    <div className="flex flex-col items-center px-8 py-14 text-center">
                      <Bot className="mb-3.5 h-7 w-7 text-muted-foreground/60" />
                      <div className="font-display text-[15px] font-semibold text-foreground">
                        {fill(t("subagents_view.empty_title"), {
                          agents: agentsBrand(assistantName),
                        })}
                      </div>
                      <p className="mt-1.5 max-w-lg text-sm text-muted-foreground">
                        {fill(t("subagents_view.empty_body"), {
                          agent: agentBrand(assistantName),
                        })}
                      </p>
                    </div>
                  ) : (
                    sortedAgents.map((agent) => (
                      <AgentRow
                        key={agent.trace_id}
                        agent={agent}
                        columns={columns}
                        nowMs={nowMs}
                        onOpen={onOpen ? () => onOpen(agent) : undefined}
                        t={t}
                      />
                    ))
                  )}
                </Table>
              </div>
            </div>
          </Panel>

          <ExplicitSpawnHint className="px-1" />
        </div>
      </ScrollArea>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Notice — the health / snapshot banners
// ---------------------------------------------------------------------------

function Notice({
  tone,
  children,
}: {
  tone: "warn" | "error";
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-xl border px-3.5 py-2.5 text-sm",
        tone === "error"
          ? "border-destructive/30 bg-destructive/10 text-destructive"
          : "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
      )}
    >
      <CircleAlert className="h-4 w-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{children}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// One agent — a row that opens its page
// ---------------------------------------------------------------------------

function AgentRow({
  agent,
  columns,
  nowMs,
  onOpen,
  t,
}: {
  agent: SubAgentNode;
  columns: Column[];
  nowMs: number;
  onOpen?: () => void;
  t: (key: string) => string;
}) {
  const assistantName = useEventStore((s) => s.assistantName);
  const task = taskLabel(agent, t);
  const result = resultLabel(agent, t);

  return (
    <TableRow columns={columns} onClick={onOpen} ariaLabel={task}>
      <Cell>
        <div className="truncate text-[15px] font-medium text-foreground">
          {displayAgentName(agent, assistantName, t)}
        </div>
        <div className="truncate text-xs text-muted-foreground">
          {agent.kind === "harness"
            ? t("subagents_view.role_worker_hint")
            : t("subagents_view.role_agent_hint")}
        </div>
      </Cell>
      <Cell muted>
        <span className="block truncate" title={task}>
          {task}
        </span>
      </Cell>
      <Cell>
        <StatusDot
          tone={STATUS_TONE[agent.status]}
          pulse={agent.status === "running"}
          label={t(`subagents_view.status.${agent.status}`)}
        />
      </Cell>
      <Cell align="right" muted>
        <span className="tabular-nums">{agent.tool_calls.length}</span>
      </Cell>
      <Cell align="right" muted>
        <span className="tabular-nums">{runtimeLabel(agent, nowMs)}</span>
      </Cell>
      <Cell muted>
        <span className="block truncate" title={result}>
          {result}
        </span>
      </Cell>
      <Cell align="right" className="text-muted-foreground/50 group-hover:text-foreground">
        {onOpen ? <ChevronRight className="h-4 w-4" aria-hidden /> : null}
      </Cell>
    </TableRow>
  );
}
