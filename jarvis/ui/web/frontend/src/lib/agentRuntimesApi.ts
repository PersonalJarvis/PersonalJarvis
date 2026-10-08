/**
 * `/api/agent-runtimes` (jarvis/ui/web/agent_runtime_routes.py): whether
 * Hermes and OpenClaw are installed and new enough, and their install /
 * update jobs. Docs: docs/agent-runtimes.md.
 */
import type { AgentRuntime } from "./societyApi";

export type ExternalRuntime = Exclude<AgentRuntime, "jarvis">;

export interface AgentRuntimeJob {
  runtime: ExternalRuntime;
  /** `prepare` is Hermes' one-time first start after its install. */
  kind: "install" | "update" | "prepare";
  state: "running" | "done" | "failed";
  started_ms: number;
  finished_ms: number | null;
  exit_code: number | null;
  message: string;
  log_tail: string[];
}

export interface AgentRuntimeStatus {
  runtime: ExternalRuntime;
  label: string;
  installed: boolean;
  version: string;
  minimum_version: string;
  /** Installed and new enough to run a turn. */
  ready: boolean;
  /** Why it is not ready, in plain words ("" when ready). */
  problem: string;
  /** The same as a key the UI translates. */
  problem_kind: "" | "not_installed" | "outdated" | "node" | "no_version";
  install_hint: string;
  /** Ready, but newer than the release Jarvis' canary tested (it still runs).
   *  Absent on an older backend. */
  untested?: boolean;
  job: AgentRuntimeJob | null;
}

export interface AgentRuntimesResponse {
  runtimes: AgentRuntimeStatus[];
  /** Jarvis providers an agent on Hermes or OpenClaw can run on right now
   *  (a saved API key or a local server). */
  supported_providers: string[];
  /** Every provider the runtimes can drive once it is connected. */
  all_providers?: string[];
  /** Providers that run through Jarvis' model gateway on a subscription
   *  (the account still matters there). */
  subscription_providers?: string[];
  /** Usable providers that answer on a Claude login instead of an API key;
   *  Anthropic bills that as extra usage. */
  login_providers?: string[];
  /** Per provider that can pay two ways (Claude: API key or Claude Code
   *  login), the ways that work right now: "api" and/or "subscription". */
  access?: Record<string, string[]>;
  /** Per provider and way of paying, the refusal code of an access that is
   *  connected but refused right now (Claude's login while its Extra Usage
   *  is off: `{"claude-api": {"subscription": "extra_usage_off"}}`). Absent
   *  on an older backend. */
  access_blocked?: Record<string, Record<string, string>>;
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
    throw new Error(typeof body?.detail === "string" ? body.detail : `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export function fetchAgentRuntimes(refresh = false): Promise<AgentRuntimesResponse> {
  return json<AgentRuntimesResponse>(`/api/agent-runtimes${refresh ? "?refresh=true" : ""}`);
}

/** Install or update the runtime when it needs it (`null` when it is ready). */
export async function ensureAgentRuntime(runtime: ExternalRuntime): Promise<AgentRuntimeJob | null> {
  const body = await json<{ job: AgentRuntimeJob | null }>(
    `/api/agent-runtimes/${encodeURIComponent(runtime)}/ensure`,
    { method: "POST" },
  );
  return body.job;
}
