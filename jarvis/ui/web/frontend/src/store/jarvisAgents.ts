/**
 * Sub-Agent-Store — Live-Tree aller aktiven Jarvis-Sub-Agents.
 *
 * Wird vom WebSocket-Hook gespeist (9 Event-Typen: OpenClawTaskStarted/Review/
 * Completed, BrainTurnStarted/Completed, ToolCallStarted/Completed, Harness-
 * Dispatched/Completed) und rendert in <SubAgentsView /> als Live-Tabelle.
 */
import { create } from "zustand";

export type NodeKind = "router" | "openclaw" | "harness" | "tool_call";
export type NodeStatus = "running" | "completed" | "failed";

export interface ToolCallEntry {
  trace_id?: string | null;
  tool_name: string;
  args_preview: string;
  started_ns: number;
  status: "running" | "completed" | "failed";
  duration_ms?: number;
  output_preview?: string;
  error?: string | null;
}

export interface SubAgentNode {
  trace_id: string;
  kind: NodeKind;
  name: string;
  status: NodeStatus;
  parent_trace_id: string | null;
  provider?: string | null;
  model?: string | null;
  started_ns: number;
  completed_ns?: number | null;
  duration_ms?: number | null;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  utterance?: string | null;
  context_hints: string[];
  prompts: string[];
  tool_calls: ToolCallEntry[];
  children_trace_ids: string[];
  error?: string | null;
  review_iterations: number;
  depth: number;
  ui_appeared_at: number;
  ui_fade_at?: number;
}

const FADE_OUT_MS = 60_000;

export interface SubAgentTreeSnapshot {
  roots: SubAgentNode[];
  all: Record<string, Omit<SubAgentNode, "ui_appeared_at" | "ui_fade_at"> & {
    ui_appeared_at?: number;
    ui_fade_at?: number;
  }>;
  count: number;
  server_ts_ns: number;
}

function normalizeTraceId(raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length === 0) return null;
  return raw.replace(/-/g, "");
}

function safeString(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function safeNumber(v: unknown, fallback = 0): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function safeStringArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === "string");
}

interface SubAgentStore {
  subAgents: Record<string, SubAgentNode>;
  ingestEvent: (
    eventName: string,
    traceId: string,
    timestampNs: number,
    payload: unknown,
  ) => void;
  nodesList: () => SubAgentNode[];
  getNode: (traceId: string) => SubAgentNode | undefined;
  hydrateSnapshot: (snapshot: SubAgentTreeSnapshot) => void;
  sweepExpired: () => void;
  clear: () => void;
}

export const useSubAgentStore = create<SubAgentStore>((set, get) => ({
  subAgents: {},

  ingestEvent: (eventName, traceIdRaw, timestampNs, payload) => {
    const traceId = normalizeTraceId(traceIdRaw);
    const payloadObj = (payload ?? {}) as Record<string, unknown>;
    const parentTraceId = normalizeTraceId(payloadObj.parent_trace_id);

    set((state) => {
      const nodes = { ...state.subAgents };

      const upsert = (
        tid: string,
        patch: Partial<SubAgentNode> & { kind: NodeKind; name: string },
      ) => {
        const existing = nodes[tid];
        if (existing) {
          nodes[tid] = { ...existing, ...patch };
        } else {
          const base: SubAgentNode = {
            trace_id: tid,
            kind: patch.kind,
            name: patch.name,
            status: "running",
            parent_trace_id: null,
            cost_usd: 0,
            tokens_in: 0,
            tokens_out: 0,
            context_hints: [],
            prompts: [],
            tool_calls: [],
            children_trace_ids: [],
            review_iterations: 0,
            depth: 0,
            started_ns: timestampNs,
            ui_appeared_at: Date.now(),
          };
          nodes[tid] = { ...base, ...patch };
        }
      };

      const linkToParent = (parentTid: string | null, childTid: string) => {
        if (!parentTid) return;
        const parent = nodes[parentTid];
        if (!parent) return;
        if (!parent.children_trace_ids.includes(childTid)) {
          parent.children_trace_ids = [...parent.children_trace_ids, childTid];
        }
      };

      const markCompleted = (
        tid: string,
        success: boolean,
        extras: Partial<SubAgentNode> = {},
      ) => {
        const node = nodes[tid];
        if (!node) return;
        nodes[tid] = {
          ...node,
          status: success ? "completed" : "failed",
          completed_ns: timestampNs,
          ui_fade_at: Date.now() + FADE_OUT_MS,
          ...extras,
        };
      };

      switch (eventName) {
        case "OpenClawTaskStarted": {
          if (!traceId) break;
          const provider = safeString(payloadObj.provider);
          const model = safeString(payloadObj.model);
          upsert(traceId, {
            kind: "openclaw",
            name: `OpenClaw (${model || provider || "unknown"})`,
            status: "running",
            parent_trace_id: parentTraceId,
            provider: provider || null,
            model: model || null,
            utterance: safeString(payloadObj.utterance) || null,
            context_hints: safeStringArray(payloadObj.context_hints),
            depth: safeNumber(payloadObj.depth),
            started_ns: timestampNs,
          });
          linkToParent(parentTraceId, traceId);
          break;
        }
        case "OpenClawReviewTriggered": {
          if (!traceId || !nodes[traceId]) break;
          const iter = safeNumber(payloadObj.iteration);
          nodes[traceId] = {
            ...nodes[traceId],
            review_iterations: Math.max(nodes[traceId].review_iterations, iter),
          };
          break;
        }
        case "OpenClawTaskCompleted": {
          if (!traceId) break;
          const success = Boolean(payloadObj.success);
          const summary = safeString(payloadObj.summary);
          const durationS = safeNumber(payloadObj.duration_s);
          const costUsd = safeNumber(payloadObj.cost_estimate_usd);
          const error = payloadObj.error as string | null | undefined;
          markCompleted(traceId, success, {
            duration_ms: durationS * 1000,
            cost_usd: (nodes[traceId]?.cost_usd ?? 0) + costUsd,
            error: error ?? null,
            prompts: summary
              ? [...(nodes[traceId]?.prompts ?? []), `[summary] ${summary}`]
              : nodes[traceId]?.prompts ?? [],
          });
          break;
        }
        case "BrainTurnStarted": {
          if (!parentTraceId) break;
          const parent = nodes[parentTraceId];
          if (!parent) break;
          const preview = safeString(payloadObj.system_prompt_preview);
          const provider = safeString(payloadObj.provider);
          const model = safeString(payloadObj.model);
          nodes[parentTraceId] = {
            ...parent,
            provider: parent.provider || provider || null,
            model: parent.model || model || null,
            prompts: preview ? [...parent.prompts, preview] : parent.prompts,
          };
          break;
        }
        case "BrainTurnCompleted": {
          // If parent_trace_id is missing we fall back to a "newest running
          // sub_jarvis" heuristic. With multi-spawn (>=2 concurrent nodes)
          // that pick is ambiguous, so we only attribute usage when exactly
          // one running node is unambiguous.
          const running = Object.values(nodes).filter(
            (n) => n.kind === "openclaw" && n.status === "running",
          );
          if (running.length !== 1) break;
          const newest = running[0];
          nodes[newest.trace_id] = {
            ...newest,
            tokens_in: newest.tokens_in + safeNumber(payloadObj.tokens_in),
            tokens_out: newest.tokens_out + safeNumber(payloadObj.tokens_out),
            cost_usd: newest.cost_usd + safeNumber(payloadObj.cost_usd),
          };
          break;
        }
        case "ToolCallStarted": {
          if (!parentTraceId) break;
          const parent = nodes[parentTraceId];
          if (!parent) break;
          const entry: ToolCallEntry = {
            trace_id: traceId ?? null,
            tool_name: safeString(payloadObj.tool_name),
            args_preview: safeString(payloadObj.args_preview),
            started_ns: timestampNs,
            status: "running",
          };
          nodes[parentTraceId] = {
            ...parent,
            tool_calls: [...parent.tool_calls, entry],
          };
          break;
        }
        case "ToolCallCompleted": {
          if (!traceId) break;
          for (const [parentTid, parent] of Object.entries(nodes)) {
            const idx = parent.tool_calls.findIndex(
              (tc) => tc.trace_id === traceId && tc.status === "running",
            );
            if (idx !== -1) {
              const nextCalls = [...parent.tool_calls];
              nextCalls[idx] = {
                ...nextCalls[idx],
                status: payloadObj.success ? "completed" : "failed",
                duration_ms: safeNumber(payloadObj.duration_ms),
                output_preview: safeString(payloadObj.output_preview),
                error: (payloadObj.error as string | null | undefined) ?? null,
              };
              nodes[parentTid] = { ...parent, tool_calls: nextCalls };
              break;
            }
          }
          break;
        }
        case "HarnessDispatched": {
          if (!traceId) break;
          const running = Object.values(nodes).filter(
            (n) => n.kind === "openclaw" && n.status === "running",
          );
          const parent =
            running.length > 0
              ? running.reduce((a, b) => (a.started_ns > b.started_ns ? a : b))
              : null;
          const harnessName = safeString(payloadObj.harness);
          upsert(traceId, {
            kind: "harness",
            name: `Harness (${harnessName || "unknown"})`,
            status: "running",
            parent_trace_id: parent?.trace_id ?? null,
            started_ns: timestampNs,
          });
          linkToParent(parent?.trace_id ?? null, traceId);
          break;
        }
        case "HarnessCompleted": {
          if (!traceId) break;
          const result = payloadObj.result as
            | Record<string, unknown>
            | null
            | undefined;
          const exitCode = result ? safeNumber(result.exit_code, 1) : 1;
          const durationMs = result ? safeNumber(result.duration_ms) : 0;
          markCompleted(traceId, exitCode === 0, {
            duration_ms: durationMs,
          });
          break;
        }
        default:
          break;
      }

      return { subAgents: nodes };
    });
  },

  nodesList: () => Object.values(get().subAgents),
  getNode: (traceId) => get().subAgents[traceId.replace(/-/g, "")],

  hydrateSnapshot: (snapshot) => {
    const now = Date.now();
    set((state) => {
      const hydrated: Record<string, SubAgentNode> = {};
      for (const [tid, raw] of Object.entries(snapshot.all ?? {})) {
        const existing = state.subAgents[tid];
        hydrated[tid] = {
          ...raw,
          ui_appeared_at: existing?.ui_appeared_at ?? raw.ui_appeared_at ?? now,
          ui_fade_at: existing?.ui_fade_at ?? raw.ui_fade_at,
          tool_calls: raw.tool_calls ?? [],
          context_hints: raw.context_hints ?? [],
          prompts: raw.prompts ?? [],
          children_trace_ids: raw.children_trace_ids ?? [],
          cost_usd: raw.cost_usd ?? 0,
          tokens_in: raw.tokens_in ?? 0,
          tokens_out: raw.tokens_out ?? 0,
          review_iterations: raw.review_iterations ?? 0,
          depth: raw.depth ?? 0,
        };
      }
      return { subAgents: hydrated };
    });
  },

  sweepExpired: () => {
    const now = Date.now();
    set((state) => {
      const remaining: Record<string, SubAgentNode> = {};
      for (const [tid, node] of Object.entries(state.subAgents)) {
        if (node.ui_fade_at === undefined || node.ui_fade_at > now) {
          remaining[tid] = node;
        }
      }
      for (const node of Object.values(remaining)) {
        node.children_trace_ids = node.children_trace_ids.filter(
          (cid) => cid in remaining,
        );
      }
      return { subAgents: remaining };
    });
  },

  clear: () => set({ subAgents: {} }),
}));

export const SUB_AGENT_EVENT_NAMES = new Set<string>([
  "OpenClawTaskStarted",
  "OpenClawReviewTriggered",
  "OpenClawTaskCompleted",
  "BrainTurnStarted",
  "BrainTurnCompleted",
  "ToolCallStarted",
  "ToolCallCompleted",
  "HarnessDispatched",
  "HarnessCompleted",
]);
