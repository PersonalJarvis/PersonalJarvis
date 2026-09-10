import { useState } from "react";
import { useT } from "@/i18n";

export function ProviderVerification({ taskId, provider, audience = "", serviceAccount = "", configured = false }: {
  taskId: string; provider: string; audience?: string; serviceAccount?: string; configured?: boolean;
}) {
  const t = useT();
  const [secret, setSecret] = useState("");
  const [aud, setAud] = useState(audience);
  const [account, setAccount] = useState(serviceAccount);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const field = "w-full rounded border border-border bg-background p-1 text-[12px] text-foreground";
  const save = async () => {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`/api/tasks/${taskId}/webhook-connection`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(provider === "gmail" ? { oidc_audience: aud, service_account: account } : { secret }) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setSecret(""); setMessage(t("society.triggers.provider_saved"));
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  };
  return <div className="space-y-2">
    {provider === "gmail" ? <>
      <label className="block text-[11px]">{t("society.triggers.oidc_audience")}<input className={field} value={aud} onChange={(e) => setAud(e.target.value)} /></label>
      <label className="block text-[11px]">{t("society.triggers.service_account")}<input className={field} value={account} onChange={(e) => setAccount(e.target.value)} /></label>
    </> : <label className="block text-[11px]">{t("society.triggers.provider_secret")}<input className={field} type="password" autoComplete="off" placeholder={configured ? t("society.triggers.stored") : ""} value={secret} onChange={(e) => setSecret(e.target.value)} /></label>}
    <button type="button" className="rounded border border-border px-2 py-1 text-[11px] hover:bg-secondary disabled:opacity-50" disabled={busy || (provider !== "gmail" && !secret)} onClick={() => void save()}>{t("society.triggers.save_connection")}</button>
    {message && <p role="status" className="text-[11px]">{message}</p>}
  </div>;
}
