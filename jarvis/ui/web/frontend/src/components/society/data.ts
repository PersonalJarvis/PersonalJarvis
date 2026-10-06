/**
 * The society frontend's data seam — every card, roster row and world walker
 * reads agents through the hooks in this file and nothing else.
 *
 * Source of truth is `/api/society` (jarvis/ui/web/society_routes.py, rows
 * typed in lib/societyApi.ts). When the backend is unreachable or has no
 * agents yet, the clearly-labeled sample roster stands in and the rail says
 * so — nothing else in the society components may know where agents come
 * from.
 *
 * The enums here mirror the roster schema (MASTERPLAN §3.1, agent-definition.md
 * §2); the const arrays in lib/societyApi.ts are pinned to the Python enums by
 * the five-layer parity test (AP-4).
 */
import { useCallback, useEffect } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import type { MentionPlugin } from "./chat/mentionItems";

import type { AgentApprovalMode, AgentRuntime, Checkpoint, SocietyAgentRow } from "@/lib/societyApi";
import { NEUTRAL_ASSISTANT_NAME } from "@/lib/assistantNameCache";
import { useEventStore } from "@/store/events";

import { PALETTE_PRESETS, resolvePalette, type FigureRecipe } from "./figures/figureRecipe";
import { SAMPLE_ROSTER } from "./mockRoster";
import { beginRetirement, retirementRunning, retirementStageMounted } from "./world/retireStore";
import { announceSpawn } from "./world/spawnStore";

/** MASTERPLAN §2.5 — exactly one lead (Jarvis), orchestrators may ASSIGN. */
export type AgentTier = "lead" | "orchestrator" | "specialist";

/**
 * Semantic place in the world (§2.7) — the backend owns this, never pixels.
 *
 * An ALIAS of the checked list, not a second copy of it: `CHECKPOINTS` in
 * lib/societyApi.ts is what the five-layer parity test pins to the Python
 * enum, and a hand-kept duplicate here is exactly the enum drift that keeps
 * costing bugs (docs/BUGS.md).
 */
export type AgentCheckpoint = Checkpoint;

/** Coarse run state for rows and badges; the event log holds the detail. */
export type AgentRunState = "idle" | "working" | "waiting" | "paused";

/** §6.2 — the unattended ceiling; "block" never appears (block is block). */
export type PermissionCeiling = "safe" | "monitor" | "ask";
export type { AgentApprovalMode };

/** agent-definition.md §3.2 — everything the tiers allow, or only an allow-list. */
export type GrantMode = "all" | "allowlist";

/**
 * Per-agent accent colours for flat UI (roster swatch, chat avatar ring).
 * Derived from the figure's palette so the rail and the character agree.
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
  /**
   * Spent since midnight UTC. The daily budget is a real gate — the scheduler
   * refuses a dispatch once this reaches `dailyBudgetUsd` — so the card shows
   * the two together rather than a cap with nothing beside it.
   */
  spentTodayUsd: number;
  /** Epoch ms of the last completed run; null for a fresh agent. */
  lastActiveMs: number | null;
}

/** Grok-style rules under the ceiling (agent-definition §3.4); require wins. */
export interface ApprovalRules {
  requireApproval: string[];
  alwaysAllow: string[];
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
  /** Provider id as the agent-chat catalog knows it (e.g. "anthropic"); "" = Jarvis' default brain. */
  provider: string;
  /** Display label for the provider, until the catalog lookup is wired. */
  providerLabel: string;
  model: string;
  effort: string;
  /** Stored subscription login; empty means the platform's active account. */
  accountId?: string;
  /** Where the agent runs: null/absent = this computer, else a connected computer id. */
  computerId?: string | null;
  /** The agent loop that runs its turns (docs/agent-runtimes.md); absent = Jarvis. */
  runtime?: AgentRuntime;
  /** The character: archetype, base, parts, palette. null = the palette tile. */
  figure: FigureRecipe | null;
  palette: AgentPalette;
  grantMode: GrantMode;
  /** Capability ids on the allow-list (read only when grantMode = allowlist). */
  toolGrants: string[];
  /** Capability ids the agent reaches for first (derived from the description, editable). */
  focus: string[];
  /** Capability ids never offered to this agent. */
  denies: string[];
  approvalRules: ApprovalRules;
  permissionCeiling: PermissionCeiling;
  approvalMode?: AgentApprovalMode | null;
  dailyBudgetUsd: number;
  checkpoint: AgentCheckpoint;
  state: AgentRunState;
  /** The durable lifecycle state behind `state` ("active" | "paused" | "archived"). */
  lifecycle: "active" | "paused" | "archived";
  createdMs: number;
  /** How many runs may be in flight under this identity at once (the scheduler enforces it). */
  maxConcurrentRuns: number;
  /** Where its files live, relative to the data dir — its own sandbox. */
  workspaceDir: string;
  /** Where its notes land in the shared wiki. */
  wikiNamespace: string;
  /** The canonical agent_chat session bound to this agent; null on sample rows. */
  chatSessionId: string | null;
  routines: AgentRoutine[];
  stats: AgentStats;
}

export interface RosterData {
  agents: SocietyAgent[];
  /** True while rows come from the sample roster rather than society.db. */
  sample: boolean;
}

/**
 * Sample rows retired in this window. The sample roster is a frozen module
 * constant, so a retirement there is remembered here instead of mutating it.
 */
const RETIRED_SAMPLE = new Set<string>();

// ---------------------------------------------------------------------------
// row ↔ agent
// ---------------------------------------------------------------------------

/** A default look for a backend row that never chose one: deterministic per id, so it stays. */
export function defaultFigureFor(agentId: string, tier: AgentTier): FigureRecipe {
  if (tier === "lead") {
    // The lead is Jarvis, and Jarvis is Gigi — the app's own mascot.
    return { contract: 1, archetype: "spirit", base: "gigi", parts: {}, style: "spirit" };
  }
  let h = 0;
  for (let i = 0; i < agentId.length; i++) h = (h * 31 + agentId.charCodeAt(i)) | 0;
  const preset = PALETTE_PRESETS[Math.abs(h) % PALETTE_PRESETS.length];
  return { contract: 1, archetype: "biped", base: "rogue", parts: {}, palette: { ...preset.palette } };
}

function recipeFromAvatar(avatar: Record<string, unknown> | null | undefined): FigureRecipe | null {
  if (!avatar || avatar.contract !== 1 || typeof avatar.base !== "string") return null;
  return avatar as unknown as FigureRecipe;
}

function paletteFor(figure: FigureRecipe | null): AgentPalette {
  const p = resolvePalette(figure);
  return { primary: p.primary, secondary: p.secondary, accent: p.accent };
}

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google",
  gemini: "Google Gemini",
  xai: "xAI",
  groq: "Groq",
  mistral: "Mistral",
  deepseek: "DeepSeek",
  ollama: "Ollama (local)",
  "local-openai": "Local server",
};

export function rowToAgent(row: SocietyAgentRow): SocietyAgent {
  const tier = row.tier as AgentTier;
  const figure = recipeFromAvatar(row.avatar) ?? defaultFigureFor(row.agent_id, tier);
  const runState: AgentRunState =
    row.state === "paused" ? "paused" : ((row.run_state as AgentRunState | undefined) ?? "idle");
  return {
    agentId: row.agent_id,
    name: row.name,
    title: row.title,
    description: row.description,
    tier,
    provider: row.provider,
    providerLabel: row.provider ? (PROVIDER_LABELS[row.provider] ?? row.provider) : "",
    model: row.model,
    accountId: row.account_id,
    computerId: row.computer_id ?? null,
    runtime: row.runtime ?? "jarvis",
    effort: row.effort,
    figure,
    palette: paletteFor(figure),
    grantMode: row.grant_mode as GrantMode,
    toolGrants: row.grants ?? [],
    focus: row.focus ?? [],
    denies: row.denies ?? [],
    approvalRules: {
      requireApproval: row.approval_rules?.require_approval ?? [],
      alwaysAllow: row.approval_rules?.always_allow ?? [],
    },
    permissionCeiling: row.permission_ceiling as PermissionCeiling,
    approvalMode: row.approval_mode ?? null,
    dailyBudgetUsd: row.daily_budget_usd,
    checkpoint: row.checkpoint as AgentCheckpoint,
    state: runState,
    lifecycle: row.state,
    createdMs: row.created_ms,
    maxConcurrentRuns: row.max_concurrent_runs ?? 1,
    workspaceDir: row.workspace_dir ?? "",
    wikiNamespace: row.wiki_namespace ?? "",
    chatSessionId: row.session_id || null,
    routines: [],
    stats: {
      runs: row.stats?.runs ?? 0,
      totalCostUsd: row.stats?.total_cost_usd ?? 0,
      spentTodayUsd: row.stats?.spent_today_usd ?? 0,
      lastActiveMs: row.stats?.last_active_ms ?? null,
    },
  };
}

// ---------------------------------------------------------------------------
// queries
// ---------------------------------------------------------------------------

const ROSTER_QUERY_KEY = ["society", "roster"] as const;
const CAPABILITIES_QUERY_KEY = ["society", "capabilities"] as const;

async function fetchSocietyRoster(): Promise<RosterData> {
  try {
    const res = await fetch("/api/society/agents");
    if (res.ok) {
      const body = (await res.json()) as { agents?: SocietyAgentRow[] };
      const rows = body.agents ?? [];
      if (rows.length > 0) {
        return { agents: rows.map(rowToAgent), sample: false };
      }
    }
  } catch {
    // Unreachable backend: the sample roster below says so on the rail.
  }
  const rows = [...SAMPLE_ROSTER].filter((a) => !RETIRED_SAMPLE.has(a.agentId));
  return { agents: rows, sample: true };
}

/**
 * Apply a change to the cached roster right away, then refresh it in the
 * background. A create or a retirement shows on the rail the moment the
 * server said yes, instead of after a second full `GET /agents`.
 */
function patchRoster(
  client: QueryClient,
  change: (agents: readonly SocietyAgent[]) => SocietyAgent[],
): void {
  client.setQueryData<RosterData>(ROSTER_QUERY_KEY, (prev) =>
    prev && !prev.sample ? { ...prev, agents: change(prev.agents) } : prev,
  );
  void client.invalidateQueries({ queryKey: ROSTER_QUERY_KEY });
}

/**
 * The lead is the assistant itself, so it wears the name the user gave it
 * through the wake phrase ("George"), not the roster row's seeded name. The
 * neutral fallback never replaces a real row name while the seed is pending.
 */
export function withLeadName(data: RosterData, assistantName: string): RosterData {
  const name = assistantName.trim();
  if (!name || name === NEUTRAL_ASSISTANT_NAME) return data;
  if (!data.agents.some((a) => a.tier === "lead" && a.name !== name)) return data;
  return { ...data, agents: data.agents.map((a) => (a.tier === "lead" ? { ...a, name } : a)) };
}

export function useSocietyRoster() {
  const assistantName = useEventStore((s) => s.assistantName) ?? "";
  const select = useCallback((data: RosterData) => withLeadName(data, assistantName), [assistantName]);
  return useQuery({
    queryKey: ROSTER_QUERY_KEY,
    queryFn: fetchSocietyRoster,
    select,
    staleTime: 15_000,
    // A refetch mid-ceremony would delete the figure being carried to the
    // mine out from under the animation; the commit invalidates instead.
    refetchInterval: () => (retirementRunning() ? false : 30_000),
  });
}

/** One agent by id, riding the roster query so both share one fetch. */
export function useSocietyAgent(agentId: string | null) {
  const roster = useSocietyRoster();
  const agent =
    agentId === null ? null : (roster.data?.agents.find((a) => a.agentId === agentId) ?? null);
  return { ...roster, agent };
}

/** One capability of the catalog (agent-definition §3.1) as GET /api/society/capabilities lists it. */
export interface Capability {
  id: string;
  kind: "plugin" | "cli" | "mcp" | "skill" | "core" | string;
  label: string;
  one_liner: string;
  risk_tier: string;
  connected: boolean;
  tool_name: string;
  /** Extra words the @-picker may match (server name, aliases). */
  aliases?: string[];
}

async function fetchCapabilities(): Promise<Capability[]> {
  const res = await fetch("/api/society/capabilities", { cache: "no-store" });
  if (!res.ok) throw new Error(`capabilities ${res.status}`);
  const body = (await res.json()) as { capabilities?: Capability[] };
  return body.capabilities ?? [];
}

export function useSocietyCapabilities(enabled = true, mentionOpen = false) {
  const query = useQuery({
    queryKey: CAPABILITIES_QUERY_KEY,
    queryFn: fetchCapabilities,
    enabled,
    staleTime: 60_000,
    retry: false,
  });
  const plugins = useQuery({
    queryKey: ["marketplace-plugins"],
    queryFn: async () => {
      const res = await fetch("/api/marketplace/plugins", { cache: "no-store" });
      if (!res.ok) throw new Error(`plugins ${res.status}`);
      return res.json() as Promise<{ plugins: MentionPlugin[] }>;
    },
    enabled: enabled && mentionOpen,
    staleTime: 0,
    retry: false,
  });
  const { refetch } = query;
  useEffect(() => {
    if (enabled && mentionOpen) void refetch();
  }, [enabled, mentionOpen, refetch]);
  return { ...query, plugins: plugins.data?.plugins ?? [], inventoryError: query.isError || plugins.isError,
    retryInventory: () => { void refetch(); void plugins.refetch(); } };
}

/**
 * One-click creation (`POST /api/society/agents` with no name): the backend
 * picks a placeholder name, a random look and the lead chat's current seat,
 * and the agent finds its role in its first conversation. A failure throws —
 * there is no local stand-in row, so the person sees the real error.
 */
export function useQuickCreateAgent() {
  const client = useQueryClient();
  return useCallback(async (): Promise<SocietyAgent> => {
    const res = await fetch("/api/society/agents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tier: "specialist" }),
    });
    if (!res.ok) {
      const detail = (await res.json().catch(() => null)) as { detail?: unknown } | null;
      const reason = detail?.detail;
      throw new Error(
        typeof reason === "string"
          ? reason
          : reason && typeof reason === "object" && "detail" in reason
            ? String((reason as { detail: unknown }).detail)
            : `create ${res.status}`,
      );
    }
    const created = (await res.json()) as { agent: SocietyAgentRow };
    // The island owes this row an entrance: it walks out of the foundry.
    announceSpawn(created.agent.agent_id);
    const agent = rowToAgent(created.agent);
    patchRoster(client, (agents) => [...agents.filter((a) => a.agentId !== agent.agentId), agent]);
    return agent;
  }, [client]);
}

/**
 * Decide a configuration proposal the agent made in its chat
 * (`POST /api/society/proposals/{id}/resolve`). A yes APPLIES the change on
 * the roster, so the roster query is refreshed afterwards.
 */
export function useResolveProposal() {
  const client = useQueryClient();
  return useCallback(
    async (proposalId: string, approve: boolean, note = ""): Promise<void> => {
      const res = await fetch(`/api/society/proposals/${encodeURIComponent(proposalId)}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approve, note }),
      });
      if (!res.ok) throw new Error(`proposal ${res.status}`);
      await client.invalidateQueries({ queryKey: ROSTER_QUERY_KEY });
    },
    [client],
  );
}

/**
 * Edit an agent's standing instructions (`PATCH /api/society/agents/{id}` with
 * the description only). The route keeps the focus and approval rules the
 * agent earned in its chat.
 */
export function useUpdateAgentDescription() {
  const client = useQueryClient();
  return useCallback(
    async (agent: SocietyAgent, description: string): Promise<void> => {
      const sample = SAMPLE_ROSTER.includes(agent);
      if (!sample) {
        const res = await fetch(`/api/society/agents/${encodeURIComponent(agent.agentId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ description }),
        });
        if (!res.ok) throw new Error(`description ${res.status}`);
      } else {
        agent.description = description;
      }
      await client.invalidateQueries({ queryKey: ROSTER_QUERY_KEY });
    },
    [client],
  );
}

/** The identity an `identity` proposal replaced, as its outcome card carries it. */
export interface PreviousIdentity {
  name: string;
  title: string;
  description: string;
  focus: string[];
  /** Present when the identity also wrote derived approval rules. */
  approval_rules?: { require_approval: string[]; always_allow: string[] };
}

/**
 * Undo an applied `identity` proposal: write the previous name, title,
 * description, focus and approval rules back. The explicit focus keeps the route from
 * re-deriving one from the restored text.
 */
export function useRestoreIdentity() {
  const client = useQueryClient();
  return useCallback(
    async (agentId: string, previous: PreviousIdentity): Promise<void> => {
      const res = await fetch(`/api/society/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(previous),
      });
      if (!res.ok) throw new Error(`identity ${res.status}`);
      await client.invalidateQueries({ queryKey: ROSTER_QUERY_KEY });
    },
    [client],
  );
}

/** Persist the roster choice and re-seat its canonical chat without losing history. */
export function useUpdateAgentModel() {
  const client = useQueryClient();
  return useCallback(async (agentId: string, choice: {
    provider: string; model: string; effort: string; account_id: string;
    /** Switch the agent loop with the model; omitted keeps the current one. */
    runtime?: AgentRuntime;
  }) => {
    const res = await fetch(`/api/society/agents/${encodeURIComponent(agentId)}/model`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(choice),
    });
    if (!res.ok) {
      // A 422 explains itself ("Hermes and OpenClaw run on an API key…").
      const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
      throw new Error(typeof body?.detail === "string" ? body.detail : `HTTP ${res.status}`);
    }
    const body = await res.json() as { agent: SocietyAgentRow };
    const updated = rowToAgent(body.agent);
    client.setQueryData<RosterData>(ROSTER_QUERY_KEY, (previous) => previous ? {
      ...previous,
      agents: previous.agents.map((agent) => agent.agentId === agentId ? updated : agent),
    } : previous);
    await client.invalidateQueries({ queryKey: ROSTER_QUERY_KEY });
  }, [client]);
}

/** The knobs the card lets a person turn after the agent exists. */
export interface AgentLimits {
  /** 0 means no cap — the scheduler skips the budget gate entirely. */
  dailyBudgetUsd: number;
  permissionCeiling: PermissionCeiling;
  approvalMode?: AgentApprovalMode | null;
  maxConcurrentRuns: number;
}

/**
 * Change what the agent is allowed to spend, decide and run at once.
 *
 * All three are enforced — the budget by `society/scheduler`, the ceiling by
 * `society/approvals.decide`, the concurrency by the scheduler's run count —
 * and all three were settable only at creation, behind the Advanced
 * disclosure, and never afterwards (maintainer, 2026-09-03). `PATCH` has
 * always accepted them; nothing was calling it with them.
 *
 * The description is deliberately NOT sent along: the route re-derives focus
 * whenever a title or description arrives, so a budget edit must not carry
 * prose with it.
 */
export function useUpdateAgentLimits() {
  const client = useQueryClient();
  return useCallback(
    async (agent: SocietyAgent, limits: AgentLimits): Promise<void> => {
      const sample = SAMPLE_ROSTER.includes(agent);
      const body = {
        daily_budget_usd: Math.max(0, limits.dailyBudgetUsd),
        permission_ceiling: limits.permissionCeiling,
        ...(limits.approvalMode ? { approval_mode: limits.approvalMode } : {}),
        max_concurrent_runs: Math.max(1, Math.round(limits.maxConcurrentRuns)),
      };
      if (!sample) {
        const res = await fetch(`/api/society/agents/${encodeURIComponent(agent.agentId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`limits ${res.status}`);
      } else {
        agent.dailyBudgetUsd = body.daily_budget_usd;
        agent.permissionCeiling = body.permission_ceiling;
        if (limits.approvalMode) agent.approvalMode = limits.approvalMode;
        agent.maxConcurrentRuns = body.max_concurrent_runs;
      }
      await client.invalidateQueries({ queryKey: ROSTER_QUERY_KEY });
    },
    [client],
  );
}

/** Move an agent to another computer (`PATCH computer_id`); "" = this computer. */
export function useSetAgentComputer() {
  const client = useQueryClient();
  return useCallback(
    async (agent: SocietyAgent, computerId: string): Promise<void> => {
      const sample = SAMPLE_ROSTER.includes(agent);
      if (sample) {
        agent.computerId = computerId || null;
      } else {
        const res = await fetch(`/api/society/agents/${encodeURIComponent(agent.agentId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ computer_id: computerId }),
        });
        if (!res.ok) {
          const detail = (await res.json().catch(() => null)) as { detail?: unknown } | null;
          const inner = detail?.detail as { detail?: unknown } | string | undefined;
          const message = typeof inner === "string" ? inner : typeof inner?.detail === "string" ? inner.detail : "";
          throw new Error(message || `computer ${res.status}`);
        }
      }
      await client.invalidateQueries({ queryKey: ROSTER_QUERY_KEY });
    },
    [client],
  );
}

/** Pause or resume an agent (`PATCH /api/society/agents/{id}`); sample rows flip locally. */
export function useSetAgentPaused() {
  const client = useQueryClient();
  return useCallback(
    async (agent: SocietyAgent, paused: boolean): Promise<void> => {
      const sample = SAMPLE_ROSTER.includes(agent);
      if (!sample) {
        const res = await fetch(`/api/society/agents/${encodeURIComponent(agent.agentId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ state: paused ? "paused" : "active" }),
        });
        if (!res.ok) throw new Error(`pause ${res.status}`);
      } else {
        agent.state = paused ? "paused" : "idle";
        agent.lifecycle = paused ? "paused" : "active";
      }
      await client.invalidateQueries({ queryKey: ROSTER_QUERY_KEY });
    },
    [client],
  );
}

/**
 * Retire an agent: `DELETE /api/society/agents/{id}` archives the row, and the
 * island plays the execution (`world/retirement.ts`) before the roster is
 * refreshed and the figure leaves.
 *
 * The order matters. The deletion goes FIRST, so a refusal — the lead cannot
 * be archived, the backend is down — is an error the person sees straight
 * away, instead of a twenty-second ceremony ending in a figure that quietly
 * comes back. The roster query is invalidated only when the body is in the
 * mine; the refetch interval is paused for the same reason.
 */
export function useRetireAgent() {
  const client = useQueryClient();
  return useCallback(
    async (agent: SocietyAgent, executioner: SocietyAgent | null): Promise<void> => {
      if (agent.tier === "lead") throw new Error("the lead cannot be retired");
      const sample = SAMPLE_ROSTER.includes(agent);
      if (sample) {
        RETIRED_SAMPLE.add(agent.agentId);
      } else {
        const res = await fetch(`/api/society/agents/${encodeURIComponent(agent.agentId)}`, {
          method: "DELETE",
        });
        if (!res.ok) {
          const detail = (await res.json().catch(() => null)) as { detail?: unknown } | null;
          const reason =
            detail && typeof detail.detail === "object" && detail.detail !== null
              ? String((detail.detail as { reason?: unknown }).reason ?? res.status)
              : String(detail?.detail ?? res.status);
          throw new Error(`retire ${reason}`);
        }
      }
      // No island on screen (the Agents ledger, the office): there is no
      // ceremony to wait for, so the row leaves the rail now.
      if (sample || !retirementStageMounted()) {
        patchRoster(client, (agents) => agents.filter((a) => a.agentId !== agent.agentId));
        if (sample) await client.invalidateQueries({ queryKey: ROSTER_QUERY_KEY });
        return;
      }
      const started = beginRetirement({
        agentId: agent.agentId,
        name: agent.name,
        figure: agent.figure,
        palette: agent.palette,
        executionerId: executioner?.agentId ?? null,
        commit: () => {
          void client.invalidateQueries({ queryKey: ROSTER_QUERY_KEY });
        },
      });
      // Another retirement already has the lead: the row is gone either way,
      // so the roster is refreshed at once rather than waiting for a ceremony
      // that will never run for this agent.
      if (!started) await client.invalidateQueries({ queryKey: ROSTER_QUERY_KEY });
    },
    [client],
  );
}

/** The one lead of the society (MASTERPLAN §2.5) — the agent who carries out a retirement. */
export function findLead(agents: readonly SocietyAgent[]): SocietyAgent | null {
  return agents.find((a) => a.tier === "lead") ?? null;
}
