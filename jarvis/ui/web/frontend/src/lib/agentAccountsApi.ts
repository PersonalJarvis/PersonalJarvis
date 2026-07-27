// REST client for switching between several subscriptions of one coding CLI.
// Plain same-origin fetch (mirrors agenticIdeApi/workspaceApi), `no-store`
// everywhere because WebView2 will happily serve a stale account list — and a
// stale one here is worse than elsewhere: it would show the wrong plan as the
// active one right after the user switched.

/** Which coding CLI a subscription belongs to. */
export type AccountPlatform = "claude" | "codex";

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
  active_account: string;
  accounts: AgentAccount[];
}

export interface AgentAccountsResponse {
  platforms: AccountPlatformGroup[];
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
