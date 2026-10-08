import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Cloud, CloudUpload, Loader2, RefreshCw } from "lucide-react";
import { useComputers } from "@/hooks/useComputers";
import { useLocaleChunk, useT } from "@/i18n";
import { useEventStore } from "@/store/events";

interface Placement {
  agent_id: string;
  computer_id: string;
  state: "preparing" | "ready" | "activating" | "active" | "uncertain";
  detail?: string;
}

async function cloudRequest(path: string, computerId?: string, checkStatus = false): Promise<Placement | null> {
  const response = await fetch(path, computerId || checkStatus ? {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(computerId ? { computer_id: computerId } : {}),
  } : undefined);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Cloud request failed (${response.status})`);
  return body.placement ?? null;
}

/** Hosting moves the owner runtime, while the existing Runs on picker only moves tools. */
export function AgentCloudControl({ agentId }: { agentId: string }) {
  const ready = useLocaleChunk("agent_cloud");
  const t = useT();
  const computers = useComputers();
  const qc = useQueryClient();
  const setSection = useEventStore((state) => state.setActiveSection);
  const [expanded, setExpanded] = useState(false);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const endpoint = `/api/society-cloud/agents/${encodeURIComponent(agentId)}`;
  const placement = useQuery({
    queryKey: ["agent-cloud", agentId], queryFn: () => cloudRequest(endpoint), retry: false,
  });
  const targets = (computers.data ?? []).filter((computer) =>
    computer.health.status !== "provisioning" && computer.facts?.os_id !== "windows" && computer.kind !== "local_vm",
  );
  const owner = (computers.data ?? []).find((computer) => computer.id === placement.data?.computer_id);
  const refresh = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      qc.setQueryData(["agent-cloud", agentId], await cloudRequest(`${endpoint}/status`, undefined, true));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };
  const cancelPreparation = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      qc.setQueryData(["agent-cloud", agentId], await cloudRequest(`${endpoint}/cancel`, undefined, true));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };
  const move = async () => {
    if (!selected || busy || placement.data) return;
    setBusy(true);
    setError("");
    try {
      const next = await cloudRequest(`${endpoint}/handoff`, selected);
      qc.setQueryData(["agent-cloud", agentId], next);
      await qc.invalidateQueries({ queryKey: ["society"] });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      // A lost response may still have committed ownership remotely. Read the
      // ledger before offering another move; never retry the mutation.
      await placement.refetch();
    } finally {
      setBusy(false);
    }
  };
  if (!ready) return null;
  return <section className="rounded-lg border border-border p-3" data-testid="agent-cloud-control">
    <button type="button" className="flex w-full items-center gap-2 text-left text-sm font-medium text-foreground"
      aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
      <Cloud className="h-4 w-4 shrink-0" aria-hidden />
      <span>{placement.data ? `${t("agent_cloud.hosted")} · ${owner?.name ?? placement.data.computer_id}` : t("agent_cloud.title")}</span>
    </button>
    {placement.data && <p role="status" className="mt-2 text-xs text-muted-foreground">
      {placement.data.state === "active" ? t("agent_cloud.active") : t("agent_cloud.pending")}
    </p>}
    {expanded && <div className="mt-3 space-y-3 text-xs text-muted-foreground">
      {placement.isPending || computers.isPending ? <p role="status">{t("agent_cloud.loading")}</p> : null}
      {placement.isError || computers.isError ? <div role="alert">
        <p>{t("agent_cloud.load_failed")}</p>
        <button type="button" className="mt-2 flex items-center gap-1 text-foreground" onClick={() => {
          void placement.refetch(); void computers.refetch();
        }}><RefreshCw className="h-3 w-3" aria-hidden />{t("agent_cloud.retry")}</button>
      </div> : !placement.data && <>
        <p>{t("agent_cloud.description")}</p>
        <p>{t("agent_cloud.requirements")}</p>
        <p>{t("agent_cloud.credentials")}</p>
        {targets.length ? <label className="block space-y-1">
          <span>{t("agent_cloud.target")}</span>
          <select aria-label={t("agent_cloud.target")} value={selected} disabled={busy}
            className="w-full rounded-md border border-border bg-background p-2 text-foreground"
            onChange={(event) => setSelected(event.target.value)}>
            <option value="">{t("agent_cloud.choose")}</option>
            {targets.map((computer) => <option key={computer.id} value={computer.id}>{computer.name}</option>)}
          </select>
        </label> : <button type="button" className="text-accent underline" onClick={() => setSection("computers")}>{t("agent_cloud.connect")}</button>}
        <button type="button" disabled={busy || !selected || placement.isPending || computers.isPending}
          className="flex items-center gap-2 rounded-md bg-accent px-3 py-2 font-medium text-accent-foreground disabled:opacity-50"
          onClick={() => void move()}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <CloudUpload className="h-4 w-4" aria-hidden />}
          {t(busy ? "agent_cloud.moving" : "agent_cloud.move")}
        </button>
      </>}
      {placement.data && <button type="button" disabled={busy} className="flex items-center gap-1 text-foreground disabled:opacity-50" onClick={() => void refresh()}>
        <RefreshCw className="h-3 w-3" aria-hidden />{t("agent_cloud.retry")}
      </button>}
      {placement.data && placement.data.state !== "active" && <button type="button" disabled={busy}
        className="text-foreground underline disabled:opacity-50" onClick={() => void cancelPreparation()}>
        {t("agent_cloud.cancel")}
      </button>}
      {error && <p role="alert" className="text-destructive">{error}</p>}
    </div>}
  </section>;
}
