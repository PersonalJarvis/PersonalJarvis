import { useState } from "react";
import { useLocaleChunk, useT } from "@/i18n";

type Connection = { path: string; token: string };

/** Credentials stay in this UI's memory, never in chat or the routine spec. */
export function WebhookConnection({ taskId }: { taskId: string }) {
  const t = useT();
  const ready = useLocaleChunk("society");
  const [connection, setConnection] = useState<Connection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const load = async (rotate = false) => {
    if (rotate && !window.confirm(t("society.hooks.rotate_confirm"))) return;
    setBusy(true);
    setError("");
    setCopied(false);
    try {
      const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/webhook-connection${rotate ? "/rotate" : ""}`, {
        method: rotate ? "POST" : "GET", cache: "no-store",
      });
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
      setConnection(body as Connection);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (!connection) return;
    try {
      await navigator.clipboard.writeText(connection.token);
      setCopied(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  if (!ready) return null;
  const button = "rounded border border-border px-2 py-1 text-[11px] text-foreground hover:bg-secondary disabled:opacity-50";
  const field = "w-full rounded border border-border bg-background p-1 text-[11px] text-foreground";
  return (
    <span className="mt-2 block space-y-2" data-testid="webhook-connection">
      {!connection ? <button type="button" className={button} disabled={busy} onClick={() => void load()}>
        {t(busy ? "society.hooks.loading" : "society.hooks.connect")}
      </button> : <>
        <label className="block text-[11px] text-muted-foreground">{t("society.hooks.endpoint")}
          <input className={field} readOnly value={new URL(connection.path, window.location.origin).href} />
        </label>
        <label className="block text-[11px] text-muted-foreground">{t("society.hooks.token")}
          <input className={field} type="password" readOnly autoComplete="off" value={connection.token} />
        </label>
        <span className="flex flex-wrap gap-2">
          <button type="button" className={button} onClick={() => void copy()}>{t(copied ? "society.hooks.copied" : "society.hooks.copy")}</button>
          <button type="button" className={button} disabled={busy} onClick={() => void load(true)}>{t("society.hooks.rotate")}</button>
        </span>
        <span className="block text-[11px] text-muted-foreground">{t("society.hooks.auth")}</span>
        <span className="block text-[11px] text-muted-foreground">{t("society.hooks.remote")}</span>
      </>}
      {error ? <span role="alert" className="block text-[11px] text-destructive">{error}</span> : null}
    </span>
  );
}
