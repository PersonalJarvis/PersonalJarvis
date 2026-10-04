import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Monitor } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";

import type { SocietyAgent } from "../data";

interface ScreenLease {
  screen_id: string;
  kind: string;
  owner: string;
  purpose: string;
  isolated: boolean;
  hidden: boolean;
}

interface ScreenStatus {
  available: boolean;
  blocked_reason: string | null;
  active: ScreenLease | null;
}

async function screenRequest(
  agentId: string,
  method: "GET" | "POST" | "DELETE",
  purpose = "",
): Promise<{ screen?: ScreenStatus | ScreenLease; closed?: boolean }> {
  const response = await fetch(`/api/society/agents/${encodeURIComponent(agentId)}/screen`, {
    method,
    ...(method === "POST"
      ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify({ purpose }) }
      : {}),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const detail = body?.detail;
    const message = typeof detail === "string"
      ? detail
      : detail && typeof detail === "object" && "detail" in detail
        ? String((detail as { detail?: unknown }).detail ?? "")
        : "";
    throw new Error(message || `HTTP ${response.status}`);
  }
  return response.json() as Promise<{ screen?: ScreenStatus | ScreenLease; closed?: boolean }>;
}

export function AgentScreenControl({ agent, sample = false }: { agent: SocietyAgent; sample?: boolean }) {
  const t = useT();
  const client = useQueryClient();
  const key = ["society", "agent-screen", agent.agentId] as const;
  const status = useQuery({
    queryKey: key,
    enabled: !sample,
    retry: false,
    staleTime: 5_000,
    refetchInterval: import.meta.env.MODE === "test" ? false : 10_000,
    queryFn: async () => {
      const body = await screenRequest(agent.agentId, "GET");
      return body.screen as ScreenStatus;
    },
  });
  const open = useMutation({
    mutationFn: async () => {
      const body = await screenRequest(agent.agentId, "POST", `${agent.name} isolated screen`);
      return body.screen as ScreenLease;
    },
    onSuccess: (active) => client.setQueryData<ScreenStatus>(key, {
      available: true,
      blocked_reason: null,
      active,
    }),
  });
  const close = useMutation({
    mutationFn: () => screenRequest(agent.agentId, "DELETE"),
    onSuccess: () => client.setQueryData<ScreenStatus>(key, (current) => ({
      available: current?.available ?? true,
      blocked_reason: current?.blocked_reason ?? null,
      active: null,
    })),
  });

  if (sample) return null;
  const data = status.data;
  const busy = open.isPending || close.isPending;
  const error = open.error ?? close.error ?? status.error;

  return <section className="shrink-0 rounded-lg border border-border bg-card p-3" data-testid="agent-screen-control" aria-label={t("society.screen.title")}>
    <div className="flex items-start gap-2">
      <Monitor className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">{t("society.screen.title")}</p>
        <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{t("society.screen.hint")}</p>
      </div>
    </div>
    {status.isLoading ? <p className="mt-2 text-xs text-muted-foreground">{t("society.screen.loading")}</p> : null}
    {data?.active ? <div className="mt-3">
      <p className="text-xs text-muted-foreground">
        {t("society.screen.active")}: <span className="font-mono text-foreground">{data.active.kind}</span>
        {data.active.isolated ? ` · ${t("society.screen.isolated")}` : ""}
      </p>
      <Button className="mt-2 w-full" size="sm" variant="secondary" disabled={busy} onClick={() => close.mutate()}>
        {close.isPending ? t("society.screen.closing") : t("society.screen.close")}
      </Button>
    </div> : data?.available ? <Button className="mt-3 w-full" size="sm" variant="secondary" disabled={busy} onClick={() => open.mutate()}>
      {open.isPending ? t("society.screen.opening") : t("society.screen.open")}
    </Button> : data ? <p className="mt-2 text-xs text-muted-foreground" role="status">
      {t("society.screen.unavailable")}{data.blocked_reason ? `: ${data.blocked_reason}` : ""}
    </p> : null}
    {error ? <p className="mt-2 text-xs text-destructive" role="alert">{error.message || t("society.screen.load_error")}</p> : null}
  </section>;
}

export default AgentScreenControl;
