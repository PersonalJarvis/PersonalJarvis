/**
 * Thin client for the setup assistant's routes
 * (`jarvis/ui/web/local_models_assistant_routes.py`, prefix
 * `/api/providers/{id}/local-models/assistant`). Shapes mirror the backend
 * one-to-one; nothing here decides behaviour. A backend that predates the
 * assistant answers 404 — callers show that honestly instead of a spinner.
 */
import type { AgentChatSurface } from "@/lib/agentChatApi";

export type AssistantMode = "setup" | "diagnose" | "test";

export interface AssistantRunResponse {
  session_id: string;
  turn_id: string;
  surface: AgentChatSurface;
}

export interface AssistantSessionResponse {
  session_id: string | null;
  provider: string;
  model: string;
  ready: boolean;
  reason: string;
}

export type AssistantHealthStatus = "ok" | "error" | "needs_setup" | "unknown";

export interface AssistantHealth {
  status: AssistantHealthStatus;
  reason: string;
  /** ISO timestamp or epoch seconds; null when the monitor has not run. */
  since: string | number | null;
  last_ok: string | number | null;
  checked_at: string | number | null;
}

export interface AssistantRoleResult {
  model: string;
  status: string;
  latency_ms: number | null;
  detail: string;
}

export interface AssistantTestReport {
  checked_at: string | number | null;
  server: Record<string, unknown>;
  roles: Record<string, AssistantRoleResult>;
  voice: Record<string, unknown> | null;
  overall: string;
}

export class AssistantApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "AssistantApiError";
  }
}

function base(providerId: string): string {
  return `/api/providers/${encodeURIComponent(providerId)}/local-models/assistant`;
}

async function parse<T>(res: Response, what: string): Promise<T> {
  if (!res.ok) {
    let detail = what;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.trim()) detail = body.detail;
    } catch {
      // no JSON body — the generic label stands
    }
    throw new AssistantApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

export async function runAssistant(
  providerId: string,
  mode: AssistantMode,
): Promise<AssistantRunResponse> {
  return parse(
    await fetch(`${base(providerId)}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    }),
    "assistant-run-failed",
  );
}

export async function getAssistantSession(providerId: string): Promise<AssistantSessionResponse> {
  return parse(await fetch(`${base(providerId)}/session`, { cache: "no-store" }), "assistant-session-failed");
}

export async function getAssistantHealth(providerId: string): Promise<AssistantHealth> {
  return parse(await fetch(`${base(providerId)}/health`, { cache: "no-store" }), "assistant-health-failed");
}

export async function runAssistantTest(providerId: string): Promise<AssistantTestReport> {
  return parse(await fetch(`${base(providerId)}/test`, { method: "POST" }), "assistant-test-failed");
}

/** Turns the backend's `since` / `checked_at` (ISO or epoch seconds) into a Date, or null. */
export function assistantTimestamp(value: string | number | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const d = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}
