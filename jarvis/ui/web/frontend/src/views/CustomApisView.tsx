import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Check, ChevronRight, KeyRound, Loader2, LockKeyhole, MoreHorizontal, Pause, Play, Plus, Search, Trash2, Users } from "lucide-react";
import { useT } from "@/i18n";
import { ActionMenu, BackLink, IconButton, Panel, PanelHeader, StatusDot } from "@/components/extensions/primitives";
import { ProviderLogo, providerFamily } from "@/components/providers/ProviderLogo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { bundledPluginLogo } from "@/lib/pluginLogos";
import { ApiConnectionError, customApiRequest, type ApiConnection } from "@/lib/customApi";
import "./CustomApisView.css";

const QUERY_KEY = ["api-connections"];
const jsonRequest = (method: string, body: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

function brandFor(name: string): string {
  const value = name.toLowerCase().replace(/\b(api|documentation)\b/g, "").replace(/[^a-z0-9]/g, "");
  return value === "11labs" ? "elevenlabs" : value;
}

/** Original artwork first; the geometric fallback is a Jarvis-owned connection mark. */
export function ConnectionLogo({ name, brand = "", image = "", large = false }: {
  name: string; brand?: string; image?: string; large?: boolean;
}) {
  const id = brand || brandFor(name);
  const size = large ? "!h-14 !w-14 !rounded-xl" : "!h-11 !w-11 !rounded-xl";
  const bundled = bundledPluginLogo(id);
  if (providerFamily(id) === id) return <ProviderLogo providerId={id} label={name} className={size} />;
  if (bundled || image) return <span aria-hidden className={`inline-flex shrink-0 items-center justify-center bg-secondary p-2 ${size}`}>
    <img src={bundled || image} alt="" className="h-full w-full object-contain" />
  </span>;
  const seed = [...name].reduce((hash, char) => ((hash * 31) + char.charCodeAt(0)) >>> 0, 19);
  return <span aria-hidden data-testid="generated-connection-mark" className={`inline-flex shrink-0 items-center justify-center bg-secondary text-muted-foreground ${size}`}>
    <svg viewBox="0 0 32 32" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="11" y="11" width="10" height="10" rx={seed % 2 ? 3 : 1} />
      {[0, 1, 2, 3].map((i) => <g key={i} transform={`rotate(${i * 90} 16 16)`}>
        <path d="M16 11V6" /><circle cx="16" cy="4.5" r={seed & (1 << i) ? 2 : 1} fill={seed & (1 << i) ? "currentColor" : "none"} />
      </g>)}
    </svg>
  </span>;
}

function useConnectionError() {
  const t = useT();
  return (error: unknown): string => {
    if (error instanceof ApiConnectionError) {
      const key = `custom_apis.errors.${error.code}`;
      const translated = t(key);
      return translated === key ? t("custom_apis.failed") : translated;
    }
    return t("custom_apis.failed");
  };
}

export function CustomApisView({ onBack }: { onBack: () => void }) {
  const t = useT();
  const errorText = useConnectionError();
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: QUERY_KEY, queryFn: () => customApiRequest<ApiConnection[]>("/connections"), retry: false });
  const [editor, setEditor] = useState<ApiConnection | "new" | null>(null);
  const [details, setDetails] = useState<ApiConnection | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: QUERY_KEY });
  async function remove(connection: ApiConnection) {
    setBusy(connection.id); setError("");
    try { await customApiRequest(`/${connection.id}`, { method: "DELETE" }); setRemoving(null); await refresh(); }
    catch (e) { setError(errorText(e)); }
    finally { setBusy(null); }
  }
  async function toggle(connection: ApiConnection) {
    setBusy(connection.id); setError("");
    try { await customApiRequest(`/connections/${connection.id}`, jsonRequest("PATCH", { enabled: !connection.enabled })); await refresh(); }
    catch (e) { setError(errorText(e)); }
    finally { setBusy(null); }
  }
  if (details) return <ConnectionDetails connection={details} onBack={() => setDetails(null)} />;
  const isEmpty = list.data?.length === 0;
  const showEditor = editor !== null || isEmpty;
  if (showEditor) return <section className="space-y-6" aria-label={t("custom_apis.title")}>
    <BackLink label={isEmpty ? t("nav.plugins") : t("custom_apis.title")}
      onClick={isEmpty ? onBack : () => setEditor(null)} />
    <ConnectionForm key={typeof editor === "object" && editor ? editor.id : "new"}
      initial={typeof editor === "object" ? editor : null}
      onSaved={async () => { await refresh(); setEditor(null); }} />
  </section>;
  return <section className="custom-api-connections space-y-5" aria-label={t("custom_apis.title")}>
    <BackLink label={t("nav.plugins")} onClick={onBack} />
    <PanelHeader className="custom-api-header" title={t("custom_apis.title")} subtitle={t("custom_apis.connections_subtitle")}
      actions={<Button size="sm" onClick={() => setEditor("new")}><Plus />{t("custom_apis.add")}</Button>} />
    {list.isPending && <div role="status" className="flex items-center gap-2 py-8 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{t("custom_apis.loading")}</div>}
    {(error || list.error) && <p role="alert" className="text-sm text-destructive">{error || errorText(list.error)} <button className="underline" onClick={() => void list.refetch()}>{t("custom_apis.retry")}</button></p>}
    {!!list.data?.length && <Panel><ul className="divide-y divide-border/60" aria-label={t("custom_apis.title")}>
      {list.data.map((connection) => <li key={connection.id} className="px-4 py-4 sm:px-5">
        <div className="custom-api-row">
          <ConnectionLogo name={connection.name} brand={connection.brand_id} image={connection.logo_data} />
          <button className="min-w-0 flex-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setDetails(connection)}>
            <span className="block truncate text-base font-semibold text-foreground-strong">{connection.name}</span>
            <span className="mt-0.5 block truncate text-sm text-muted-foreground">{connection.action_count} {t("custom_apis.capabilities_count")} <span className="px-1.5 text-foreground-faint">·</span> {connection.website.replace(/^https?:\/\//, "")}</span>
          </button>
          <span className="custom-api-status"><StatusDot tone={!connection.enabled ? "off" : connection.status === "verified" ? "ok" : connection.status === "limited" ? "warn" : "off"}
            label={t(!connection.enabled ? "custom_apis.paused" : connection.tools_ready ? `custom_apis.status_${connection.status}` : "custom_apis.saved")} /></span>
          <ActionMenu label={`${t("custom_apis.manage")} ${connection.name}`} actions={[
            { id: "edit", label: t("custom_apis.edit_key"), icon: <KeyRound className="h-4 w-4" />, onSelect: () => setEditor(connection) },
            { id: "pause", label: t(connection.enabled ? "custom_apis.pause" : "custom_apis.resume"), icon: connection.enabled ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />, onSelect: () => void toggle(connection) },
            { id: "remove", label: t("custom_apis.delete"), icon: <Trash2 className="h-4 w-4" />, separatorAbove: true, onSelect: () => setRemoving(connection.id) },
          ]} trigger={({ toggle: openMenu }) => <IconButton label={`${t("custom_apis.manage")} ${connection.name}`} onClick={openMenu} disabled={busy === connection.id}><MoreHorizontal className="h-4 w-4" /></IconButton>} />
        </div>
        {removing === connection.id && <div className="mt-4 flex flex-wrap items-center justify-end gap-2 border-t border-border/60 pt-3">
          <p className="mr-auto text-sm text-muted-foreground">{t("custom_apis.remove_confirm")}</p>
          <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => setRemoving(null)}>{t("common.cancel")}</Button>
          <Button size="sm" variant="destructive" disabled={busy !== null} onClick={() => void remove(connection)}>{t("custom_apis.delete")}</Button>
        </div>}
      </li>)}
    </ul></Panel>}
    <div className="flex gap-2 px-1 text-sm leading-relaxed text-muted-foreground"><Users className="mt-0.5 h-4 w-4 shrink-0" /><p>{t("custom_apis.inheritance")}</p></div>
  </section>;
}

function ConnectionForm({ initial, onSaved }: { initial: ApiConnection | null; onSaved: () => Promise<void> }) {
  const t = useT();
  const errorText = useConnectionError();
  const [id] = useState(() => initial?.id ?? crypto.randomUUID().replaceAll("-", ""));
  const [name, setName] = useState(initial?.name ?? "");
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(null); setBusy(true);
    try {
      await customApiRequest<ApiConnection>("/connect", jsonRequest("POST", { id, name: name.trim(), ...(credential ? { credential } : {}) }));
      setCredential(""); await onSaved();
    } catch (e) { setError(e); }
    finally { setBusy(false); }
  }
  return <div className="mx-auto w-full max-w-md pb-3 pt-1">
    <div className="mb-7 flex items-center gap-4">
      <ConnectionLogo name={name || "Jarvis connection"} brand={initial?.brand_id} image={initial?.logo_data} large />
      <div className="min-w-0"><h2 className="text-xl font-semibold tracking-tight text-foreground-strong">{t(initial ? "custom_apis.edit_key" : "custom_apis.add_title")}</h2>
        <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{t(initial ? "custom_apis.edit_hint" : "custom_apis.simple_hint")}</p></div>
    </div>
    <form onSubmit={(event) => void submit(event)} className="space-y-5" aria-label={t("custom_apis.add_title")}>
      <div className="space-y-2"><label htmlFor="api-service-name" className="text-sm font-medium text-foreground">{t("custom_apis.service_name")}</label>
        <Input id="api-service-name" required readOnly={!!initial} disabled={busy} autoComplete="off" maxLength={100} placeholder={t("custom_apis.name_example")} value={name} onChange={(event) => setName(event.target.value)} className="h-11 rounded-lg bg-secondary/50 text-base" autoFocus={!initial} /></div>
      <div className="space-y-2"><label htmlFor="api-service-key" className="text-sm font-medium text-foreground">{t("custom_apis.key")}</label>
        <Input id="api-service-key" type="password" required={!initial} disabled={busy} autoComplete="new-password" maxLength={8192} placeholder={t(initial?.has_credential ? "custom_apis.key_saved" : "custom_apis.key_placeholder")} value={credential} onChange={(event) => setCredential(event.target.value)} className="h-11 rounded-lg bg-secondary/50 font-mono text-base" /></div>
      {Boolean(error) && <div role="alert" className="rounded-lg bg-destructive/10 px-3 py-2.5 text-sm text-destructive">
        {errorText(error)}
        {error instanceof ApiConnectionError && error.suggestions.length > 0 && <div className="mt-2 flex flex-wrap gap-2">{error.suggestions.map((suggestion) => <button key={suggestion} type="button" className="rounded-md bg-background px-2 py-1 text-foreground" onClick={() => { setName(suggestion); setError(null); }}>{suggestion}</button>)}</div>}
      </div>}
      <Button type="submit" className="h-11 w-full rounded-lg" disabled={busy || !name.trim() || (!initial && !credential)}>
        {busy ? <><Loader2 className="animate-spin" />{t("custom_apis.connecting")}</> : <>{t(initial ? "custom_apis.save_key" : "custom_apis.connect")}<ArrowRight /></>}
      </Button>
      {busy && <p role="status" className="text-center text-sm text-muted-foreground">{t("custom_apis.discovering")}</p>}
    </form>
    <div className="mt-5 flex gap-2.5 text-xs leading-relaxed text-muted-foreground"><LockKeyhole className="mt-0.5 h-3.5 w-3.5 shrink-0" /><p>{t("custom_apis.private_key")}</p></div>
    {!initial && <div className="mt-7 border-t border-border/60 pt-5">
      <div className="flex items-center gap-2 text-sm font-medium text-foreground"><Check className="h-4 w-4 text-muted-foreground" />{t("custom_apis.automatic")}</div>
      <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{t("custom_apis.automatic_hint")}</p>
    </div>}
  </div>;
}

function ConnectionDetails({ connection, onBack }: { connection: ApiConnection; onBack: () => void }) {
  const t = useT();
  const [query, setQuery] = useState("");
  const actions = useQuery({ queryKey: ["api-capabilities", connection.id],
    queryFn: () => customApiRequest<{ total: number; actions: { id: string; description: string; risk_tier: string }[] }>(`/connections/${connection.id}/actions`), retry: false });
  const visible = (actions.data?.actions ?? []).filter((action) => action.description.toLowerCase().includes(query.toLowerCase()));
  return <section className="space-y-5">
    <BackLink label={t("custom_apis.title")} onClick={onBack} />
    <div className="flex items-center gap-4"><ConnectionLogo name={connection.name} brand={connection.brand_id} image={connection.logo_data} large />
      <div><h2 className="text-xl font-semibold text-foreground-strong">{connection.name}</h2><p className="mt-1 text-sm text-muted-foreground">{connection.action_count} {t("custom_apis.capabilities_count")}</p></div></div>
    <p className="text-sm leading-relaxed text-muted-foreground">{t("custom_apis.capabilities_hint")}</p>
    <label className="flex items-center gap-2 rounded-lg bg-secondary px-3 py-2.5 text-muted-foreground"><Search className="h-4 w-4" /><input type="search" aria-label={t("custom_apis.search_capabilities")} placeholder={t("custom_apis.search_capabilities")} value={query} onChange={(event) => setQuery(event.target.value)} className="min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none" /></label>
    {actions.isPending && <p role="status" className="text-sm text-muted-foreground">{t("custom_apis.loading")}</p>}
    {actions.error && <p role="alert" className="text-sm text-destructive">{t("custom_apis.failed")}</p>}
    <Panel><ul className="divide-y divide-border/60">{visible.map((action) => <li key={action.id} className="flex items-center gap-3 px-4 py-3 text-sm text-foreground">
      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-foreground-faint" /><span>{action.description}</span>
      {action.risk_tier === "block" && <span className="ml-auto text-xs text-muted-foreground">{t("custom_apis.blocked")}</span>}
    </li>)}</ul></Panel>
    {connection.omitted_operations > 0 && <p className="text-xs text-muted-foreground">{t("custom_apis.partial_capabilities").replace("{count}", String(connection.omitted_operations))}</p>}
    <p className="flex items-center gap-2 text-xs text-muted-foreground"><Users className="h-3.5 w-3.5" />{t("custom_apis.inheritance")}</p>
  </section>;
}
