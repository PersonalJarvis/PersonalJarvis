// REST client for the "Make It Yours" multi-agent workspace launcher.
// Plain fetch, same-origin (no auth header — mirrors chatsApi). `no-store`
// avoids the WebView2 stale-list cache trap. Terminals are embedded (xterm
// panes driven by the workspace PTY WebSocket), so launching just returns the
// per-slot plan — it opens no OS windows.

export interface WorkspaceAgent {
  name: string;
  display_name: string;
  installed: boolean;
  version: string | null;
  install_command: string | null;
  launch_command: string;
}

export interface AgentsResponse {
  cwd: string;
  terminal_available: boolean;
  layout_choices: number[];
  agents: WorkspaceAgent[];
}

export interface Slot {
  index: number;
  agent: string;
  display_name: string;
}

export interface TrustResult {
  agent: string;
  ok: boolean;
  method: string;
  detail: string;
}

export interface LaunchPlan {
  ok: boolean;
  cwd: string;
  slots: Slot[];
  trust: TrustResult[];
  detail: string;
}

export async function fetchWorkspaceAgents(): Promise<AgentsResponse> {
  const res = await fetch("/api/workspace/agents", { cache: "no-store" });
  if (!res.ok) throw new Error(`agents request failed: ${res.status}`);
  return (await res.json()) as AgentsResponse;
}

export async function launchWorkspace(
  layout: number,
  split: Record<string, number>,
): Promise<LaunchPlan> {
  const res = await fetch("/api/workspace/launch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ layout, split }),
  });
  if (!res.ok) throw new Error(await extractDetail(res));
  return (await res.json()) as LaunchPlan;
}

async function extractDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    if (body?.detail) return body.detail;
  } catch {
    /* fall through to status code */
  }
  return `request failed: ${res.status}`;
}
