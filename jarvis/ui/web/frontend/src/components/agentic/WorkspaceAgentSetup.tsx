import { useEffect, useState } from "react";
import { Check } from "lucide-react";
import type { AgentStatus } from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";
import { AgentMark } from "./AgentMark";
import { MAX_WORKSPACE_PANES, balancedColumns } from "./workspaceDocking";

interface Props {
  agents: AgentStatus[];
  sessions: string[];
  onChange: (sessions: string[]) => void;
  disabled?: boolean;
  /** The server's per-workspace pane limit (`max_terminals`). */
  maxSessions?: number;
}

/** One launch plan: choose an agent for everyone, then customize individual seats. */
export function WorkspaceAgentSetup({ agents, sessions, onChange, disabled = false, maxSessions = MAX_WORKSPACE_PANES }: Props) {
  const [editingSeat, setEditing] = useState<number | null>(null);
  const editing = editingSeat !== null && editingSeat < sessions.length ? editingSeat : null;
  const firstAgent = agents[0]?.name ?? "";
  const selectedAgent = editing !== null
    ? sessions[editing]
    : sessions.every((agent) => agent === sessions[0]) ? sessions[0] : undefined;
  const counts = new Map<string, number>();
  sessions.forEach((agent) => counts.set(agent, (counts.get(agent) ?? 0) + 1));

  useEffect(() => {
    if (editingSeat !== null && editingSeat >= sessions.length) setEditing(null);
  }, [editingSeat, sessions.length]);

  useEffect(() => {
    // The dialog can open before launchability discovery completes. Fill only
    // unassigned seats; never replace a user's existing agent choice.
    if (firstAgent && sessions.some((agent) => !agent) && !disabled) {
      onChange(sessions.map((agent) => agent || firstAgent));
    }
  }, [firstAgent, sessions, onChange, disabled]);

  const chooseAgent = (agent: string) => {
    onChange(sessions.map((current, index) => editing === null || index === editing ? agent : current));
  };
  const chooseCount = (count: number) => {
    const fillAgent = selectedAgent || sessions[0] || firstAgent;
    onChange(Array.from({ length: count }, (_, index) => sessions[index] ?? fillAgent));
    if (editing !== null && editing >= count) setEditing(null);
  };
  const selection = "border-ring/70 bg-muted ring-1 ring-ring/20";
  const idle = "border-border bg-background/40 hover:border-foreground/30 hover:bg-muted/60";

  return <div className="space-y-6">
    <section aria-label="Coding agents">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">Agent</h3>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground" aria-live="polite">
            {editing === null ? "Choose for all sessions" : `Editing session ${editing + 1}`}
          </span>
          {editing !== null && <button type="button" disabled={disabled} onClick={() => setEditing(null)}
            className="rounded-md border border-border px-2 py-1 hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            All sessions
          </button>}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {agents.map((agent) => {
          const selected = agent.name === selectedAgent;
          const count = counts.get(agent.name) ?? 0;
          return <button key={agent.name} type="button" aria-label={agent.display_name}
            aria-pressed={selected} disabled={disabled} onClick={() => chooseAgent(agent.name)}
            className={cn("flex min-h-14 min-w-0 items-center gap-3 rounded-xl border px-4 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50", selected ? selection : idle)}>
            <AgentMark agent={agent.name} label={agent.display_name} logoUrl={agent.logo_url}
              variant="plain" className="h-6 w-6 opacity-100" />
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{agent.display_name}</span>
            {count > 0 && <span aria-hidden="true" className="text-xs tabular-nums text-muted-foreground">×{count}</span>}
            {selected && <Check aria-hidden="true" className="h-4 w-4 shrink-0" />}
          </button>;
        })}
      </div>
      {agents.length === 0 && <p className="rounded-lg border border-border p-4 text-sm text-muted-foreground">
        No coding agents available. Connect a coding agent in CLIs to continue.
      </p>}
    </section>

    <section aria-label="Number of sessions">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">How many sessions?</h3>
      <div className="flex flex-wrap items-center gap-2">
        {Array.from({ length: maxSessions }, (_, index) => index + 1).map((count) => <button
          key={count} type="button" aria-label={`${count} ${count === 1 ? "session" : "sessions"}`}
          aria-pressed={sessions.length === count} disabled={disabled || !firstAgent}
          onClick={() => chooseCount(count)}
          className={cn("flex h-11 w-11 items-center justify-center rounded-lg border text-sm font-semibold tabular-nums transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40", sessions.length === count ? selection : idle)}>
          {count}
        </button>)}
        <span className="ml-2 text-xs text-muted-foreground">
          Up to {maxSessions} per workspace{sessions.length > 1 && <> · opens as {balancedColumns(sessions.length)} × {Math.ceil(sessions.length / balancedColumns(sessions.length))}</>}
        </span>
      </div>
    </section>

    <section aria-label="Session lineup">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">Will launch</h3>
        <span className="text-xs text-muted-foreground">Click a session to choose a different agent</span>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {sessions.map((name, index) => {
          const agent = agents.find((entry) => entry.name === name);
          const label = agent?.display_name ?? "Choose an agent";
          return <button key={index} type="button" aria-label={`Edit session ${index + 1}: ${label}`}
            aria-pressed={editing === index} disabled={disabled || !firstAgent}
            onClick={() => setEditing((current) => current === index ? null : index)}
            className={cn("flex min-h-12 min-w-0 items-center gap-2 rounded-lg border px-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50", editing === index ? selection : idle)}>
            <span className="w-3 shrink-0 text-xs tabular-nums text-muted-foreground">{index + 1}</span>
            <AgentMark agent={name} label={label} logoUrl={agent?.logo_url} variant="plain" />
            <span className="truncate text-xs font-medium" title={label}>{label}</span>
          </button>;
        })}
      </div>
    </section>
  </div>;
}
