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
 * The content is unchanged: the same five numbers, the same seven columns, the
 * same inline drilldown per row. What changed is that they are rendered by the
 * shared primitives and that every visible string goes through i18n — the old
 * board hard-coded its English, so a German or Spanish UI still read "Tool
 * calls / In progress / No agents are running right now."
 *
 * The brand comes from `agentBrand`, so the board follows whatever wake word is
 * configured rather than a product name (see lib/agentBrand.ts).
 */
import { Fragment, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Radio,
  TerminalSquare,
  Trash2,
  Wrench,
} from "lucide-react";

import type { SubAgentNode, ToolCallEntry } from "@/store/jarvisAgents";
import type { SectionHealth } from "@/hooks/useProviders";
import { ExplicitSpawnHint } from "@/components/ExplicitSpawnHint";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Cell,
  type Column,
  IconButton,
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

const TOOL_STATUS_TONE: Record<ToolCallEntry["status"], "busy" | "ok" | "error"> = {
  running: "busy",
  completed: "ok",
  failed: "error",
};

function formatRelative(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.floor(ms)}ms`;
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m`;
  return `${Math.floor(ms / 3_600_000)}h`;
}

function startedMs(node: SubAgentNode): number {
  return node.started_ns > 1_000_000_000_000_000
    ? Math.floor(node.started_ns / 1_000_000)
    : node.ui_appeared_at;
}

function runtimeLabel(node: SubAgentNode, nowMs: number): string {
  if (node.duration_ms != null) return formatRelative(node.duration_ms);
  if (node.status === "running") return formatRelative(nowMs - startedMs(node));
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

function resultLabel(node: SubAgentNode, t: (key: string) => string): string {
  const failure = failureLabel(node, t);
  if (failure) return failure;
  const summary = [...node.prompts].reverse().find((p) => p.startsWith("[summary] "));
  if (summary) return summary.replace("[summary] ", "");
  if (node.status === "completed") return t("subagents_view.result_done");
  if (node.status === "running") return t("subagents_view.result_running");
  return "—";
}

interface Props {
  agents?: SubAgentNode[];
  snapshotError?: string | null;
  health?: SectionHealth | null;
  onClear?: () => void;
}

export function DepartureBoard({
  agents = [],
  snapshotError = null,
  health = null,
  onClear,
}: Props) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    setExpanded((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const agent of agents) {
        if (agent.status === "running" && agent.tool_calls.length > 0 && !next.has(agent.trace_id)) {
          next.add(agent.trace_id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [agents]);

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

  const toggle = (traceId: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(traceId)) next.delete(traceId);
      else next.add(traceId);
      return next;
    });

  const columns: Column[] = [
    { id: "expand", label: t("subagents_view.col_expand"), width: "22px", srOnly: true },
    { id: "agent", label: t("subagents_view.col_agent"), width: "minmax(0, 1.1fr)" },
    { id: "task", label: t("subagents_view.col_task"), width: "minmax(0, 2.4fr)" },
    { id: "status", label: t("subagents_view.col_status"), width: "124px" },
    { id: "tools", label: t("subagents_view.col_tools"), width: "62px", align: "right" },
    { id: "runtime", label: t("subagents_view.col_runtime"), width: "76px", align: "right" },
    { id: "result", label: t("subagents_view.col_result"), width: "minmax(0, 1.4fr)" },
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
            actions={
              onClear ? (
                <IconButton
                  label={t("subagents_view.clear_tooltip")}
                  onClick={onClear}
                  disabled={agents.length === 0}
                >
                  <Trash2 className="h-4 w-4" />
                </IconButton>
              ) : null
            }
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
                subtitle={t("subagents_view.board_subtitle")}
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
                        expanded={expanded.has(agent.trace_id)}
                        onToggle={() => toggle(agent.trace_id)}
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
// One agent, plus its drilldown
// ---------------------------------------------------------------------------

function AgentRow({
  agent,
  columns,
  nowMs,
  expanded,
  onToggle,
  t,
}: {
  agent: SubAgentNode;
  columns: Column[];
  nowMs: number;
  expanded: boolean;
  onToggle: () => void;
  t: (key: string) => string;
}) {
  const assistantName = useEventStore((s) => s.assistantName);
  const hasDrilldown = agent.tool_calls.length > 0 || !!agent.error || agent.prompts.length > 0;
  const task = taskLabel(agent, t);
  const result = resultLabel(agent, t);

  return (
    <Fragment>
      <TableRow
        columns={columns}
        onClick={hasDrilldown ? onToggle : undefined}
        selected={expanded}
        ariaLabel={task}
      >
        <Cell className="text-muted-foreground">
          {hasDrilldown ? (
            expanded ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )
          ) : null}
        </Cell>
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
      </TableRow>

      {expanded && hasDrilldown && <Drilldown agent={agent} task={task} t={t} />}
    </Fragment>
  );
}

/**
 * The expanded half of a row.
 *
 * `role="row"` with a single `role="cell"` rather than a bare `<div>`: the
 * parent is `role="table"`, which accepts only rows as children, and a div
 * table has no colspan to widen a cell with.
 */
function Drilldown({
  agent,
  task,
  t,
}: {
  agent: SubAgentNode;
  task: string;
  t: (key: string) => string;
}) {
  const assistantName = useEventStore((s) => s.assistantName);
  const failure = failureLabel(agent, t);

  return (
    <div role="row" className="border-b border-border/70 bg-sheen/[0.04] last:border-b-0">
      <div role="cell" className="px-3 py-3.5">
        <div className="grid gap-3 lg:grid-cols-[1.4fr_1fr]">
          <Panel className="bg-card/60">
            <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
              <TerminalSquare className="h-3.5 w-3.5 text-primary" />
              {t("subagents_view.drill_tools")}
            </div>
            {agent.tool_calls.length > 0 ? (
              <div className="divide-y divide-border/70">
                {agent.tool_calls.map((call, idx) => (
                  <ToolCallRow key={`${agent.trace_id}-${idx}`} call={call} t={t} />
                ))}
              </div>
            ) : (
              <div className="px-3 py-3 text-sm text-muted-foreground">
                {t("subagents_view.drill_tools_empty")}
              </div>
            )}
          </Panel>

          <Panel className="bg-card/60">
            <div className="border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
              {fill(t("subagents_view.drill_details"), { agent: agentBrand(assistantName) })}
            </div>
            <div className="space-y-2.5 px-3 py-3 text-sm text-muted-foreground">
              <p className="line-clamp-4 text-foreground/85">{task}</p>
              {agent.context_hints.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {agent.context_hints.slice(0, 5).map((hint) => (
                    <span
                      key={hint}
                      className="inline-flex h-6 items-center rounded-md border border-border bg-card/60 px-2 text-[11px] text-foreground/80"
                    >
                      {hint}
                    </span>
                  ))}
                </div>
              )}
              {failure && <p className="text-destructive">{failure}</p>}
              <p className="font-mono text-[11px] text-muted-foreground/70">
                {fill(t("subagents_view.drill_trace"), { trace: agent.trace_id.slice(0, 10) })}
              </p>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function ToolCallRow({ call, t }: { call: ToolCallEntry; t: (key: string) => string }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_64px_96px] items-center gap-3 px-3 py-2.5 text-sm">
      <div className="min-w-0">
        <div className="truncate font-medium text-foreground">
          {call.tool_name || t("subagents_view.tool_unnamed")}
        </div>
        <div className="truncate text-xs text-muted-foreground" title={call.args_preview}>
          {call.args_preview || call.output_preview || "—"}
        </div>
      </div>
      <div className="text-right text-xs tabular-nums text-muted-foreground">
        {call.duration_ms != null ? formatRelative(call.duration_ms) : "—"}
      </div>
      <div className="flex justify-end">
        <StatusDot
          tone={TOOL_STATUS_TONE[call.status]}
          pulse={call.status === "running"}
          label={t(`subagents_view.tool_status.${call.status}`)}
        />
      </div>
    </div>
  );
}
