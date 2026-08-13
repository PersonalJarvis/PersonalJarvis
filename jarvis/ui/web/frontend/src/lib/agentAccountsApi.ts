// REST client for switching between several subscriptions of one coding CLI.
// Plain same-origin fetch (mirrors agenticIdeApi/workspaceApi), `no-store`
// everywhere because WebView2 will happily serve a stale account list — and a
// stale one here is worse than elsewhere: it would show the wrong plan as the
// active one right after the user switched.

/**
 * Which coding CLI a subscription belongs to — the backend's registry id.
 *
 * Deliberately an open `string` and not a union of the two ids that happened to
 * exist when this was written. Which CLIs can hold several subscriptions is
 * decided by the backend registry and can grow; a closed union here would not
 * merely be out of date, it would make the compiler reject correct code that
 * renders a platform the API actually returned.
 */
export type AccountPlatform = string;

/**
 * One registered subscription.
 *
 * `builtin` marks the CLI's own default login. It is always present, cannot be
 * renamed or removed, and is what a user who never opens this feature uses —
 * so the UI must never offer those actions on it.
 */
export interface AgentAccount {
  id: string;
  platform: AccountPlatform;
  label: string;
  config_dir: string;
  builtin: boolean;
  connected: boolean;
  /** "subscription" | "api_key" | "expired" | "unknown" */
  mode: string;
  message: string;
  email: string | null;
  tier: string | null;
  /**
   * Set when this account is signed in as the SAME identity as another one —
   * two rows, one plan's usage. Absent on older backends, hence optional.
   */
  warning?: string | null;
}

export interface AccountPlatformGroup {
  platform: AccountPlatform;
  /**
   * What to show as the section heading. Sent by the backend so the UI needs no
   * id-to-label map of its own: a second list of product names is a second
   * place for a new CLI to go missing, and it goes missing invisibly — the API
   * answers correctly and the section is simply never drawn. Optional because
   * an older backend does not send it; fall back to the id.
   */
  display_name?: string;
  active_account: string;
  accounts: AgentAccount[];
}

export interface AgentAccountsResponse {
  platforms: AccountPlatformGroup[];
}

/**
 * One plan limit of one subscription, already normalised by the backend.
 *
 * `kind` is a small closed vocabulary the UI translates against — `session`,
 * `weekly`, `weekly_scoped`, `monthly`, `other`. A provider that invents a
 * limit this build has no name for arrives as `other` and is still DRAWN, using
 * `raw_label`: the limit a user is actually being throttled by must never be
 * the one the panel decides to hide.
 */
export interface UsageWindow {
  kind: string;
  /** 0-100, already clamped. */
  percent: number;
  /** "normal" | "warning" | "critical" */
  severity: string;
  /** ISO-8601 UTC, or null when the provider states no reset time. */
  resets_at: string | null;
  window_minutes: number | null;
  /** What a scoped budget is restricted to, e.g. a model name. */
  scope_label: string | null;
  raw_label: string | null;
}

/**
 * How much of one subscription's plan is spent.
 *
 * `source` and `as_of` are not decoration. A reading can come from the provider
 * just now (`live`) or from what the CLI last wrote to disk (`cached`), and a
 * cached weekly figure for an idle seat can be days old. Since this is the
 * number a user picks a subscription on, the UI states which one it is showing
 * rather than letting a stale percentage pass for a live one.
 */
export interface AccountUsage {
  account_id: string;
  platform: AccountPlatform;
  /** "ok" | "signed_out" | "unsupported" | "unavailable" */
  status: string;
  windows: UsageWindow[];
  /** "live" | "cached" */
  source: string;
  /** Epoch SECONDS the numbers were true at. */
  as_of: number | null;
  message: string;
  /** Display-only plan name, e.g. "Max 20x". */
  plan: string | null;
}

export interface AgentUsageResponse {
  accounts: AccountUsage[];
  /** The server's own cache lifetime — the poll interval follows it. */
  ttl_seconds: number;
  generated_at: number;
}

/**
 * Plan usage for every registered subscription, or `null` on a backend that
 * has no usage route at all.
 *
 * The `null` is the interesting part. The app's Python server does not pick up
 * a new route while it is running, so between updating and restarting there is
 * a real window where the new panel talks to an old backend and every call 404s.
 * Treating that as an ERROR put a Refresh button on screen that could not
 * possibly work; treating it as "not available here" lets the panel hide the
 * whole block and look finished rather than broken.
 *
 * `refresh` bypasses the server's short cache; it backs the manual refresh
 * button, so a user who just closed a heavy session can see the new number
 * without waiting out the interval.
 */
export async function fetchAgentUsage(refresh = false): Promise<AgentUsageResponse | null> {
  const res = await fetch(`/api/agent-accounts/usage${refresh ? "?refresh=true" : ""}`, {
    cache: "no-store",
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as AgentUsageResponse;
}

async function detail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    if (body?.detail) return body.detail;
  } catch {
    /* fall through */
  }
  return `request failed: ${res.status}`;
}

async function send<T>(url: string, init: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as T;
}

export async function fetchAgentAccounts(): Promise<AgentAccountsResponse> {
  const res = await fetch("/api/agent-accounts", { cache: "no-store" });
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as AgentAccountsResponse;
}

export function createAgentAccount(
  platform: AccountPlatform,
  label: string,
): Promise<AgentAccountsResponse> {
  return send<AgentAccountsResponse>("/api/agent-accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ platform, label }),
  });
}

/** Point NEW terminals of one CLI at another subscription. Running panes stay put. */
export function setActiveAgentAccount(
  platform: AccountPlatform,
  accountId: string,
): Promise<AgentAccountsResponse> {
  return send<AgentAccountsResponse>("/api/agent-accounts/active", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ platform, account_id: accountId }),
  });
}

/** Start the CLI's own sign-in, pointed at this account's own folder. */
export function loginAgentAccount(accountId: string): Promise<{ message: string }> {
  return send<{ message: string }>(
    `/api/agent-accounts/${encodeURIComponent(accountId)}/login`,
    { method: "POST" },
  );
}

/**
 * One in-app guided sign-in, as the backend reports it.
 *
 * `url` is the point of the whole flow: shown as data so the user can copy it
 * into ANY browser profile or private window — the default browser is usually
 * signed in as the wrong account when a second subscription is being added.
 * `tail` is what the CLI is showing right now, for transparency when a flow
 * asks something unexpected. Nothing here ever carries a credential.
 */
export interface LoginFlowState {
  flow_id: string;
  account_id: string;
  platform: AccountPlatform;
  label: string;
  /** starting | awaiting_input | verifying | success | failed | cancelled */
  status: string;
  url: string | null;
  /** True once the CLI asked for a code — show the input then. */
  code_expected: boolean;
  message: string;
  tail: string;
  finished: boolean;
}

interface LoginFlowEnvelope {
  flow: LoginFlowState;
}

/** Begin the in-app sign-in; any previous flow for this account is cancelled. */
export async function startLoginFlow(accountId: string): Promise<LoginFlowState> {
  const body = await send<LoginFlowEnvelope>(
    `/api/agent-accounts/${encodeURIComponent(accountId)}/login-flow`,
    { method: "POST" },
  );
  return body.flow;
}

export async function getLoginFlow(flowId: string): Promise<LoginFlowState> {
  const body = await send<LoginFlowEnvelope>(
    `/api/agent-accounts/login-flow/${encodeURIComponent(flowId)}`,
    { method: "GET", cache: "no-store" },
  );
  return body.flow;
}

/** Hand the pasted code to the CLI, exactly as typing it into the terminal would. */
export async function submitLoginFlowCode(
  flowId: string,
  code: string,
): Promise<LoginFlowState> {
  const body = await send<LoginFlowEnvelope>(
    `/api/agent-accounts/login-flow/${encodeURIComponent(flowId)}/code`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    },
  );
  return body.flow;
}

export async function cancelLoginFlow(flowId: string): Promise<LoginFlowState> {
  const body = await send<LoginFlowEnvelope>(
    `/api/agent-accounts/login-flow/${encodeURIComponent(flowId)}`,
    { method: "DELETE" },
  );
  return body.flow;
}

export function renameAgentAccount(
  accountId: string,
  label: string,
): Promise<AgentAccountsResponse> {
  return send<AgentAccountsResponse>(
    `/api/agent-accounts/${encodeURIComponent(accountId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label }),
    },
  );
}

/**
 * Forget a subscription. `removeFiles` also erases its stored login, which is
 * why it defaults to false: forgetting is reversible, erasing is not.
 */
export function deleteAgentAccount(
  accountId: string,
  removeFiles = false,
): Promise<AgentAccountsResponse> {
  const qs = removeFiles ? "?remove_files=true" : "";
  return send<AgentAccountsResponse>(
    `/api/agent-accounts/${encodeURIComponent(accountId)}${qs}`,
    { method: "DELETE" },
  );
}

/**
 * The group for one platform, or undefined while the list is still loading.
 *
 * `platforms` is optional-chained as well, and that is not defensive noise: an
 * older backend, a warming one, or a proxy answering with something else hands
 * back an object with no `platforms` at all, and reaching into it threw — which
 * took down the WHOLE section the panel sits in rather than costing one list.
 */
export function groupFor(
  data: AgentAccountsResponse | null,
  platform: AccountPlatform,
): AccountPlatformGroup | undefined {
  return data?.platforms?.find((group) => group.platform === platform);
}
