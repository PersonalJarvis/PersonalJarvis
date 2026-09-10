/** Secret-free twins of the machine protocol; credentials are used only by setup forms. */
export const MACHINE_JOB_STATES = [
  "queued",
  "running",
  "succeeded",
  "failed",
  "uncertain",
  "cancelled",
] as const;
export interface Machine {
  id: string;
  name: string;
  transport: "ssh" | "connector";
  online: boolean;
  capabilities: {
    os: "windows" | "macos" | "linux";
    shell: boolean;
    files: boolean;
    agent_runtime: boolean;
    desktop: boolean;
    isolated_desktop: boolean;
    workspace_isolation: boolean;
    shell_name: string;
    reason: string;
  };
}
export interface MachineGrant {
  agent_id: string;
  machine_id: string;
  workspace: string;
  scope: "workspace" | "account";
  shell: boolean;
  files: boolean;
  desktop: "none" | "own" | "attached";
}
export interface MachineJob {
  id: string;
  agent_id: string;
  machine_id: string;
  trace_id: string;
  state: (typeof MACHINE_JOB_STATES)[number];
  result: string | null;
  created: number;
}
export async function machineRequest<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/api/machines${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const value = (await response.json().catch(() => ({}))) as {
      detail?: unknown;
    };
    throw new Error(
      typeof value.detail === "string"
        ? value.detail
        : `Request failed (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}
