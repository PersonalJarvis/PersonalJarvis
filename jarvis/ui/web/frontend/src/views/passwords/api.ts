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

export async function listLogins(): Promise<LoginSummary[]> {
  const body = await request<{ logins: LoginSummary[] }>("/api/logins");
  return body.logins;
}

export function createLogin(draft: LoginDraft): Promise<LoginSummary> {
  return request<LoginSummary>("/api/logins", {
    method: "POST",
    body: JSON.stringify(draft),
  });
}

/** Partial edit. Omitted fields stay as they are — that is the server contract. */
export function updateLogin(
  serviceId: string,
  patch: Partial<LoginDraft>,
): Promise<LoginSummary> {
  return request<LoginSummary>(`/api/logins/${encodeURIComponent(serviceId)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
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
