import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = await response.json();
  if (!response.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail : body.detail?.detail ?? "Sandbox request failed");
  }
  return body as T;
}

export function SandboxChoice({ value, onChange, disabled = false }: {
  value: "local" | "sandbox"; onChange: (value: "local" | "sandbox") => void; disabled?: boolean;
}) {
  const t = useT();
  return <label className="flex flex-col gap-2 text-sm">
    <span className="font-medium">{t("society.sandbox.environment")}</span>
    <select aria-label={t("society.sandbox.environment")} value={value} disabled={disabled}
      onChange={(event) => onChange(event.target.value as "local" | "sandbox")}
      className="rounded-md border border-border bg-background px-3 py-2 text-foreground">
      <option value="local">{t("society.sandbox.local")}</option>
      <option value="sandbox">{t("society.sandbox.code")}</option>
    </select>
    {value === "sandbox" ? <span className="text-xs text-muted-foreground">{t("society.sandbox.scope")}</span> : null}
  </label>;
}

export function AgentSandboxSection({ agent }: { agent: SocietyAgent }) {
  const t = useT();
  const client = useQueryClient();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [path, setPath] = useState(".");
  const enabled = agent.executionEnvironment === "sandbox";
  const base = `/api/society/agents/${encodeURIComponent(agent.agentId)}/sandbox`;
  const ready = useQuery({
    queryKey: ["society", "sandbox", "status"], enabled, retry: false,
    queryFn: () => json<{ available: boolean; needs_image: boolean; message: string }>("/api/society/sandbox"),
  });
  const files = useQuery({
    queryKey: ["society", "sandbox", agent.agentId, path],
    enabled: enabled && ready.data?.available === true, retry: false,
    queryFn: () => json<{ files: { name: string; directory: boolean }[] }>(`${base}/files?path=${encodeURIComponent(path)}`),
  });
  async function perform(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); } catch (exc) { setError(exc instanceof Error ? exc.message : String(exc)); }
    finally { setBusy(false); }
  }
  return <section className="flex flex-col gap-3" data-testid="agent-sandbox">
    <SandboxChoice value={enabled ? "sandbox" : "local"} disabled={busy || agent.runtime !== "jarvis" && !!agent.runtime}
      onChange={(value) => void perform(async () => {
        await json(`/api/society/agents/${encodeURIComponent(agent.agentId)}`, {
          method: "PATCH", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ execution_environment: value }),
        });
        await client.invalidateQueries({ queryKey: ["society", "roster"] });
      })} />
    {enabled ? <>
      <p className="text-xs text-muted-foreground">{t("society.sandbox.storage")}</p>
      {ready.isLoading ? <p role="status">{t("society.sandbox.checking")}</p> : null}
      {ready.data?.message ? <p role="status" className="text-xs">{ready.data.message}</p> : null}
      {ready.data?.needs_image ? <Button disabled={busy} onClick={() => void perform(async () => {
        await json("/api/society/sandbox/prepare", { method: "POST" });
        await ready.refetch();
      })}>{t("society.sandbox.prepare")}</Button> : null}
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={busy} onClick={() => void perform(async () => {
          const result = await ready.refetch();
          if (result.data?.available) await files.refetch();
        })}>{t("society.sandbox.refresh")}</Button>
        {path !== "." ? <Button size="sm" variant="ghost" onClick={() => setPath(".")}>{t("society.sandbox.root")}</Button> : null}
        <span className="truncate text-xs text-muted-foreground">{path}</span>
      </div>
      {files.isLoading ? <p role="status">{t("society.sandbox.loading")}</p> : null}
      {files.data?.files.length === 0 ? <p className="text-xs text-muted-foreground">{t("society.sandbox.empty")}</p> : null}
      <ul className="space-y-1 text-sm">
        {files.data?.files.map((file) => {
          const child = path === "." ? file.name : `${path}/${file.name}`;
          return <li key={file.name} className="break-all">
            {file.directory ? <button className="text-accent underline" onClick={() => setPath(child)}>{file.name}/</button>
              : <a className="text-accent underline" href={`${base}/file?path=${encodeURIComponent(child)}`} download>{file.name}</a>}
          </li>;
        })}
      </ul>
    </> : null}
    {error || ready.error || files.error ? <p role="alert" className="text-xs text-destructive">{error || ready.error?.message || files.error?.message}</p> : null}
  </section>;
}
