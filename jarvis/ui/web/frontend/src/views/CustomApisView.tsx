import { useRef, useState, type FormEvent, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Cable, Download, Loader2, Plus, Trash2 } from "lucide-react";
import { useT } from "@/i18n";
import { SoftButton } from "@/components/extensions/primitives";
import { actionWithPath, customApiRequest, newApiAction, newApiDefinition,
  type ApiAction, type ApiDefinition, type ApiParameter, type ApiStatus } from "@/lib/customApi";

const fieldClass = "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring disabled:opacity-50";
const queryKey = ["custom-apis"];
const jsonRequest = (method: string, body: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="grid min-w-0 gap-1.5 text-xs font-medium text-muted-foreground">{label}{children}</label>;
}

export function CustomApisView({ onBack }: { onBack: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey, queryFn: () => customApiRequest<ApiStatus[]>(), retry: false });
  const [editing, setEditing] = useState<ApiStatus | null>(null);
  const [running, setRunning] = useState<ApiDefinition | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const upload = useRef<HTMLInputElement>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey });
  const start = (definition: ApiDefinition) => {
    setError(""); setEditing({ definition, has_credential: false, tools_ready: false });
  };
  async function loadTemplate() {
    setBusy(true); setError("");
    try { start({ ...await customApiRequest<ApiDefinition>("/templates/elevenlabs"), enabled: true }); }
    catch (e) { setError(e instanceof Error ? e.message : t("custom_apis.failed")); }
    finally { setBusy(false); }
  }
  async function remove(id: string) {
    setBusy(true); setError("");
    try { await customApiRequest(`/${id}`, { method: "DELETE" }); setDeleting(null); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : t("custom_apis.failed")); }
    finally { setBusy(false); }
  }
  if (editing) return <ApiEditor key={editing.definition.id} initial={editing}
    onCancel={() => setEditing(null)} onSaved={async () => { setEditing(null); await refresh(); }} />;
  if (running) return <ApiRunner definition={running} onBack={() => setRunning(null)} />;
  return <section className="space-y-5" aria-label={t("custom_apis.title")}>
    <SoftButton onClick={onBack}><ArrowLeft className="mr-2 h-4 w-4" />{t("nav.plugins")}</SoftButton>
    <div className="flex items-start gap-4">
      <div className="rounded-xl bg-secondary p-3 text-primary"><Cable className="h-6 w-6" /></div>
      <div><h2 className="text-xl font-semibold text-foreground">{t("custom_apis.title")}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t("custom_apis.subtitle")}</p></div>
    </div>
    <div className="flex flex-wrap gap-2">
      <SoftButton onClick={() => start(newApiDefinition())}><Plus className="mr-2 h-4 w-4" />{t("custom_apis.add")}</SoftButton>
      <SoftButton disabled={busy} onClick={() => void loadTemplate()}>{t("custom_apis.elevenlabs")}</SoftButton>
      <SoftButton onClick={() => upload.current?.click()}>{t("custom_apis.import")}</SoftButton>
      <input ref={upload} className="hidden" type="file" accept="application/json,.json" aria-label={t("custom_apis.import")}
        onChange={async (event) => {
          const file = event.target.files?.[0]; event.target.value = "";
          if (!file) return;
          try {
            if (file.size > 256_000) throw new Error(t("custom_apis.invalid_file"));
            const value = JSON.parse(await file.text()) as ApiDefinition;
            if (!value.name || !value.base_url || !Array.isArray(value.actions) || !value.auth || "credential" in value)
              throw new Error(t("custom_apis.invalid_file"));
            // Import is a draft, never an implicit activation or provider call.
            start(await customApiRequest<ApiDefinition>("/validate", jsonRequest("POST", {
              ...value, id: crypto.randomUUID().replaceAll("-", ""), enabled: false,
            })));
          } catch { setError(t("custom_apis.invalid_file")); }
        }} />
    </div>
    {(error || list.error) && <p role="alert" className="text-sm text-destructive">{error || (list.error as Error).message}
      {list.error && <button className="ml-2 underline" onClick={() => void list.refetch()}>{t("custom_apis.retry")}</button>}</p>}
    {list.isPending ? <p role="status" className="text-sm text-muted-foreground">{t("custom_apis.loading")}</p>
      : list.data?.length === 0 ? <div className="rounded-xl border border-dashed border-border p-8 text-center">
        <h3 className="font-medium text-foreground">{t("custom_apis.empty")}</h3>
        <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">{t("custom_apis.empty_hint")}</p>
      </div> : <div className="space-y-3">{list.data?.map((item) => <article key={item.definition.id} className="rounded-xl border border-border bg-card p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0"><h3 className="font-semibold text-foreground">{item.definition.name}</h3>
            <p className="mt-1 break-all text-xs text-muted-foreground">{item.definition.base_url}</p>
            <p className="mt-2 text-sm text-muted-foreground">{item.definition.description}</p>
            <p className="mt-2 text-xs text-muted-foreground">{item.definition.actions.length} {t("custom_apis.actions")} · {t(!item.definition.enabled ? "custom_apis.disabled" : item.tools_ready ? "custom_apis.ready" : "custom_apis.saved")}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <SoftButton onClick={() => setEditing(item)}>{t("custom_apis.edit")}</SoftButton>
            <SoftButton disabled={!item.tools_ready} onClick={() => setRunning(item.definition)}>{t("custom_apis.run")}</SoftButton>
            <SoftButton onClick={() => {
              const url = URL.createObjectURL(new Blob([JSON.stringify({ ...item.definition, enabled: false }, null, 2)], { type: "application/json" }));
              const anchor = document.createElement("a"); anchor.href = url; anchor.download = `api-${item.definition.id}.json`; anchor.click();
              setTimeout(() => URL.revokeObjectURL(url), 1000);
            }}><Download className="mr-1 h-3.5 w-3.5" />{t("custom_apis.export")}</SoftButton>
            <button className="rounded-lg p-2 text-muted-foreground hover:bg-secondary hover:text-destructive" aria-label={`${t("custom_apis.delete")} ${item.definition.name}`} onClick={() => setDeleting(item.definition.id)}><Trash2 className="h-4 w-4" /></button>
          </div>
        </div>
        {deleting === item.definition.id && <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-border pt-3">
          <p className="text-sm text-muted-foreground">{t("custom_apis.delete_hint")}</p>
          <SoftButton disabled={busy} onClick={() => void remove(item.definition.id)}>{t("custom_apis.delete")}</SoftButton>
          <SoftButton disabled={busy} onClick={() => setDeleting(null)}>{t("common.cancel")}</SoftButton>
        </div>}
      </article>)}</div>}
    <p className="text-xs leading-relaxed text-muted-foreground">{t("custom_apis.availability")}</p>
  </section>;
}

function ApiEditor({ initial, onCancel, onSaved }: { initial: ApiStatus; onCancel: () => void; onSaved: () => Promise<void> }) {
  const t = useT();
  const [definition, setDefinition] = useState(initial.definition);
  const [credential, setCredential] = useState("");
  const [schemas, setSchemas] = useState(initial.definition.actions.map((a) => a.body_schema ? JSON.stringify(a.body_schema, null, 2) : ""));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const updateAction = (index: number, action: ApiAction) => setDefinition((d) => ({ ...d, actions: d.actions.map((a, i) => i === index ? action : a) }));
  async function save(event: FormEvent) {
    event.preventDefault(); setError(""); setBusy(true);
    try {
      let actions: ApiAction[];
      try { actions = definition.actions.map((a, i) => ({ ...a, body_schema: schemas[i]?.trim() ? JSON.parse(schemas[i]) : null })); }
      catch { throw new Error(t("custom_apis.invalid_schema")); }
      await customApiRequest(`/${definition.id}`, jsonRequest("PUT", { definition: { ...definition, actions }, ...(credential ? { credential } : {}) }));
      setCredential(""); await onSaved();
    } catch (e) { setError(e instanceof Error ? e.message : t("custom_apis.failed")); }
    finally { setBusy(false); }
  }
  return <form onSubmit={(event) => void save(event)} className="space-y-5">
    <div className="flex items-center justify-between gap-3"><h2 className="text-xl font-semibold text-foreground">{t("custom_apis.configure")}</h2>
      <SoftButton disabled={busy} onClick={onCancel}>{t("common.cancel")}</SoftButton></div>
    <fieldset disabled={busy} className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("custom_apis.name")}><input required maxLength={100} className={fieldClass} value={definition.name} onChange={(e) => setDefinition({ ...definition, name: e.target.value })} /></Field>
        <Field label={t("custom_apis.url")}><input required type="url" placeholder="https://api.example.com" className={fieldClass} value={definition.base_url} onChange={(e) => setDefinition({ ...definition, base_url: e.target.value })} /></Field>
      </div>
      <Field label={t("custom_apis.description")}><input className={fieldClass} maxLength={2000} value={definition.description} onChange={(e) => setDefinition({ ...definition, description: e.target.value })} /></Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("custom_apis.auth")}><select className={fieldClass} value={definition.auth.mode} onChange={(e) => setDefinition({ ...definition, auth: { ...definition.auth, mode: e.target.value as ApiDefinition["auth"]["mode"] } })}>
          <option value="bearer">Bearer token</option><option value="header">{t("custom_apis.header_auth")}</option><option value="none">{t("custom_apis.no_auth")}</option>
        </select></Field>
        {definition.auth.mode === "header" && <Field label={t("custom_apis.header")}><input required className={fieldClass} value={definition.auth.header_name} onChange={(e) => setDefinition({ ...definition, auth: { ...definition.auth, header_name: e.target.value } })} /></Field>}
        {definition.auth.mode !== "none" && <Field label={t("custom_apis.key")}><input type="password" autoComplete="new-password" className={fieldClass} value={credential} placeholder={initial.has_credential ? t("custom_apis.key_saved") : ""} onChange={(e) => setCredential(e.target.value)} /></Field>}
      </div>
      <p className="text-xs text-muted-foreground">{t("custom_apis.key_hint")}</p>
      <label className="flex items-center gap-2 text-sm text-foreground"><input type="checkbox" checked={definition.enabled} onChange={(e) => setDefinition({ ...definition, enabled: e.target.checked })} />{t("custom_apis.enable")}</label>
      <div className="space-y-4">{definition.actions.map((action, index) => <section key={index} className="space-y-4 rounded-xl border border-border bg-card p-4">
        <div className="flex items-center justify-between"><h3 className="font-medium text-foreground">{t("custom_apis.action")} {index + 1}</h3>
          <button type="button" disabled={definition.actions.length === 1} aria-label={`${t("custom_apis.remove_action")} ${index + 1}`} className="p-1 text-muted-foreground hover:text-destructive disabled:opacity-30" onClick={() => {
            setDefinition({ ...definition, actions: definition.actions.filter((_, i) => i !== index) }); setSchemas(schemas.filter((_, i) => i !== index));
          }}><Trash2 className="h-4 w-4" /></button></div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("custom_apis.action_id")}><input required pattern="[a-z][a-z0-9_]{0,23}" className={fieldClass} value={action.id} onChange={(e) => updateAction(index, { ...action, id: e.target.value })} /></Field>
          <Field label={t("custom_apis.action_description")}><input required className={fieldClass} value={action.description} onChange={(e) => updateAction(index, { ...action, description: e.target.value })} /></Field>
          <Field label={t("custom_apis.method")}><select className={fieldClass} value={action.method} onChange={(e) => {
            const method = e.target.value as ApiAction["method"]; updateAction(index, { ...action, method });
            if (method === "GET") setSchemas(schemas.map((s, i) => i === index ? "" : s));
          }}>{["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <option key={m}>{m}</option>)}</select></Field>
          <Field label={t("custom_apis.path")}><input required placeholder="/v1/items/{item_id}" className={fieldClass} value={action.path} onChange={(e) => updateAction(index, actionWithPath(action, e.target.value))} /></Field>
        </div>
        {action.parameters.map((parameter, pi) => <div key={pi} className="flex flex-wrap items-end gap-2">
          <span className="pb-2 text-xs text-muted-foreground">{parameter.location}</span>
          <div className="min-w-0 flex-1"><Field label={t("custom_apis.parameter")}><input required disabled={parameter.location === "path"} className={fieldClass} value={parameter.name} onChange={(e) => updateAction(index, { ...action, parameters: action.parameters.map((p, i) => i === pi ? { ...p, name: e.target.value } : p) })} /></Field></div>
          <select aria-label={t("custom_apis.type")} className={`${fieldClass} !w-auto`} value={parameter.type} onChange={(e) => updateAction(index, { ...action, parameters: action.parameters.map((p, i) => i === pi ? { ...p, type: e.target.value as ApiParameter["type"] } : p) })}>
            {["string", "integer", "number", "boolean"].map((value) => <option key={value}>{value}</option>)}</select>
          {parameter.location === "query" && <><label className="pb-2 text-xs text-muted-foreground"><input type="checkbox" checked={parameter.required} onChange={(e) => updateAction(index, { ...action, parameters: action.parameters.map((p, i) => i === pi ? { ...p, required: e.target.checked } : p) })} /> {t("custom_apis.required")}</label>
            <button type="button" aria-label={t("custom_apis.remove_parameter")} className="p-2 text-muted-foreground" onClick={() => updateAction(index, { ...action, parameters: action.parameters.filter((_, i) => i !== pi) })}><Trash2 className="h-4 w-4" /></button></>}
        </div>)}
        <SoftButton onClick={() => updateAction(index, { ...action, parameters: [...action.parameters, { name: "", location: "query", type: "string", required: false, description: "" }] })}>{t("custom_apis.add_query")}</SoftButton>
        {action.method !== "GET" && <Field label={t("custom_apis.body")}><textarea rows={5} spellCheck={false} className={`${fieldClass} font-mono text-xs`} placeholder={'{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}'} value={schemas[index]} onChange={(e) => setSchemas(schemas.map((s, i) => i === index ? e.target.value : s))} /></Field>}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("custom_apis.response")}><select className={fieldClass} value={action.response} onChange={(e) => updateAction(index, { ...action, response: e.target.value as ApiAction["response"] })}>{["auto", "json", "text", "file"].map((v) => <option key={v} value={v}>{t(`custom_apis.response_${v}`)}</option>)}</select></Field>
          <Field label={t("custom_apis.permission")}><select className={fieldClass} value={action.risk_tier} onChange={(e) => updateAction(index, { ...action, risk_tier: e.target.value as ApiAction["risk_tier"] })}>{["monitor", "ask", "block"].map((v) => <option key={v} value={v}>{t(`custom_apis.permission_${v}`)}</option>)}</select></Field>
        </div>
      </section>)}</div>
      <SoftButton onClick={() => {
        let index = definition.actions.length + 1;
        while (definition.actions.some((a) => a.id === `action_${index}`)) index++;
        setDefinition({ ...definition, actions: [...definition.actions, newApiAction(index)] }); setSchemas([...schemas, ""]);
      }}>{t("custom_apis.add_action")}</SoftButton>
    </fieldset>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <button type="submit" disabled={busy} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">{busy && <Loader2 className="h-4 w-4 animate-spin" />}{t("custom_apis.save")}</button>
  </form>;
}

function ApiRunner({ definition, onBack }: { definition: ApiDefinition; onBack: () => void }) {
  const t = useT();
  const [action, setAction] = useState(definition.actions[0].id);
  const [argumentsText, setArgumentsText] = useState("{}");
  const [result, setResult] = useState("");
  const [downloadUrl, setDownloadUrl] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return <section className="space-y-4">
    <SoftButton disabled={busy} onClick={onBack}><ArrowLeft className="mr-2 h-4 w-4" />{t("custom_apis.title")}</SoftButton>
    <h2 className="text-xl font-semibold text-foreground">{definition.name}</h2>
    <p className="text-sm text-muted-foreground">{t("custom_apis.run_hint")}</p>
    <Field label={t("custom_apis.action")}><select disabled={busy} className={fieldClass} value={action} onChange={(e) => { setAction(e.target.value); setResult(""); setError(""); setDownloadUrl(""); }}>{definition.actions.map((a) => <option key={a.id} value={a.id}>{a.method} {a.description}</option>)}</select></Field>
    <Field label={t("custom_apis.arguments")}><textarea disabled={busy} className={`${fieldClass} font-mono`} rows={8} value={argumentsText} onChange={(e) => setArgumentsText(e.target.value)} /></Field>
    <SoftButton disabled={busy} onClick={async () => {
      setBusy(true); setError(""); setResult(""); setDownloadUrl("");
      try {
        let args: unknown;
        try { args = JSON.parse(argumentsText); } catch { throw new Error(t("custom_apis.invalid_arguments")); }
        const value = await customApiRequest<{ success: boolean; output: unknown; error?: string; artifacts?: string[] }>(`/${definition.id}/actions/${action}/run`, jsonRequest("POST", { arguments: args }));
        if (!value.success) throw new Error(value.error || t("custom_apis.failed"));
        const output = value.output as { download_url?: unknown } | null;
        if (value.artifacts?.length && output && typeof output.download_url === "string"
          && /^\/api\/outputs\/[a-zA-Z0-9_-]+\/files\/tasks\/api\/artifacts\/files\/[a-z0-9_]+\.[a-z0-9]+\/download$/.test(output.download_url)) setDownloadUrl(output.download_url);
        setResult(JSON.stringify(value.output, null, 2));
      } catch (e) { setError(e instanceof Error ? e.message : t("custom_apis.failed")); }
      finally { setBusy(false); }
    }}>{busy ? t("custom_apis.running") : t("custom_apis.run")}</SoftButton>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {downloadUrl && <a className="inline-flex items-center gap-2 text-sm text-primary underline" href={downloadUrl} download><Download className="h-4 w-4" />{t("custom_apis.download")}</a>}
    {result && <pre role="status" className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-secondary p-4 text-xs text-foreground">{result}</pre>}
  </section>;
}
