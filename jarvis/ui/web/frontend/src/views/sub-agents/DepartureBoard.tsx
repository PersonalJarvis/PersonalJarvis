import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Radio,
  TerminalSquare,
  type LucideIcon,
  Wrench,
} from "lucide-react";

import type { SubAgentNode, ToolCallEntry } from "@/store/subAgents";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

const STATUS_COLOR: Record<SubAgentNode["status"], string> = {
  running: "text-primary",
  completed: "text-emerald-400",
  failed: "text-destructive",
};

const STATUS_LABEL: Record<SubAgentNode["status"], string> = {
  running: "ACTIVE",
  completed: "DONE",
  failed: "FAILED",
};

const TOOL_STATUS_LABEL: Record<ToolCallEntry["status"], string> = {
  running: "RUNNING",
  completed: "DONE",
  failed: "FAILED",
};

function formatRelative(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "-";
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
  return "-";
}

// User-facing role label only. The underlying engine, provider and model are
// deliberately NOT surfaced here: from the operator's perspective every node is
// just one of Jarvis' own Jarvis-Agents. `kind` is an internal routing tag
// (the top-level mission node vs. harness == the worker subprocess) and is
// never shown raw — see the subtitle below. The concrete "what is it doing"
// lives in the Task/Project column (`taskLabel`).
function displayAgentName(node: SubAgentNode): string {
  return node.kind === "harness" ? "Worker" : "Jarvis-Agent";
}

function taskLabel(node: SubAgentNode): string {
  return node.utterance || node.context_hints.at(0) || node.prompts.at(0) || "-";
}

function resultLabel(node: SubAgentNode): string {
  if (node.error) return node.error;
  const summary = [...node.prompts].reverse().find((p) => p.startsWith("[summary] "));
  if (summary) return summary.replace("[summary] ", "");
  if (node.status === "completed") return "Done";
  if (node.status === "running") return "In progress";
  return "-";
}

interface Props {
  agents?: SubAgentNode[];
  snapshotError?: string | null;
}

export function DepartureBoard({ agents = [], snapshotError = null }: Props) {
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

  const toggle = (traceId: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(traceId)) next.delete(traceId);
      else next.add(traceId);
      return next;
    });

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-[radial-gradient(circle_at_78%_0%,rgba(255,214,10,0.08),transparent_30%),linear-gradient(rgba(255,214,10,0.016)_1px,transparent_1px),linear-gradient(90deg,rgba(255,214,10,0.012)_1px,transparent_1px)] bg-[length:auto,72px_72px,72px_72px]">
      <div className="grid grid-cols-2 gap-px border-b border-zinc-900 bg-zinc-900/70 md:grid-cols-5">
        <Metric label="Jarvis-Agents" value={agents.length.toString()} icon={Bot} />
        <Metric label="Active" value={activeCount.toString()} icon={Radio} tone="primary" />
        <Metric label="Done" value={doneCount.toString()} icon={CheckCircle2} tone="success" />
        <Metric label="Failed" value={failedCount.toString()} icon={Activity} tone={failedCount ? "danger" : "muted"} />
        <Metric label="Tool calls" value={toolCount.toString()} icon={Wrench} />
      </div>

      <div className="flex min-h-0 flex-1 flex-col px-5 py-4">
        <div className="mb-3 flex items-center justify-between gap-4">
          <div>
            <div className="font-mono text-[11px] uppercase tracking-[0.32em] text-primary/80">
              Jarvis-Agent operations board
            </div>
            <div className="mt-1 text-xs text-zinc-500">
              Live table from the Jarvis-Agent backend registry and WebSocket events.
            </div>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-primary/20 bg-primary/5 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.22em] text-primary">
            <span className="h-1.5 w-1.5 rounded-full bg-primary animate-jarvis-pulse" />
            {activeCount > 0 ? "live" : "standby"}
          </div>
        </div>

        {snapshotError && (
          <div className="mb-3 flex items-center gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            <CircleAlert className="h-4 w-4" />
            Jarvis-Agent snapshot could not be loaded: {snapshotError}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-zinc-800/80 bg-black/35 scrollbar-jarvis">
          <div className="min-w-[1040px]">
            <BoardHeader />
            {sortedAgents.length === 0 ? (
              <EmptyState />
            ) : (
              <div className="divide-y divide-zinc-900/80">
                {sortedAgents.map((agent) => (
                  <AgentRow
                    key={agent.trace_id}
                    agent={agent}
                    nowMs={nowMs}
                    expanded={expanded.has(agent.trace_id)}
                    onToggle={() => toggle(agent.trace_id)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  icon: Icon,
  tone = "muted",
}: {
  label: string;
  value: string;
  icon: LucideIcon;
  tone?: "muted" | "primary" | "success" | "danger";
}) {
  return (
    <div className="flex min-w-0 items-center gap-3 bg-zinc-950 px-4 py-3">
      <Icon
        className={cn(
          "h-4 w-4 shrink-0",
          tone === "primary" && "text-primary",
          tone === "success" && "text-emerald-400",
          tone === "danger" && "text-destructive",
          tone === "muted" && "text-zinc-500",
        )}
      />
      <div className="min-w-0">
        <div className="truncate font-mono text-[10px] uppercase tracking-[0.18em] text-zinc-600">
          {label}
        </div>
        <div className="truncate text-sm font-semibold text-zinc-100">{value}</div>
      </div>
    </div>
  );
}

function BoardHeader() {
  return (
    <div className="sticky top-0 z-10 grid grid-cols-[44px_1.4fr_3fr_0.85fr_0.7fr_0.8fr_1.4fr] gap-4 border-b border-zinc-800 bg-zinc-950/95 px-4 py-3 font-mono text-[10px] uppercase tracking-[0.18em] text-zinc-500 backdrop-blur">
      <span />
      <span>Jarvis-Agent</span>
      <span>Task / Project</span>
      <span>Status</span>
      <span>Tools</span>
      <span>Runtime</span>
      <span>Result</span>
    </div>
  );
}

function EmptyState() {
  const assistantName = useEventStore((s) => s.assistantName);
  return (
    <div className="flex min-h-[420px] flex-col items-center justify-center px-8 text-center">
      <Bot className="mb-4 h-8 w-8 text-zinc-600" />
      <div className="font-display text-base font-semibold text-zinc-200">
        No Jarvis-Agents are running right now.
      </div>
      <p className="mt-2 max-w-lg text-sm text-zinc-500">
        When {assistantName} starts a real Jarvis-Agent, it will appear here with its task, status,
        tool calls, runtime and result.
      </p>
    </div>
  );
}

function AgentRow({
  agent,
  nowMs,
  expanded,
  onToggle,
}: {
  agent: SubAgentNode;
  nowMs: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const hasDrilldown = agent.tool_calls.length > 0 || agent.error || agent.prompts.length > 0;

  return (
    <div>
      <button
        type="button"
        disabled={!hasDrilldown}
        onClick={hasDrilldown ? onToggle : undefined}
        className={cn(
          "grid w-full grid-cols-[44px_1.4fr_3fr_0.85fr_0.7fr_0.8fr_1.4fr] gap-4 px-4 py-3.5 text-left font-mono text-xs tabular-nums",
          hasDrilldown && "transition-colors hover:bg-zinc-900/55",
        )}
      >
        <span className="flex items-center text-zinc-600">
          {hasDrilldown ? (
            expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />
          ) : (
            <span className="pl-1">-</span>
          )}
        </span>
        <span className="min-w-0">
          <span className="block truncate text-zinc-100">{displayAgentName(agent)}</span>
          <span className="block truncate text-[10px] uppercase tracking-[0.16em] text-zinc-600">
            {agent.kind === "harness" ? "worker" : "jarvis-agent"}
          </span>
        </span>
        <span className="truncate text-zinc-400" title={taskLabel(agent)}>
          {taskLabel(agent)}
        </span>
        <span className={cn("flex items-center gap-2", STATUS_COLOR[agent.status])}>
          {agent.status === "running" && <span className="h-1.5 w-1.5 rounded-full bg-primary animate-jarvis-pulse" />}
          {STATUS_LABEL[agent.status]}
        </span>
        <span className="text-zinc-400">{agent.tool_calls.length}</span>
        <span className="text-zinc-400">{runtimeLabel(agent, nowMs)}</span>
        <span className="truncate text-zinc-400" title={resultLabel(agent)}>
          {resultLabel(agent)}
        </span>
      </button>

      {expanded && hasDrilldown && (
        <div className="border-t border-zinc-900/80 bg-zinc-950/55 px-4 pb-4 pt-3">
          <div className="grid grid-cols-[1.35fr_1fr] gap-4">
            <div className="min-w-0 rounded-md border border-zinc-800/70 bg-black/30">
              <div className="flex items-center gap-2 border-b border-zinc-900 px-3 py-2 font-mono text-[10px] uppercase tracking-[0.18em] text-zinc-500">
                <TerminalSquare className="h-3.5 w-3.5 text-primary" />
                Tool calls
              </div>
              {agent.tool_calls.length > 0 ? (
                <div className="divide-y divide-zinc-900/80">
                  {agent.tool_calls.map((call, idx) => (
                    <ToolCallRow key={`${agent.trace_id}-${idx}`} call={call} />
                  ))}
                </div>
              ) : (
                <div className="px-3 py-3 font-mono text-xs text-zinc-600">No tool calls recorded.</div>
              )}
            </div>

            <div className="min-w-0 rounded-md border border-zinc-800/70 bg-black/30">
              <div className="border-b border-zinc-900 px-3 py-2 font-mono text-[10px] uppercase tracking-[0.18em] text-zinc-500">
                Jarvis-Agent details
              </div>
              <div className="space-y-2 px-3 py-3 font-mono text-xs text-zinc-500">
                <div className="line-clamp-4">{taskLabel(agent)}</div>
                {agent.context_hints.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {agent.context_hints.slice(0, 5).map((hint) => (
                      <span key={hint} className="rounded border border-primary/20 bg-primary/5 px-2 py-0.5 text-[10px] text-primary/80">
                        {hint}
                      </span>
                    ))}
                  </div>
                )}
                {agent.error && <div className="text-destructive">{agent.error}</div>}
                <div className="text-[10px] uppercase tracking-[0.16em] text-zinc-700">
                  trace {agent.trace_id.slice(0, 10)}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ToolCallRow({ call }: { call: ToolCallEntry }) {
  return (
    <div className="grid grid-cols-[1fr_110px_90px] gap-3 px-3 py-2.5 font-mono text-xs tabular-nums">
      <div className="min-w-0">
        <div className="truncate text-zinc-300">{call.tool_name || "tool"}</div>
        <div className="truncate text-[11px] text-zinc-600" title={call.args_preview}>
          {call.args_preview || call.output_preview || "-"}
        </div>
      </div>
      <div className="text-zinc-500">
        {call.duration_ms != null ? formatRelative(call.duration_ms) : "-"}
      </div>
      <div
        className={cn(
          "text-right",
          call.status === "running" && "text-primary",
          call.status === "completed" && "text-emerald-400",
          call.status === "failed" && "text-destructive",
        )}
      >
        {TOOL_STATUS_LABEL[call.status]}
      </div>
    </div>
  );
}
