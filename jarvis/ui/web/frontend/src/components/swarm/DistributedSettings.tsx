import { useEffect, useState, type FormEvent } from "react";
import { request } from "./api";
import { useSwarmText } from "./strings";

export interface DistributedConfig {
  enabled: boolean; postgres_configured: boolean; redis_configured: boolean;
  object_credentials_configured: boolean; s3_endpoint: string; s3_bucket: string;
  s3_region: string; max_concurrency: number; available: boolean; reason: string;
}
const SECRETS = ["postgres_dsn", "redis_url", "s3_access_key_id", "s3_secret_access_key"] as const;

/** Optional setup is opened explicitly and never blocks local team creation. */
export function DistributedSettings({ onSaved }: { onSaved: () => void }) {
  const t = useSwarmText();
  const [open, setOpen] = useState(false);
  return <details className="swarm-panel swarm-distributed" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{t("distributedSetup")}</summary>
    {open && <DistributedForm onSaved={onSaved} />}
  </details>;
}
function DistributedForm({ onSaved }: { onSaved: () => void }) {
  const t = useSwarmText();
  const [config, setConfig] = useState<DistributedConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [revision, setRevision] = useState(0);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setError("");
    request<DistributedConfig>("/api/swarm/distributed-config", { signal: abort.signal })
      .then(data => { if (!abort.signal.aborted) setConfig(data); })
      .catch(failure => { if (!abort.signal.aborted) setError(String(failure)); });
    return () => abort.abort();
  }, [retry]);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !config) return;
    const data = new FormData(event.currentTarget);
    const field = (name: string) => String(data.get(name) ?? "").trim();
    const body: Record<string, unknown> = { enabled: data.get("enabled") === "on", s3_endpoint: field("s3_endpoint"), s3_bucket: field("s3_bucket"), s3_region: field("s3_region"), max_concurrency: Number(field("max_concurrency")) };
    for (const name of SECRETS) { const value = field(name); if (value) body[name] = value; }
    setBusy(true); setSaved(false); setError("");
    try {
      const result = await request<DistributedConfig>("/api/swarm/distributed-config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setConfig(result); setRevision(n => n + 1); setSaved(true); onSaved();
    } catch (failure) { setError(String(failure)); }
    finally { setBusy(false); }
  }
  return <>
    <p className="swarm-muted">{t("distributedHint")}</p>
    {error && <p role="alert" className="swarm-error">{error} <button onClick={() => setRetry(n => n + 1)}>{t("refresh")}</button></p>}
    {!config && !error && <p>{t("loading")}</p>}
    {config && <form key={revision} onSubmit={save} aria-label={t("distributedSetup")} autoComplete="off">
      <label className="swarm-check"><input type="checkbox" name="enabled" defaultChecked={config.enabled} />{t("distributedEnable")}</label>
      <p className="swarm-muted">{t(config.available ? "available" : "unavailable")}{config.reason && ` · ${config.reason}`}</p>
      <div className="swarm-form-grid">
        <label>{t("s3Endpoint")}<input name="s3_endpoint" type="url" defaultValue={config.s3_endpoint} /></label>
        <label>{t("s3Bucket")}<input name="s3_bucket" defaultValue={config.s3_bucket} /></label>
        <label>{t("s3Region")}<input name="s3_region" defaultValue={config.s3_region} /></label>
        <label>{t("distributedConcurrency")}<input name="max_concurrency" type="number" min={1} max={10000} defaultValue={config.max_concurrency} required /></label>
        {SECRETS.map(name => {
          const configured = name === "postgres_dsn" ? config.postgres_configured : name === "redis_url" ? config.redis_configured : config.object_credentials_configured;
          return <label key={name}>{t(name)}<input name={name} type="password" autoComplete="new-password" placeholder={t(configured ? "secretConfigured" : "secretMissing")} />
            <span className="swarm-muted">{t(configured ? "configured" : "notConfigured")}</span></label>;
        })}
      </div>
      <p className="swarm-muted">{t("secretHint")}</p>
      <button className="swarm-primary" type="submit" disabled={busy}>{t(busy ? "loading" : "saveConfiguration")}</button>
    </form>}
    {saved && <p className="swarm-notice" role="status">{t("configurationSaved")}</p>}
  </>;
}
