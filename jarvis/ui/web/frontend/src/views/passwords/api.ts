/**
 * Client for the Passwords section (`/api/logins`).
 *
 * Note what is missing here: there is no "fetch everything including the
 * password" call. The list and save endpoints answer with a summary that has no
 * password field at all, and `revealLogin` is a separate POST that exists only
 * for an explicit click by the person at the screen. Keeping the reveal on its
 * own call is what stops a password from riding along in every list refresh.
 */

export type LoginStatus = "unknown" | "ok" | "rejected";

/**
 * Whose account a record is. "user" = the person at the screen, the assistant
 * acts on their behalf; "agent" = an account the assistant holds in its own
 * name. Mirrors `jarvis.logins.store.CredentialOwner` — the wire strings are
 * the enum values, and the server rejects anything else with a 400.
 */
export type LoginOwner = "user" | "agent";

export interface LoginSummary {
  service_id: string;
  label: string;
  domains: string[];
  username: string;
  notes: string;
  has_password: boolean;
  has_totp: boolean;
  status: LoginStatus;
  created_at: string | null;
  updated_at: string | null;
  last_used_at: string | null;
  owner: LoginOwner;
  kind: string;
  /** Non-secret extra detail (an address, a base URL) — safe to display. */
  fields: Record<string, string>;
  /** NAMES of additional stored secrets. The values never reach this client. */
  secret_names: string[];
}

export interface LoginSecrets {
  service_id: string;
  username: string;
  password: string;
  totp_secret: string | null;
}

export interface LoginDraft {
  label: string;
  domains: string[];
  username: string;
  password: string;
  notes: string;
  totp_secret?: string | null;
  owner?: LoginOwner;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // A non-JSON error body is not worth a second failure — keep the status.
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

/**
 * Fill in the fields an OLDER running server does not send yet. The desktop's
 * Python process does not hot-reload, so a freshly built frontend routinely
 * talks to a server from before `owner`/`kind`/`fields` existed — and every
 * record from that era is the user's own, which is exactly what the backend
 * itself assumes when reading old records.
 */
function normalizeSummary(raw: Partial<LoginSummary> & { service_id: string }): LoginSummary {
  return {
    label: raw.service_id,
    domains: [],
    username: "",
    notes: "",
    has_password: false,
    has_totp: false,
    status: "unknown",
    created_at: null,
    updated_at: null,
    last_used_at: null,
    ...raw,
    owner: raw.owner === "agent" ? "agent" : "user",
    kind: raw.kind || "website",
    fields: raw.fields ?? {},
    secret_names: raw.secret_names ?? [],
  };
}

export async function listLogins(): Promise<LoginSummary[]> {
  const body = await request<{ logins: (Partial<LoginSummary> & { service_id: string })[] }>(
    "/api/logins",
  );
  return body.logins.map(normalizeSummary);
}

export async function createLogin(draft: LoginDraft): Promise<LoginSummary> {
  const created = await request<Partial<LoginSummary> & { service_id: string }>(
    "/api/logins",
    {
      method: "POST",
      body: JSON.stringify(draft),
    },
  );
  return normalizeSummary(created);
}

/** Partial edit. Omitted fields stay as they are — that is the server contract. */
export async function updateLogin(
  serviceId: string,
  patch: Partial<LoginDraft>,
): Promise<LoginSummary> {
  const updated = await request<Partial<LoginSummary> & { service_id: string }>(
    `/api/logins/${encodeURIComponent(serviceId)}`,
    {
      method: "PATCH",
      body: JSON.stringify(patch),
    },
  );
  return normalizeSummary(updated);
}

export function deleteLogin(serviceId: string): Promise<{ removed: boolean }> {
  return request<{ removed: boolean }>(
    `/api/logins/${encodeURIComponent(serviceId)}`,
    { method: "DELETE" },
  );
}

/** POST, never GET — a secret must not end up in a URL. */
export function revealLogin(serviceId: string): Promise<LoginSecrets> {
  return request<LoginSecrets>(
    `/api/logins/${encodeURIComponent(serviceId)}/reveal`,
    { method: "POST" },
  );
}
