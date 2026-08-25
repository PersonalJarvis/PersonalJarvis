// Thin client for the agent-chat REST + WebSocket API
// (jarvis/ui/web/agent_chat_routes.py). Shapes mirror jarvis/agent_chat/*
// one-to-one; nothing here decides behaviour.

export interface CuratedModel {
  id: string;
  label: string;
  /**
   * The effort levels this model takes when narrower than the provider's
   * ladder (agy's Pro: low/high; its Claude models: none). Absent = the
   * provider ladder applies; [] = no effort pick for this model.
   */
  efforts?: string[];
  /** A short note for the picker's hint ("retires 2026-08-31"). */
  note?: string;
}

export interface PermissionModeOption {
  id: string;
  label: string;
  description: string;
}

export interface AgentChatProvider {
  id: string;
  label: string;
  /** Brand family for ProviderLogo. */
  family: string;
  /** "api" | "claude-cli" | "codex-cli" | "agy-cli" | "grok-cli" — resolved for this machine. */
  runner: string;
  models_source: "live" | "curated";
  curated_models: CuratedModel[];
  /** "" = the runner's own default. */
  default_model: string;
  keyless: boolean;
  native_resume: boolean;
  /** Ascending ladder; "" means "provider default". */
  effort_levels: string[];
  default_effort: string;
  /** The ladder this provider's runner offers; a "plan" entry powers the Build | Plan switch. */
  permission_modes: PermissionModeOption[];
  default_permission_mode: string;
  /** null for the API runner; else whether the vendor binary is on PATH. */
  cli_installed: boolean | null;
}

export interface AgentChatCatalog {
  providers: AgentChatProvider[];
  default_cwd: string;
  shell: string;
}

/**
 * Where a session lives: the front page's typed chat (`jarvis` — the same
 * assistant as the microphone, on a keyboard) or a coding session listed by
 * the Agentic IDE (`agent`). Each surface asks the backend for its own list
 * and catalog, so they never mix in a sidebar. Mirrors `SURFACES` in
 * `jarvis/agent_chat/store.py` (a parity test reads this union).
 */
export type AgentChatSurface = "jarvis" | "agent";

export interface AgentChatSession {
  session_id: string;
  title: string;
  provider: string;
  model: string;
  effort: string;
  cwd: string;
  permission_mode: string;
  /** Absent on a backend older than the surface split; the store keeps such rows. */
  surface?: AgentChatSurface;
  vendor_session: string | null;
  created_ms: number;
  updated_ms: number;
  message_count: number;
  preview: string;
  running?: boolean;
  pending_approvals?: string[];
}

export interface AgentChatEvent {
  seq: number;
  ts_ms: number;
  kind: string;
  payload: Record<string, unknown>;
}

/** One row of `GET /api/jarvis-agent/status` — the Agents tab's truth about credentials. */
export interface AgentConnectionRow {
  jarvis: string;
  label?: string;
  key_set: boolean;
  api_key_set?: boolean;
  oauth_connected?: boolean;
  is_active_brain: boolean;
  keyless?: boolean;
}

export class AgentChatApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "AgentChatApiError";
  }
}

async function json<T>(res: Response, what: string): Promise<T> {
  if (!res.ok) {
    let detail = what;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* no JSON body — keep the generic label */
    }
    throw new AgentChatApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

export async function fetchAgentChatCatalog(surface?: AgentChatSurface): Promise<AgentChatCatalog> {
  const query = surface ? `?surface=${encodeURIComponent(surface)}` : "";
  return json(await fetch(`/api/agent-chat/catalog${query}`), "catalog-failed");
}

export async function fetchAgentConnections(): Promise<AgentConnectionRow[]> {
  const data = await json<{ mapping?: AgentConnectionRow[] }>(
    await fetch("/api/jarvis-agent/status"),
    "status-failed",
  );
  return Array.isArray(data.mapping) ? data.mapping : [];
}

export interface LiveModel {
  id: string;
  label?: string;
  name?: string;
}

/** The provider's live model list (brain catalog route); [] when the route has none. */
export async function fetchProviderModels(providerId: string): Promise<LiveModel[]> {
  const res = await fetch(`/api/providers/${encodeURIComponent(providerId)}/models`);
  if (!res.ok) return [];
  const data = (await res.json()) as { models?: unknown } | unknown[];
  const raw = Array.isArray(data) ? data : Array.isArray(data.models) ? data.models : [];
  return raw
    .map((m) => {
      if (typeof m === "string") return { id: m };
      if (m && typeof m === "object" && typeof (m as LiveModel).id === "string") {
        return m as LiveModel;
      }
      return null;
    })
    .filter((m): m is LiveModel => m !== null);
}

export async function fetchAgentChatSessions(
  limit = 200,
  surface?: AgentChatSurface,
): Promise<AgentChatSession[]> {
  const query = surface ? `&surface=${encodeURIComponent(surface)}` : "";
  const data = await json<{ sessions: AgentChatSession[] }>(
    await fetch(`/api/agent-chat/sessions?limit=${limit}${query}`),
    "sessions-failed",
  );
  return data.sessions;
}

export interface CreateSessionInput {
  provider: string;
  model?: string;
  effort?: string | null;
  cwd?: string | null;
  permission_mode?: string;
  title?: string;
  surface?: AgentChatSurface;
}

export async function createAgentChatSession(input: CreateSessionInput): Promise<AgentChatSession> {
  return json(
    await fetch("/api/agent-chat/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
    "create-failed",
  );
}

export type PatchSessionInput = Partial<
  Pick<AgentChatSession, "title" | "provider" | "model" | "effort" | "cwd" | "permission_mode">
>;

export async function patchAgentChatSession(
  sessionId: string,
  input: PatchSessionInput,
): Promise<AgentChatSession> {
  return json(
    await fetch(`/api/agent-chat/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
    "patch-failed",
  );
}

export async function deleteAgentChatSession(sessionId: string): Promise<void> {
  await json(
    await fetch(`/api/agent-chat/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    }),
    "delete-failed",
  );
}

export async function fetchAgentChatSession(
  sessionId: string,
): Promise<{ session: AgentChatSession; events: AgentChatEvent[] }> {
  return json(
    await fetch(`/api/agent-chat/sessions/${encodeURIComponent(sessionId)}`),
    "session-failed",
  );
}

export async function sendAgentChatMessage(
  sessionId: string,
  text: string,
  attachments: ChatAttachment[] = [],
): Promise<{ turn_id: string }> {
  return json(
    await fetch(`/api/agent-chat/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, attachments }),
    }),
    "send-failed",
  );
}

/**
 * One file going in with the next message, and what was READ from it.
 *
 * `detail` is the whole point: a description written by a model that could see
 * the image, or a document's extracted text. A chat can be answered by a coding
 * CLI or a text-only model, neither of which can open the file itself, so the
 * contents have to travel with the sentence.
 *
 * Structurally the same row the Agentic IDE's terminals hand back
 * (`DropAttachment` in agenticIdeApi) — same backend analysis, same wire shape.
 */
export interface ChatAttachment {
  name: string;
  /** How the agent should refer to the file — `@path` or a quoted path. */
  reference: string;
  kind: "image" | "text" | "pdf" | "other";
  /** The description or extracted text. Empty when neither could be produced. */
  detail: string;
  /** Which layer produced `detail`. */
  described_by: "vision" | "extraction" | "none";
  /** Why `detail` is empty or shortened. Empty on the happy path. */
  note: string;
}

/**
 * Hand dropped, pasted or picked files to the backend and get them READ.
 *
 * Nothing is sent to the agent here — the composer holds the result while the
 * person finishes the sentence, then posts both together.
 *
 * `paths` are real locations the browser managed to supply (an Explorer drag,
 * or the desktop shell resolving the drop); `files` are the bytes for
 * everything with no path at all. Sending both is normal and the backend
 * de-duplicates, because a drag can carry the path AND the bytes.
 */
export async function attachChatFiles(payload: {
  files?: File[];
  paths?: string[];
  sessionId?: string | null;
  cwd?: string;
  provider?: string;
  surface?: AgentChatSurface;
}): Promise<ChatAttachment[]> {
  const form = new FormData();
  for (const file of payload.files ?? []) form.append("files", file, file.name);
  if (payload.paths?.length) form.append("paths", payload.paths.join("\n"));
  if (payload.sessionId) form.append("session_id", payload.sessionId);
  if (payload.cwd) form.append("cwd", payload.cwd);
  if (payload.provider) form.append("provider", payload.provider);
  if (payload.surface) form.append("surface", payload.surface);

  const data = await json<{ attachments?: ChatAttachment[] }>(
    await fetch("/api/agent-chat/attachments", { method: "POST", body: form }),
    "attach-failed",
  );
  return Array.isArray(data.attachments) ? data.attachments : [];
}

export async function cancelAgentChatTurn(sessionId: string): Promise<void> {
  await json(
    await fetch(`/api/agent-chat/sessions/${encodeURIComponent(sessionId)}/cancel`, {
      method: "POST",
    }),
    "cancel-failed",
  );
}

export type ApprovalDecision = "allow" | "allow_always" | "deny";

export async function resolveAgentChatApproval(
  sessionId: string,
  approvalId: string,
  decision: ApprovalDecision,
): Promise<void> {
  await json(
    await fetch(
      `/api/agent-chat/sessions/${encodeURIComponent(sessionId)}/approvals/${encodeURIComponent(approvalId)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      },
    ),
    "approval-failed",
  );
}

export async function pickAgentChatFolder(start?: string): Promise<string | null> {
  const data = await json<{ path: string | null; cancelled: boolean }>(
    await fetch("/api/agent-chat/pick-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start: start ?? null }),
    }),
    "pick-folder-failed",
  );
  return data.cancelled ? null : data.path;
}

export function agentChatSocketUrl(sessionId: string, afterSeq = 0): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const after = afterSeq > 0 ? `?after=${afterSeq}` : "";
  return `${proto}://${window.location.host}/api/agent-chat/sessions/${encodeURIComponent(sessionId)}/ws${after}`;
}
