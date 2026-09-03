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
import { useQuery } from "@tanstack/react-query";

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
  /** Readable schedule ("every 24 h", "at 07:00"), built from the raw trigger. */
  schedule: string;
  /** Next fire in epoch ms, or null when the scheduler knows none. */
  dueMs: number | null;
  lastRunMs: number | null;
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

/** Seconds as the coarsest unit that stays a whole number: "6 h", "30 min". */
function humanEvery(seconds: number): string {
  if (seconds % 86_400 === 0) return `${seconds / 86_400} d`;
  if (seconds % 3_600 === 0) return `${seconds / 3_600} h`;
  if (seconds % 60 === 0) return `${seconds / 60} min`;
  return `${Math.round(seconds)} s`;
}

/**
 * The raw trigger object into one readable phrase.
 *
 * The scheduler stores a `TaskSpec` trigger (`jarvis/society/routines.py`),
 * not a sentence, and there is no server-side rendering of it — so the shapes
 * are mapped here, and an unknown kind falls back to its own name rather than
 * to an invented schedule.
 */
export function describeTrigger(trigger: unknown): string {
  if (!trigger || typeof trigger !== "object") return "";
  const t = trigger as Record<string, unknown>;
  const kind = String(t.kind ?? t.type ?? "");
  if (kind === "every" && typeof t.interval_seconds === "number") {
    return `every ${humanEvery(t.interval_seconds)}`;
  }
  if (kind === "at_time" && typeof t.iso_timestamp === "string") {
    const at = new Date(t.iso_timestamp);
    return Number.isNaN(at.getTime())
      ? "at a fixed time"
      : `at ${at.toLocaleString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
  }
  if (kind === "after_delay" && typeof t.delay_seconds === "number") {
    return `once, in ${humanEvery(t.delay_seconds)}`;
  }
  if (kind === "on_event" && typeof t.event_name === "string") return `on ${t.event_name}`;
  return kind;
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
    staleTime: 30_000,
    retry: false,
    queryFn: async (): Promise<LiveRoutine[]> => {
      const body = await getJson<{
        routines?: {
          id?: string;
          title?: string;
          state?: string;
          trigger?: unknown;
          due_at_ns?: number;
          last_run_ns?: number;
        }[];
      }>(`/api/society/agents/${encodeURIComponent(agentId ?? "")}/routines`);
      return (body?.routines ?? []).map((r, i) => ({
        id: String(r.id ?? i),
        title: String(r.title ?? ""),
        state: String(r.state ?? ""),
        schedule: describeTrigger(r.trigger),
        dueMs: nsToMs(r.due_at_ns),
        lastRunMs: nsToMs(r.last_run_ns),
      }));
    },
  });
}
