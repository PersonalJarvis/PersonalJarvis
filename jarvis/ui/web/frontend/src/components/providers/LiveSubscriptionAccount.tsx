import { useEffect, useRef, useState } from "react";
import { ExternalLink, Loader2, LogIn } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import { useT } from "@/i18n";
import {
  cancelLoginFlow,
  getLoginFlow,
  startLoginFlow,
  submitLoginFlowCode,
  type AccountPlatformGroup,
  type LoginFlowState,
} from "@/lib/agentAccountsApi";
import { requestConnect, spreadDelay } from "@/lib/connectBudget";
import { openExternalUrl } from "@/lib/openExternal";

export function LiveSubscriptionAccount({
  group,
  accountId,
  loading,
  disabled,
  voiceStatus,
  onAccountChange,
  onConnected,
}: {
  group?: AccountPlatformGroup;
  accountId: string;
  loading: boolean;
  disabled: boolean;
  voiceStatus: string;
  onAccountChange: (accountId: string) => void;
  onConnected: () => void;
}) {
  const t = useT();
  const selectedId = accountId || group?.active_account || "";
  const account = group?.accounts.find((entry) => entry.id === selectedId);
  const connected = account?.connected && account.mode === "subscription";
  const [flow, setFlow] = useState<LoginFlowState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [code, setCode] = useState("");
  const flowRef = useRef<LoginFlowState | null>(null);
  const onConnectedRef = useRef(onConnected);
  onConnectedRef.current = onConnected;
  const generation = useRef(0);

  // Changing the voice account or leaving setup ends its pending login. A late
  // response must never attach the previous account's flow to the new account.
  useEffect(() => {
    generation.current += 1;
    setFlow(null);
    setError("");
    setCode("");
    setBusy(false);
    return () => {
      generation.current += 1;
      const current = flowRef.current;
      flowRef.current = null;
      if (current && !current.finished) {
        void cancelLoginFlow(current.flow_id).catch(() => {
          // The view is gone; the server also bounds the login lifetime.
        });
      }
    };
  }, [selectedId]);

  function accept(next: LoginFlowState) {
    flowRef.current = next;
    setFlow(next);
    if (next.status === "success") onConnectedRef.current();
  }

  useEffect(() => {
    if (!flow || flow.finished) return;
    let disposed = false;
    const current = generation.current;
    const cancel = requestConnect(() => {
      void getLoginFlow(flow.flow_id).then((next) => {
        if (disposed || generation.current !== current) return;
        flowRef.current = next;
        setFlow(next);
        if (next.status === "success") onConnectedRef.current();
      }).catch(() => {
        if (disposed || generation.current !== current) return;
        // A failed status read is terminal here; retry is an explicit action.
        setError(t("live.subscription_login_failed"));
        setFlow(null);
      });
    }, spreadDelay(1500));
    return () => { disposed = true; cancel(); };
  }, [flow, t]);

  async function signIn() {
    if (!selectedId || busy) return;
    const current = generation.current;
    setBusy(true);
    setError("");
    try {
      const next = await startLoginFlow(selectedId);
      if (generation.current !== current) {
        if (!next.finished) await cancelLoginFlow(next.flow_id);
        return;
      }
      accept(next);
    } catch {
      if (generation.current === current) setError(t("live.subscription_login_failed"));
    } finally {
      if (generation.current === current) setBusy(false);
    }
  }

  async function submit() {
    if (!flow || !code.trim() || busy) return;
    const current = generation.current;
    setBusy(true);
    setError("");
    try {
      const next = await submitLoginFlowCode(flow.flow_id, code.trim());
      if (generation.current === current) { accept(next); setCode(""); }
    } catch {
      if (generation.current === current) setError(t("live.subscription_login_failed"));
    } finally {
      if (generation.current === current) setBusy(false);
    }
  }

  async function cancel() {
    if (!flow || busy) return;
    generation.current += 1;
    setBusy(true);
    try {
      await cancelLoginFlow(flow.flow_id);
      flowRef.current = null;
      setFlow(null);
    } catch {
      setError(t("live.subscription_login_failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-border bg-secondary/30 p-3">
      <label className="block space-y-2 text-sm">
        <span>{t("live.subscription_account")}</span>
        <BrandedSelect
          value={accountId}
          disabled={disabled || loading || busy}
          ariaLabel={t("live.subscription_account")}
          onValueChange={onAccountChange}
          options={[
            { value: "", label: t("live.subscription_active_account") },
            ...(group?.accounts ?? []).map((entry) => ({ value: entry.id, label: entry.label })),
          ]}
        />
      </label>
      <p role="status" className="text-xs leading-relaxed text-muted-foreground">
        {loading ? t("live.loading") : connected
          ? t("live.subscription_connected") : t("live.subscription_sign_in_required")}
      </p>
      {connected ? (
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t(voiceStatus === "ready" ? "live.subscription_voice_ready"
            : voiceStatus === "unavailable" ? "live.subscription_voice_unavailable"
              : "live.subscription_voice_unverified")}
        </p>
      ) : null}
      {(!flow || flow.finished) && !connected ? (
        <Button type="button" variant="outline" size="sm" disabled={disabled || busy || !selectedId} onClick={() => void signIn()}>
          {busy ? <Loader2 className="animate-spin" /> : <LogIn />}
          {t("live.subscription_sign_in")}
        </Button>
      ) : null}
      {flow && !flow.finished ? (
        <div className="space-y-2 border-t border-border pt-3">
          <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {t("agent_accounts.flow.waiting_browser")}
          </p>
          {flow.url ? (
            <Button type="button" variant="outline" size="sm" onClick={() => {
              void openExternalUrl(flow.url!).then((opened) => {
                if (!opened) setError(t("live.subscription_browser_failed"));
              });
            }}>
              <ExternalLink />{t("agent_accounts.flow.open")}
            </Button>
          ) : null}
          {flow.code_expected ? (
            <div className="flex flex-wrap items-end gap-2">
              <label className="min-w-0 flex-1 space-y-1 text-xs">
                <span>{t("agent_accounts.flow.code_placeholder")}</span>
                <input value={code} onChange={(event) => setCode(event.target.value)} autoComplete="off" spellCheck={false}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground" />
              </label>
              <Button type="button" size="sm" disabled={busy || !code.trim()} onClick={() => void submit()}>{t("agent_accounts.flow.submit")}</Button>
            </div>
          ) : null}
          <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void cancel()}>{t("agent_accounts.flow.cancel")}</Button>
        </div>
      ) : null}
      {flow?.status === "failed" ? <p role="alert" className="text-xs text-destructive">{t("live.subscription_login_failed")}</p> : null}
      {error ? <p role="alert" className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}
