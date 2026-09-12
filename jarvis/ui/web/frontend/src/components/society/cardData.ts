import { describeTrigger } from "@/lib/triggerDescription";
export { describeTrigger } from "@/lib/triggerDescription";
/**
 * The per-agent detail the roster row does not carry.
 *
 * `GET /api/society/agents` answers with the profile and three derived
 * numbers; everything an agent has actually DONE lives behind three further
 * routes that nothing in the frontend was calling. That is why the card's
 * Routines block could only ever read "No routines yet" — not because the
 * agent had none, but because nobody asked.
 *
 * These hooks are deliberately quiet: `retry: false`, no error surfaced, and
 * every one of them returns an empty list when the backend is not there. The
 * card treats them as enrichment and never blocks on them.
 */
import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

/** One line of what the agent did or was told, from the society event log. */
export interface AgentActivity {
  id: string;
  /** CLAIM, ASSIGN, RESULT, DIGEST, MESSAGE … — the envelope's own type. */
  type: string;
  fromAgent: string | null;
  toAgent: string | null;
  tsMs: number;
  costUsd: number;
}

/** A skill the agent authored for itself after a finished task. */
export interface LearnedSkill {
  slug: string;
  name: string;
  description: string;
  whenToUse: string;
}

/** A scheduled task tagged for this agent, as the Automations store holds it. */
export interface LiveRoutine {
  id: string;
  title: string;
  state: string;
  /** Raw TaskSpec trigger; formatted in the list so the phrase follows the UI language. */
  trigger: unknown;
  /**
   * Preformatted schedule when there is no trigger (sample roster rows). The
   * list prefers `describeTrigger(trigger)` and falls back to this.
   */
  schedule: string;
  /** Next fire in epoch ms, or null when the scheduler knows none. */
  dueMs: number | null;
  lastRunMs: number | null;
  prompt?: string;
  members?: LiveRoutine[];
}

/** Per-agent browser status from `GET /api/society/agents/{id}/browser`. */
export interface AgentBrowserStatus {
  installed: boolean;
  mode: string;
  loggedIn: boolean;
  running: boolean;
}

/** Machine-wide browser-use venv install, from `GET /api/society/browser/status`. */
export interface BrowserInstallStatus {
  installed: boolean;
  phase: string;
  percent: number;
  detail: string;
  error: string;
  running: boolean;
}

const AGENT_TITLE_PREFIX = /^\[agent:[^\]]+\]\s*/i;

/** The scheduler stores `[agent:Name] Title`; the list shows just the title. */
export function displayRoutineTitle(title: string): string {
  const stripped = title.replace(AGENT_TITLE_PREFIX, "").trim();
  return stripped || title;
}

interface EnvelopeRow {
  event_id?: string;
  seq?: number;
  msg_type?: string;
  from_agent?: string | null;
  to_agent?: string | null;
  ts_ms?: number;
  cost_usd?: number;
}

async function getJson<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null; // the card says nothing rather than an error nobody can act on
  }
}

/** The line the routines list shows: live trigger, else the sample's own phrase. */
export function routineScheduleLine(routine: Pick<LiveRoutine, "trigger" | "schedule">, t: (key: string) => string): string {
  return describeTrigger(routine.trigger, t) || routine.schedule;
}

/** Nanoseconds (what the task store stores) to epoch ms, or null. */
function nsToMs(value: unknown): number | null {
  return typeof value === "number" && value > 0 ? Math.round(value / 1e6) : null;
}

/**
 * What the agent has been doing, and how many runs it has in flight.
 *
 * `events_for_agent` deliberately includes broadcasts (`to_agent IS NULL`), so
 * the rows are filtered here to the ones this agent actually sent or received
 * — a card headed "Recent" must not show the whole board's traffic.
 */
export function useAgentActivity(agentId: string | null) {
  return useQuery({
    queryKey: ["society", "agent-activity", agentId],
    enabled: Boolean(agentId),
    staleTime: 10_000,
    refetchInterval: 20_000,
    retry: false,
    queryFn: async (): Promise<{ events: AgentActivity[]; activeRuns: number }> => {
      const body = await getJson<{ recent_events?: EnvelopeRow[]; active_runs?: number }>(
        `/api/society/agents/${encodeURIComponent(agentId ?? "")}`,
      );
      const rows = body?.recent_events ?? [];
      const events = rows
        .filter((r) => r.from_agent === agentId || r.to_agent === agentId)
        .map((r, i) => ({
          id: String(r.event_id ?? r.seq ?? i),
          type: String(r.msg_type ?? ""),
          fromAgent: r.from_agent ?? null,
          toAgent: r.to_agent ?? null,
          tsMs: Number(r.ts_ms ?? 0),
          costUsd: Number(r.cost_usd ?? 0),
        }))
        .reverse(); // newest first
      return { events, activeRuns: Number(body?.active_runs ?? 0) };
    },
  });
}

/** The skills the agent taught itself, newest first is not knowable — as served. */
export function useAgentSkills(agentId: string | null) {
  return useQuery({
    queryKey: ["society", "agent-skills", agentId],
    enabled: Boolean(agentId),
    staleTime: 60_000,
    retry: false,
    queryFn: async (): Promise<LearnedSkill[]> => {
      const body = await getJson<{
        skills?: { slug?: string; name?: string; description?: string; when_to_use?: string }[];
      }>(`/api/society/agents/${encodeURIComponent(agentId ?? "")}/skills`);
      return (body?.skills ?? []).map((s) => ({
        slug: String(s.slug ?? ""),
        name: String(s.name ?? s.slug ?? ""),
        description: String(s.description ?? ""),
        whenToUse: String(s.when_to_use ?? ""),
      }));
    },
  });
}

/** The agent's scheduled tasks, as the Automations store actually holds them. */
export function useAgentRoutines(agentId: string | null) {
  return useQuery({
    queryKey: ["society", "agent-routines", agentId],
    enabled: Boolean(agentId),
    staleTime: 5_000,
    refetchInterval: import.meta.env.MODE === "test" ? false : 5_000,
    retry: false,
    queryFn: async (): Promise<LiveRoutine[]> => {
      const response = await fetch(`/api/society/agents/${encodeURIComponent(agentId ?? "")}/routines`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = await response.json() as {
        routines?: {
          id?: string;
          title?: string;
          state?: string;
          trigger?: unknown;
          due_at_ns?: number;
          last_run_ns?: number;
          prompt?: string;
          tags?: string[];
        }[];
      };
      const raw = body?.routines ?? [];
      const mapped = raw.map((r, i) => ({
        id: String(r.id ?? i),
        title: String(r.title ?? ""),
        state: String(r.state ?? ""),
        trigger: r.trigger ?? null,
        schedule: "",
        dueMs: nsToMs(r.due_at_ns),
        lastRunMs: nsToMs(r.last_run_ns),
        prompt: r.prompt ?? "",
      }));
      const groups = new Map<string, LiveRoutine[]>();
      mapped.forEach((row, i) => {
        const group = raw[i].tags?.find((tag) => tag.startsWith("routine-group:"))?.slice(14) ?? row.id;
        groups.set(group, [...(groups.get(group) ?? []), row]);
      });
      return [...groups.entries()].map(([id, members]) => {
        members.sort((a, b) => Number(b.id === id) - Number(a.id === id));
        const due = members.filter((m) => m.state === "scheduled" && m.dueMs).map((m) => m.dueMs!);
        const state = members.some((m) => m.state === "running") ? "running"
          : members.some((m) => m.state === "scheduled") ? "scheduled" : members[0].state;
        return { ...members[0], state, members, dueMs: due.length ? Math.min(...due) : null };
      });
    },
  });
}

/** This agent's own browser profile: installed / logged-in / running. */
export function useAgentBrowser(agentId: string | null) {
  return useQuery({
    queryKey: ["society", "agent-browser", agentId],
    enabled: Boolean(agentId),
    staleTime: 3_000,
    refetchInterval: import.meta.env.MODE === "test" ? false : 5_000,
    retry: false,
    queryFn: async (): Promise<AgentBrowserStatus> => {
      const body = await getJson<{
        installed?: boolean;
        mode?: string;
        logged_in_profile?: boolean;
        running?: boolean;
      }>(`/api/society/agents/${encodeURIComponent(agentId ?? "")}/browser`);
      return {
        installed: Boolean(body?.installed),
        mode: String(body?.mode ?? "own"),
        loggedIn: Boolean(body?.logged_in_profile),
        running: Boolean(body?.running),
      };
    },
  });
}

function installIsBusy(row: BrowserInstallStatus | undefined): boolean {
  if (!row) return false;
  if (row.running) return true;
  if (row.installed || row.phase === "idle" || row.phase === "done" || row.phase === "error") return false;
  return row.phase.length > 0;
}

/** The one-click browser-use venv on this machine. */
export function useBrowserInstallStatus() {
  return useQuery({
    queryKey: ["society", "browser-install"],
    staleTime: 2_000,
    refetchInterval: (query) =>
      import.meta.env.MODE === "test" ? false : installIsBusy(query.state.data) ? 1_000 : 15_000,
    retry: false,
    queryFn: async (): Promise<BrowserInstallStatus> => {
      const body = await getJson<{
        installed?: boolean;
        phase?: string;
        percent?: number;
        detail?: string;
        error?: string;
        running?: boolean;
      }>("/api/society/browser/status");
      return {
        installed: Boolean(body?.installed),
        phase: String(body?.phase ?? "idle"),
        percent: Number(body?.percent ?? 0),
        detail: String(body?.detail ?? ""),
        error: String(body?.error ?? ""),
        running: Boolean(body?.running),
      };
    },
  });
}

export function useStartBrowserInstall() {
  const client = useQueryClient();
  return useCallback(async () => {
    const res = await fetch("/api/society/browser/install", { method: "POST" });
    if (!res.ok) throw new Error(`install ${res.status}`);
    await client.invalidateQueries({ queryKey: ["society", "browser-install"] });
    await client.invalidateQueries({ queryKey: ["society", "agent-browser"] });
  }, [client]);
}

/**
 * Opens the headed login window on this computer. The request stays open
 * until that window closes (up to 15 minutes), so the caller must not await
 * it as if it were a short POST — fire and let the status poll catch up.
 */
export function useAgentBrowserLogin() {
  const client = useQueryClient();
  return useCallback((agentId: string) => {
    void fetch(`/api/society/agents/${encodeURIComponent(agentId)}/browser/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start_url: "" }),
    }).finally(() => {
      void client.invalidateQueries({ queryKey: ["society", "agent-browser", agentId] });
    });
  }, [client]);
}

export function useCreateAgentRoutine() {
  const client = useQueryClient();
  return useCallback(
    async (
      agentId: string,
      body: { title: string; prompt: string; schedule: Record<string, unknown> },
    ): Promise<{ id: string; title: string }> => {
      const res = await fetch(`/api/society/agents/${encodeURIComponent(agentId)}/routines`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = (await res.json().catch(() => null)) as { reason?: unknown; detail?: unknown } | null;
        const reason =
          (detail && typeof detail.reason === "string" && detail.reason) ||
          (detail && typeof detail.detail === "string" && detail.detail) ||
          String(res.status);
        throw new Error(reason);
      }
      const payload = (await res.json()) as { id?: string; title?: string };
      await client.invalidateQueries({ queryKey: ["society", "agent-routines", agentId] });
      await client.invalidateQueries({ queryKey: ["tasks"] });
      return { id: String(payload.id ?? ""), title: String(payload.title ?? body.title) };
    },
    [client],
  );
}
