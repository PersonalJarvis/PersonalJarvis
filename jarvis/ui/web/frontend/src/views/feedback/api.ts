/**
 * Typed client for the Feedback REST API (jarvis/ui/web/feedback_routes.py).
 * Plain fetch — mirrors the pattern in views/socials/api.ts.
 */

/**
 * The three kinds of report the section handles. `bug` and `idea` are both
 * first-class GitHub issues on their own issue form; `question` deliberately
 * has no form and is routed to the community instead of the tracker.
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
  /**
   * The same OS collapsed onto one of the bug form's dropdown options. GitHub
   * drops a dropdown prefill that is not an exact option string, so the
   * free-form `os` above cannot serve that field.
   */
  os_choice: string;
}

/**
 * Capability probe (GET /api/feedback/status).
 *
 * `templates` is the primary path: it names the issue form each report type
 * opens, which is what applies the `bug` / `enhancement` label and the title
 * prefix. A type mapped to `null` has no form and must not open the tracker.
 *
 * `configured: false` is the default on every fresh install — the direct
 * dispatch webhook is the maintainer's own operator credential — and that is
 * a normal state, not a fault.
 */
export interface FeedbackChannelStatus {
  configured: boolean;
  github_url: string;
  templates: Partial<Record<FeedbackType, string | null>>;
  context: FeedbackContext;
}

/** One open issue as the board renders it. */
export interface BoardEntry {
  number: number;
  title: string;
  url: string;
  /** 👍 reactions — the tracker's closest thing to a vote count. */
  upvotes: number;
  comments: number;
}

/**
 * Open issues grouped by kind (GET /api/feedback/board). Read from the public
 * GitHub API, so it needs no token and no login.
 *
 * `available: false` means the lists could not be loaded at all — the view
 * hides the board rather than rendering an empty one, which would read as
 * "nobody ever asked for anything".
 */
export interface FeedbackBoard {
  available: boolean;
  ideas: BoardEntry[];
  bugs: BoardEntry[];
  detail: string;
}

const BASE = "/api/feedback";

export async function fetchFeedbackStatus(): Promise<FeedbackChannelStatus> {
  const res = await fetch(`${BASE}/status`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as FeedbackChannelStatus;
}

export async function fetchFeedbackBoard(): Promise<FeedbackBoard> {
  const res = await fetch(`${BASE}/board`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as FeedbackBoard;
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
