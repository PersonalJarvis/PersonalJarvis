import { useEffect, useState } from "react";
import { KeyRound } from "lucide-react";
import type { AntigravityStatus } from "@/hooks/useProviders";
import { useT } from "@/i18n";

/** A connected Google login is a subscription even without multi-account support. */
export function AntigravitySubscriptionCard({ refresh }: { refresh: number }) {
  const t = useT();
  const [status, setStatus] = useState<AntigravityStatus | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/antigravity/status", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Antigravity status unavailable");
        const next = await response.json() as AntigravityStatus;
        if (!controller.signal.aborted) { setStatus(next); setFailed(false); }
      })
      .catch(() => {
        // Render the failed read; never present a previous login as current.
        if (!controller.signal.aborted) { setStatus(null); setFailed(true); }
      });
    return () => controller.abort();
  }, [refresh]);
  const connected = status?.installed && status.connected && status.mode === "oauth-personal";
  return <section aria-label="Antigravity" className="flex flex-col gap-3 rounded-2xl border border-border bg-card p-4">
    <h4 className="text-sm font-semibold">Antigravity</h4>
    <div className="space-y-2 rounded-xl border border-border px-3 py-2">
      <p className="flex items-start gap-2 text-xs text-muted-foreground">
        <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        {status ? connected || status.mode !== "api_key" ? status.message : t("agent_accounts.not_signed_in") : t(failed ? "agent_accounts.usage.state.unavailable" : "agent_accounts.loading")}
      </p>
      <p className="text-micro text-muted-foreground">{t("agent_accounts.single_google_login")}</p>
      <p className="text-micro text-muted-foreground">{t("agent_accounts.usage.state.unsupported")}</p>
    </div>
  </section>;
}
