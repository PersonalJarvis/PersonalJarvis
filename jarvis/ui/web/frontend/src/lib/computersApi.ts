/**
 * Typed client for the Computers REST API (jarvis/ui/web/computers_routes.py).
 *
 * The shapes mirror `jarvis/computers/models.py` field for field; the backend
 * stays the authority. Errors arrive as `{detail: {message, kind}}` and are
 * rethrown as `ComputerApiError` so a view can branch on `kind` ("auth",
 * "host_key_changed", …) without parsing sentences.
 */

export type ComputerKind = "server" | "local_vm";
export type ProviderId = "generic" | "hostinger" | "hetzner" | "digitalocean" | "multipass";
export type CloudProviderId = "hostinger" | "hetzner" | "digitalocean";
export type AuthMethod = "key" | "password";
export type HealthStatus =
  | "unknown"
  | "online"
  | "offline"
  | "auth_failed"
  | "host_key_changed"
  | "provisioning"
  | "stopped"
  | "error";

export interface ComputerFacts {
  hostname: string | null;
  os_id: string | null;
  os_name: string | null;
  kernel: string | null;
  arch: string | null;
  cpu_count: number | null;
  mem_total_mb: number | null;
  disk_total_gb: number | null;
}

export interface ComputerHealth {
  status: HealthStatus;
  checked_at: number | null;
  latency_ms: number | null;
  message: string | null;
  load_1m: number | null;
  mem_used_pct: number | null;
  disk_used_pct: number | null;
  uptime_s: number | null;
}

export interface Computer {
  id: string;
  name: string;
  kind: ComputerKind;
  provider: ProviderId;
  host: string;
  port: number;
  username: string;
  auth: AuthMethod;
  provider_ref: string | null;
  region: string | null;
  plan: string | null;
  host_key: string | null;
  host_fingerprint: string | null;
  created_at: number;
  facts: ComputerFacts | null;
  health: ComputerHealth;
  /** A background job (VM creation) is still running for this record. */
  busy: boolean;
}

export interface Identity {
  public_key: string;
  fingerprint: string;
  algorithm: string;
}

export interface CloudProvider {
  id: CloudProviderId;
  name: string;
  connected: boolean;
  console_url: string;
  setup_hint: string;
  attaches_keys: boolean;
}

export interface CloudServer {
  id: string;
  name: string;
  host: string | null;
  status: string;
  running: boolean;
  os: string | null;
  plan: string | null;
  region: string | null;
  cpus: number | null;
  memory_mb: number | null;
  disk_gb: number | null;
  /** The computer id this server is already connected as, if any. */
  added_as: string | null;
}

export interface LocalInstance {
  name: string;
  state: string;
  ipv4: string | null;
  release: string | null;
}

export interface LocalStatus {
  backend: "multipass";
  available: boolean;
  version: string | null;
  install_url: string;
  install_hint: string;
  images: string[];
  instances: LocalInstance[];
  error: string | null;
}

export interface CommandResult {
  exit_status: number | null;
  stdout: string;
  stderr: string;
  duration_ms: number;
  truncated: boolean;
}

export interface AddServerInput {
  name: string;
  host: string;
  port: number;
  username: string;
  auth: AuthMethod;
  password?: string;
  keep_password?: boolean;
}

export interface LocalVmInput {
  name: string;
  cpus: number;
  memory_gb: number;
  disk_gb: number;
  image: string;
}

export class ComputerApiError extends Error {
  readonly kind: string | null;
  readonly status: number;

  constructor(message: string, status: number, kind: string | null) {
    super(message);
    this.name = "ComputerApiError";
    this.status = status;
    this.kind = kind;
  }
}

const BASE = "/api/computers";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    let kind: string | null = null;
    try {
      const body = (await res.json()) as { detail?: unknown };
      const detail = body.detail;
      if (typeof detail === "string") message = detail;
      else if (detail && typeof detail === "object") {
        const d = detail as { message?: unknown; kind?: unknown };
        if (typeof d.message === "string") message = d.message;
        if (typeof d.kind === "string") kind = d.kind;
      }
    } catch {
      /* non-JSON body: keep the status line */
    }
    throw new ComputerApiError(message, res.status, kind);
  }
  return (await res.json()) as T;
}

const post = (body?: unknown): RequestInit => ({
  method: "POST",
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const computersApi = {
  list: () => request<{ computers: Computer[] }>("").then((r) => r.computers),
  identity: () => request<Identity>("/identity"),
  add: (input: AddServerInput) => request<Computer>("", post(input)),
  checkAll: () => request<{ computers: Computer[] }>("/check-all", post()).then((r) => r.computers),
  check: (id: string) => request<Computer>(`/${encodeURIComponent(id)}/check`, post()),
  update: (id: string, patch: Partial<Pick<Computer, "name" | "host" | "port" | "username">>) =>
    request<Computer>(`/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  remove: (id: string, destroyVm = false) =>
    request<{ removed: boolean }>(
      `/${encodeURIComponent(id)}${destroyVm ? "?destroy_vm=true" : ""}`,
      { method: "DELETE" },
    ),
  run: (id: string, command: string, timeoutS = 60) =>
    request<CommandResult>(`/${encodeURIComponent(id)}/run`, post({ command, timeout_s: timeoutS })),
  installKey: (id: string, password: string) =>
    request<Computer>(`/${encodeURIComponent(id)}/install-key`, post({ password })),
  trustHostKey: (id: string) =>
    request<Computer>(`/${encodeURIComponent(id)}/trust-host-key`, post()),
  power: (id: string, action: "start" | "stop") =>
    request<Computer>(`/${encodeURIComponent(id)}/power`, post({ action })),
  cloudProviders: () =>
    request<{ providers: CloudProvider[] }>("/cloud").then((r) => r.providers),
  saveCloudToken: (provider: CloudProviderId, token: string) =>
    request<{ connected: boolean; servers: CloudServer[] }>(`/cloud/${provider}/token`, {
      method: "PUT",
      body: JSON.stringify({ token }),
    }),
  forgetCloudToken: (provider: CloudProviderId) =>
    request<{ connected: boolean }>(`/cloud/${provider}/token`, { method: "DELETE" }),
  cloudServers: (provider: CloudProviderId) =>
    request<{ servers: CloudServer[] }>(`/cloud/${provider}/servers`).then((r) => r.servers),
  importCloudServer: (
    provider: CloudProviderId,
    body: { server_id: string; name?: string; username?: string; password?: string },
  ) => request<Computer>(`/cloud/${provider}/import`, post(body)),
  localStatus: () => request<LocalStatus>("/local"),
  createLocalVm: (input: LocalVmInput) => request<Computer>("/local", post(input)),
};
