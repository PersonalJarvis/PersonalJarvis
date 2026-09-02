/**
 * The society frontend's data seam — every card, roster row and world walker
 * reads agents through the hooks in this file and nothing else.
 *
 * The M1 backend (docs/agent-society/MASTERPLAN.md §3.1: society.db roster +
 * /api/society routes) is being built in a parallel session. Until the two
 * sides are wired, `fetchSocietyRoster` resolves the clearly-labeled sample
 * roster from ./mockRoster plus whatever this window created — the ONE swap
 * point. Nothing else in the society components may know where agents come
 * from.
 *
 * The enums here mirror the roster schema (MASTERPLAN §3.1, agent-definition.md
 * §2) and cross Python ↔ SQL ↔ Pydantic ↔ TS ↔ UI once the backend is bound —
 * at that point they join the five-layer parity tests (AP-4). Keep names in
 * lockstep with the plan, not with what reads nicely in TS.
 */
import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import type { FigureRecipe } from "./figures/figureRecipe";
import { SAMPLE_ROSTER } from "./mockRoster";

/** MASTERPLAN §2.5 — exactly one lead (Jarvis), orchestrators may ASSIGN. */
export type AgentTier = "lead" | "orchestrator" | "specialist";

/** Semantic place in the world (§2.7) — the backend owns this, never pixels. */
export type AgentCheckpoint = "desk" | "meeting" | "archive" | "gate" | "idle";

/** Coarse run state for rows and badges; the event log holds the detail. */
export type AgentRunState = "idle" | "working" | "waiting" | "paused";

/** §6.2 — the unattended ceiling; "block" never appears (block is block). */
export type PermissionCeiling = "safe" | "monitor" | "ask";

/** agent-definition.md §3.2 — everything the tiers allow, or only an allow-list. */
export type GrantMode = "all" | "allowlist";

/**
 * Per-agent accent colours for flat UI (roster swatch, chat avatar ring).
 * The figure's own colours live in `figure.palette`; these three are derived
 * from it so the rail and the character always agree.
 */
export interface AgentPalette {
  primary: string;
  secondary: string;
  accent: string;
}

export interface AgentRoutine {
  id: string;
  label: string;
  /** Human-readable schedule ("daily 07:00"), not a cron string. */
  schedule: string;
  /** ISO timestamp of the next fire, when the scheduler knows one. */
  nextFire: string | null;
}

export interface AgentStats {
  runs: number;
  totalCostUsd: number;
  /** Epoch ms of the last completed run; null for a fresh agent. */
  lastActiveMs: number | null;
}

/** One roster row — the model card's whole world (MASTERPLAN §3.1, agent-definition §2). */
export interface SocietyAgent {
  agentId: string;
  name: string;
  /** One job line ("Research scout"). */
  title: string;
  /** Standing instructions, Markdown — the agent's own rulebook. */
  description: string;
  tier: AgentTier;
  /** Provider id as the agent-chat catalog knows it (e.g. "anthropic"). */
  provider: string;
  /** Display label for the provider, until the catalog lookup is wired. */
  providerLabel: string;
  model: string;
  /** The character: archetype, base, parts, palette. null = the palette tile. */
  figure: FigureRecipe | null;
  palette: AgentPalette;
  grantMode: GrantMode;
  toolGrants: string[];
  permissionCeiling: PermissionCeiling;
  dailyBudgetUsd: number;
  checkpoint: AgentCheckpoint;
  state: AgentRunState;
  createdMs: number;
  /**
   * The canonical agent_chat session bound to this agent (M2). Sample rows
   * carry null — the card then shows the honest empty state instead of an
   * invented transcript.
   */
  chatSessionId: string | null;
  routines: AgentRoutine[];
  stats: AgentStats;
}

/** What the creator hands over; everything else is derived or defaulted. */
export interface NewAgentInput {
  name: string;
  title: string;
  description: string;
  figure: FigureRecipe;
  palette: AgentPalette;
  provider: string;
  providerLabel: string;
  model: string;
  grantMode: GrantMode;
  permissionCeiling: PermissionCeiling;
  dailyBudgetUsd: number;
}

/** Agents created in THIS window before the backend exists — sample data, not persisted. */
const LOCAL_ROSTER: SocietyAgent[] = [];

/**
 * THE swap point: replace the sample resolve with
 * `fetch("/api/society/agents")` once the M1 routes are bound. Async already,
 * so the swap touches no caller.
 */
async function fetchSocietyRoster(): Promise<SocietyAgent[]> {
  return [...SAMPLE_ROSTER, ...LOCAL_ROSTER];
}

const ROSTER_QUERY_KEY = ["society", "roster"] as const;

export function useSocietyRoster() {
  return useQuery({
    queryKey: ROSTER_QUERY_KEY,
    queryFn: fetchSocietyRoster,
    staleTime: 30_000,
  });
}

/** One agent by id, riding the roster query so both share one fetch. */
export function useSocietyAgent(agentId: string | null) {
  const roster = useSocietyRoster();
  const agent =
    agentId === null ? null : (roster.data?.find((a) => a.agentId === agentId) ?? null);
  return { ...roster, agent };
}

/** A URL-safe id from a display name, unique against the rows already known. */
export function slugifyAgentName(name: string, taken: ReadonlySet<string>): string {
  const base =
    name
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "agent";
  let candidate = base;
  let n = 2;
  while (taken.has(candidate)) candidate = `${base}-${n++}`;
  return candidate;
}

/**
 * Create an agent. Today this appends a sample row for this window and
 * refreshes the roster; the swap to `POST /api/society/agents` changes only
 * the body of `create`.
 */
export function useCreateAgent() {
  const client = useQueryClient();
  const { data } = useSocietyRoster();
  return useCallback(
    async (input: NewAgentInput): Promise<SocietyAgent> => {
      const taken = new Set((data ?? []).map((a) => a.agentId));
      const agent: SocietyAgent = {
        agentId: slugifyAgentName(input.name, taken),
        name: input.name.trim(),
        title: input.title.trim(),
        description: input.description.trim(),
        tier: "specialist",
        provider: input.provider,
        providerLabel: input.providerLabel,
        model: input.model,
        figure: input.figure,
        palette: input.palette,
        grantMode: input.grantMode,
        toolGrants: [],
        permissionCeiling: input.permissionCeiling,
        dailyBudgetUsd: input.dailyBudgetUsd,
        checkpoint: "idle",
        state: "idle",
        createdMs: Date.now(),
        chatSessionId: null,
        routines: [],
        stats: { runs: 0, totalCostUsd: 0, lastActiveMs: null },
      };
      LOCAL_ROSTER.push(agent);
      await client.invalidateQueries({ queryKey: ROSTER_QUERY_KEY });
      return agent;
    },
    [client, data],
  );
}
