/**
 * React Query Hooks fuer die CLI-Integration.
 *
 * Endpoints (siehe jarvis/ui/web/cli_routes.py).
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

export type CliStatus =
  | "connected"
  | "disconnected"
  | "not_installed"
  | "error"
  | "checking";

export type CliAuthMode = "oauth_cli" | "api_key" | "config_file" | "none";

export interface CliSummary {
  name: string;
  display_name: string;
  category: string;
  icon: string;
  description: string;
  status: CliStatus;
  installed: boolean;
  connected: boolean;
  version: string | null;
  auth_mode: CliAuthMode;
  is_custom: boolean;
  last_used_at: number | null;
  usage_count_7d: number;
  error?: string | null;
}

export interface InstallMethodInfo {
  manager: "winget" | "scoop" | "npm" | "pip" | "cargo" | "script" | "manual";
  command: string;
  requires_admin: boolean;
  notes?: string | null;
}

export interface SecretKeyInfo {
  name: string;
  env_var: string;
  required: boolean;
}

export interface CliDetail extends CliSummary {
  homepage: string;
  binary_name: string;
  binary_path: string | null;
  install_methods: InstallMethodInfo[];
  recommended_install: string | null;
  secret_keys: SecretKeyInfo[];
  secrets_set: Record<string, boolean>;
  login_command: string | null;
  logout_command: string | null;
  status_command: string | null;
  check_command: string;
  tool_schema_examples: string[];
  risk_tier: string;
  allow_patterns: string[];
  deny_patterns: string[];
}

export interface ListClisResponse {
  clis: CliSummary[];
  total: number;
  connected: number;
  installed: number;
  categories: string[];
}

export interface CheckResponse {
  name: string;
  status: CliStatus;
  installed: boolean;
  connected: boolean;
  version: string | null;
  binary_path: string | null;
  error?: string | null;
}

export interface UsageEntry {
  id: number;
  trace_id: string | null;
  full_command: string;
  exit_code: number | null;
  stdout_len: number;
  stderr_len: number;
  stderr_preview: string | null;
  duration_ms: number | null;
  caller: string;
  started_at: number;
  finished_at: number | null;
}

export interface UsageListResponse {
  entries: UsageEntry[];
  total: number;
  page: number;
  page_size: number;
}

export interface UsageStatsResponse {
  total_calls: number;
  success_calls: number;
  success_rate: number;
  avg_duration_ms: number;
  last_used_at: number | null;
  top_commands: Array<[string, number]>;
  calls_by_caller: Record<string, number>;
}

export interface InstallStartResponse {
  ok: boolean;
  job_id: string;
  command: string;
  error?: string | null;
}

export interface ConnectResponse {
  ok: boolean;
  status: CliStatus;
  job_id: string | null;
  error?: string | null;
}

// ---------------------------------------------------------------------------
// CLI Test Hub — POST /api/clis/test-run
// ---------------------------------------------------------------------------

/**
 * Risk tier of a resolved command, mirroring `jarvis/safety/risk_tier.py`.
 * `null` means the backend could not resolve a command (no tool was called).
 */
export type RiskTier = "safe" | "monitor" | "ask" | "block";

export interface TestRunStep {
  tool: string;
  command: string;
  exit_code: number | null;
}

export interface TestRunRequest {
  instruction: string;
  cli_hint?: string;
}

/**
 * Result of a single natural-language CLI test-run.
 *
 * Contract: `docs/superpowers/specs/2026-05-24-cli-integration-design.md`
 * ("Interface contract"). The backend is built concurrently — every field is
 * treated as potentially absent at runtime, so callers must guard nullable
 * values defensively.
 */
export interface TestRunResponse {
  ok: boolean;
  instruction: string;
  tool_called: string | null;
  command: string | null;
  risk_tier: RiskTier | null;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  duration_ms: number | null;
  summary: string;
  error: string | null;
  steps: TestRunStep[];
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${text || res.statusText}`);
  }
  return res.json();
}

async function postJson<T>(
  url: string,
  body?: unknown,
  method: "POST" | "DELETE" = "POST",
): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${text || res.statusText}`);
  }
  return res.json();
}

export function useClisList() {
  return useQuery({
    queryKey: ["clis"],
    queryFn: () => getJson<ListClisResponse>("/api/clis"),
    refetchInterval: 5_000,
  });
}

export function useCliDetail(name: string | null) {
  return useQuery({
    queryKey: ["cli", name],
    queryFn: () => getJson<CliDetail>(`/api/clis/${name}`),
    enabled: Boolean(name),
    staleTime: 30_000,
  });
}

export function useCliUsage(
  name: string | null,
  opts: { page?: number; pageSize?: number; successOnly?: boolean; search?: string } = {},
) {
  const { page = 1, pageSize = 50, successOnly = false, search } = opts;
  const params = new URLSearchParams();
  params.set("page", String(page));
  params.set("page_size", String(pageSize));
  if (successOnly) params.set("success_only", "true");
  if (search) params.set("search", search);
  const queryString = params.toString();
  return useQuery({
    queryKey: ["cli-usage", name, page, pageSize, successOnly, search],
    queryFn: () =>
      getJson<UsageListResponse>(`/api/clis/${name}/usage?${queryString}`),
    enabled: Boolean(name),
    staleTime: 10_000,
  });
}

export function useCliStats(name: string | null) {
  return useQuery({
    queryKey: ["cli-usage-stats", name],
    queryFn: () => getJson<UsageStatsResponse>(`/api/clis/${name}/usage/stats`),
    enabled: Boolean(name),
    staleTime: 30_000,
  });
}

export function useCheckCli() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      postJson<CheckResponse>(`/api/clis/${name}/check`),
    onSuccess: (_res, name) => {
      qc.invalidateQueries({ queryKey: ["clis"] });
      qc.invalidateQueries({ queryKey: ["cli", name] });
    },
  });
}

export function useInstallCli() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, method }: { name: string; method: string }) =>
      postJson<InstallStartResponse>(`/api/clis/${name}/install`, { method }),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ["clis"] });
      qc.invalidateQueries({ queryKey: ["cli", vars.name] });
    },
  });
}

export interface SpawnExternalResponse {
  ok: boolean;
  method: string; // "wt" | "pwsh" | "powershell" | "failed"
  command: string | null;
  error: string | null;
}

export function useSpawnExternalTerminal() {
  return useMutation({
    mutationFn: async ({
      name,
      kind,
      method,
    }: {
      name: string;
      kind: "install" | "login";
      method?: string;
    }) => {
      const r = await fetch(`/api/clis/${name}/spawn-external`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, method: method ?? null }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      return (await r.json()) as SpawnExternalResponse;
    },
  });
}

export function useConnectCli() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      mode,
      secrets,
    }: {
      name: string;
      mode: "oauth_cli" | "api_key";
      secrets?: Record<string, string>;
    }) =>
      postJson<ConnectResponse>(`/api/clis/${name}/connect`, {
        mode,
        secrets: secrets ?? null,
        validate_creds: true,
      }),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ["clis"] });
      qc.invalidateQueries({ queryKey: ["cli", vars.name] });
    },
  });
}

export function useDisconnectCli() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      postJson<{ ok: boolean; error?: string }>(
        `/api/clis/${name}/disconnect`,
      ),
    onSuccess: (_res, name) => {
      qc.invalidateQueries({ queryKey: ["clis"] });
      qc.invalidateQueries({ queryKey: ["cli", name] });
    },
  });
}

export function useRegisterCustomCli() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      postJson<CliDetail>("/api/clis/custom", payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["clis"] });
    },
  });
}

export function useDeleteCustomCli() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      postJson<{ ok: boolean }>(`/api/clis/custom/${name}`, undefined, "DELETE"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["clis"] });
    },
  });
}

export function useClearUsage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      postJson<{ deleted: number }>(
        `/api/clis/${name}/usage`,
        undefined,
        "DELETE",
      ),
    onSuccess: (_res, name) => {
      qc.invalidateQueries({ queryKey: ["cli-usage", name] });
      qc.invalidateQueries({ queryKey: ["cli-usage-stats", name] });
    },
  });
}

/**
 * Drive the CLI Test Hub: send a natural-language instruction to
 * `POST /api/clis/test-run`, let Jarvis pick a `cli_<name>` tool, run a real
 * command through the safety gate, and return the structured result.
 *
 * On success we invalidate the connected-CLI list and the usage caches so a
 * fresh run shows up immediately in the CLIs view (the run records usage).
 */
export function useCliTestRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: TestRunRequest) =>
      postJson<TestRunResponse>("/api/clis/test-run", {
        instruction: req.instruction,
        // Omit the hint entirely when blank so the backend stays free to pick.
        ...(req.cli_hint ? { cli_hint: req.cli_hint } : {}),
      }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["clis"] });
      if (res.tool_called) {
        // tool_called is "cli_<name>" — strip the prefix for the usage cache key.
        const cliName = res.tool_called.replace(/^cli_/, "");
        qc.invalidateQueries({ queryKey: ["cli-usage", cliName] });
        qc.invalidateQueries({ queryKey: ["cli-usage-stats", cliName] });
      }
    },
  });
}
