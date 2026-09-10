import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocaleChunk, useT } from "@/i18n";

type Source = { kind: string; form_fields?: Record<string, { label: string; kind: string; required: boolean; choices: string[] }> };

export function SourceControls({ taskId, source }: { taskId: string; source: Source }) {
  const t = useT();
  useLocaleChunk("society");
  const cache = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [values, setValues] = useState<Record<string, unknown>>(() => Object.fromEntries(Object.entries(source.form_fields ?? {}).filter(([, field]) => field.kind === "boolean").map(([name]) => [name, false])));
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const listener = ["sse", "kafka", "rabbitmq", "mqtt", "redis", "file"].includes(source.kind);
  const info = useQuery({ queryKey: ["routine-source", taskId], enabled: open && listener, retry: false, refetchInterval: open && listener ? 5000 : false,
    queryFn: async () => { const r = await fetch(`/api/tasks/${taskId}/source-connection`); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); } });
  const label = (key: string) => t(`society.triggers.${key}`);
  const send = async (suffix: string, method: string, body?: unknown) => {
    setBusy(true); setNotice("");
    try {
      const r = await fetch(`/api/tasks/${taskId}/${suffix}`, { method, headers: { "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });
      const result = await r.json();
      if (!r.ok) throw new Error(typeof result.detail === "string" ? result.detail : `HTTP ${r.status}`);
      setNotice(label(result.saved ? "saved" : suffix.endsWith("install") ? "installing" : result.status === "filtered" ? "filtered" : result.status === "duplicate" ? "duplicate" : "accepted")); setCredentials({});
      void cache.invalidateQueries({ queryKey: ["routine-source", taskId] });
    } catch (err) { setNotice(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };
  const button = "rounded border border-border px-2 py-1 text-[11px] hover:bg-secondary disabled:opacity-50";
  const field = "w-full rounded border border-border bg-background p-1 text-[12px] text-foreground";
  if (source.kind === "chat" || source.kind === "mcp" || source.kind === "workflow") return <span className="block text-[11px] text-muted-foreground">{label(`entry.${source.kind}`)}</span>;
  return <div className="mt-2 block space-y-2" data-testid="source-controls">
    <button type="button" className={button} onClick={() => setOpen((v) => !v)}>{label(listener ? "connection" : "input")}</button>
    {open && listener ? <>
      <span className="block text-[11px]">{info.data?.status ? label(`state.${info.data.status}`) : label("loading")}</span>
      {info.data?.detail && <span className="block text-[11px] text-muted-foreground">{info.data.detail}</span>}
      {source.kind !== "file" && <>
        {["username", "password", "token"].map((key) => <label key={key} className="block text-[11px]">{label(key)}<input className={field} type={key === "username" ? "text" : "password"} autoComplete="off" value={credentials[key] ?? ""} onChange={(e) => setCredentials((v) => ({ ...v, [key]: e.target.value }))} placeholder={info.data?.credentials?.[key] ? label("stored") : ""} /></label>)}
        <button type="button" className={button} disabled={busy} onClick={() => void send("source-connection", "PUT", credentials)}>{label("save_connection")}</button>
        {["kafka", "rabbitmq", "mqtt", "redis"].includes(source.kind) && <button type="button" disabled={busy || info.data?.install?.status === "running"} className={button} onClick={() => void send("source-connection/install", "POST")}>{label(info.data?.install?.status === "running" ? "installing" : "install_support")}</button>}
      </>}
    </> : null}
    {open && !listener ? <form onSubmit={(event) => { event.preventDefault(); void send("invoke", "POST", { payload: values }); }} className="space-y-2">
      {Object.entries(source.form_fields ?? {}).map(([name, spec]) => <label className="block text-[11px]" key={name}>{spec.label}
        {spec.kind === "choice" ? <select className={field} required={spec.required} value={String(values[name] ?? "")} onChange={(e) => setValues((v) => ({ ...v, [name]: e.target.value }))}><option value="">—</option>{spec.choices.map((choice) => <option key={choice}>{choice}</option>)}</select> : <input className={field} type={spec.kind === "boolean" ? "checkbox" : spec.kind === "number" ? "number" : "text"} required={spec.required && spec.kind !== "boolean"} onChange={(e) => setValues((v) => ({ ...v, [name]: spec.kind === "boolean" ? e.target.checked : spec.kind === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value }))} />}
      </label>)}
      <button type="submit" className={button} disabled={busy}>{label("run")}</button>
    </form> : null}
    {notice && <span role="status" className="block text-[11px]">{notice}</span>}
    {info.error && <span role="alert" className="block text-[11px] text-destructive">{label("connection_unavailable")}</span>}
  </div>;
}
