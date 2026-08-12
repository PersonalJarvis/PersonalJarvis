/**
 * Typed client for the Feedback REST API (jarvis/ui/web/feedback_routes.py).
 * Plain fetch — mirrors the pattern in views/socials/api.ts.
 */

export type FeedbackType = "bug" | "idea" | "question";

export interface FeedbackPayload {
  type: FeedbackType;
  title: string;
  description: string;
  screenshot?: string | null;
}

export type FeedbackStatus = "sent" | "not_configured" | "discord_error" | "unreachable";

export interface FeedbackResult {
  ok: boolean;
  status: FeedbackStatus;
  detail: string;
  // Populated only for status === "not_configured": a public GitHub issues
  // URL the caller can render as a "report it on GitHub" fallback.
  github_url?: string | null;
}

/** System fields the server would attach to a dispatched report. */
export interface FeedbackContext {
  app_version: string;
  os: string;
  python: string;
}

/**
 * Capability probe (GET /api/feedback/status). `configured: false` is the
 * default on every fresh install — the operator webhook is the maintainer's
 * own credential — and the form then routes to a prefilled GitHub issue.
 */
export interface FeedbackChannelStatus {
  configured: boolean;
  github_url: string;
  context: FeedbackContext;
}

const BASE = "/api/feedback";

export async function fetchFeedbackStatus(): Promise<FeedbackChannelStatus> {
  const res = await fetch(`${BASE}/status`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as FeedbackChannelStatus;
}

export async function submitFeedback(payload: FeedbackPayload): Promise<FeedbackResult> {
  const res = await fetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // Prevent WebView2 from serving a cached response for a write endpoint.
    cache: "no-store",
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON body — keep the status line */
    }
    throw new Error(detail);
  }
  return (await res.json()) as FeedbackResult;
}
