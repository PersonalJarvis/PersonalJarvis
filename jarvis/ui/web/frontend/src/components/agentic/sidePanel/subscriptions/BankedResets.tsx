import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { fill, useT, useUiLanguage } from "@/i18n";
import type { AccountUsage } from "@/lib/agentAccountsApi";
import { consumeAccountReset, fetchAccountResets, ResetRequestError, type AccountResets, type ResetAttempt, type ResetCredit } from "@/lib/agentResetsApi";
import { openExternalUrl } from "@/lib/openExternal";

const BASE = "ide_side_panel.subscriptions.bank";
const BUTTON = "rounded border border-border px-2 py-1 text-[11px] text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

function savedAttempt(accountId: string): ResetAttempt | null {
  try {
    const data = JSON.parse(sessionStorage.getItem(`subscription-reset:${accountId}`) || "null");
    return data && typeof data.idempotency_key === "string" && typeof data.account_key === "string"
      && (data.credit_id === null || typeof data.credit_id === "string") ? data : null;
  } catch {
    // An unavailable or corrupt storage entry must never trigger a redemption.
    return null;
  }
}

export function BankedResets({ accountId, accountName, revision, onUsage }: {
  accountId: string; accountName: string; revision: number;
  onUsage: (usage: AccountUsage) => void;
}) {
  const t = useT();
  const lang = useUiLanguage();
  const [snapshot, setSnapshot] = useState<AccountResets | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [outcome, setOutcome] = useState("");
  const [confirm, setConfirm] = useState<{ credit: ResetCredit | null; accountKey: string; accountName: string } | null>(null);
  const [pending, setPending] = useState(() => savedAttempt(accountId));
  const submitting = useRef(false);
  const generation = useRef(0);
  const load = useCallback(async () => {
    if (submitting.current) return;
    const id = ++generation.current;
    setLoading(true);
    try {
      const next = await fetchAccountResets(accountId);
      if (id !== generation.current) return;
      setSnapshot(next);
      setError("");
      if (next.usage) onUsage(next.usage);
    } catch {
      if (id === generation.current) { setSnapshot(null); setError("unavailable"); }
    } finally {
      if (id === generation.current) setLoading(false);
    }
  }, [accountId, onUsage]);

  useEffect(() => {
    void load();
    return () => { generation.current += 1; };
  }, [load, revision]);

  const redeem = async () => {
    if (submitting.current) return;
    const attempt = pending ?? (confirm ? {
      idempotency_key: crypto.randomUUID(), credit_id: confirm.credit?.id ?? null, account_key: confirm.accountKey,
    } : null);
    if (!attempt) return;
    // Save BEFORE sending. A closed panel, reload or lost reply must retain
    // the same provider idempotency key; there is no automatic retry.
    try { sessionStorage.setItem(`subscription-reset:${accountId}`, JSON.stringify(attempt)); }
    catch { setError("storage_failed"); return; }
    submitting.current = true;
    setBusy(true);
    setPending(attempt);
    setError("");
    setOutcome("");
    generation.current += 1;
    try {
      const result = await consumeAccountReset(accountId, attempt);
      sessionStorage.removeItem(`subscription-reset:${accountId}`);
      setPending(null);
      setConfirm(null);
      setSnapshot(result.resets);
      setOutcome(result.outcome);
      if (result.resets.usage) onUsage(result.resets.usage);
    } catch (err) {
      if (err instanceof ResetRequestError && ["account_changed", "signed_out", "unsupported"].includes(err.code)) {
        sessionStorage.removeItem(`subscription-reset:${accountId}`);
        setPending(null);
        setConfirm(null);
        setSnapshot(null);
        setError(err.code);
      } else setError("uncertain");
    } finally {
      submitting.current = false;
      setBusy(false);
      setLoading(false);
    }
  };

  const eligible = (credit: ResetCredit) => credit.redeemable
    && (!credit.expires_at || Date.parse(credit.expires_at) > Date.now());
  const useButton = (credit: ResetCredit | null) => (
    <button type="button" className={BUTTON} disabled={busy || loading || !!pending || !!confirm || !snapshot?.can_redeem || (!!credit && !eligible(credit))}
      onClick={() => { if (snapshot?.account_key) setConfirm({ credit, accountKey: snapshot.account_key, accountName }); setOutcome(""); }}>
      {t(`${BASE}.use`)}
    </button>
  );
  return (
    <section aria-label={t(`${BASE}.title`)} className="mt-3 space-y-2 border-t border-border/60 pt-2 text-[11px]">
      <div className="flex items-center justify-between gap-2">
        <h4 className="font-medium text-foreground">{t(`${BASE}.title`)}</h4>
        <button type="button" className={BUTTON} aria-label={t(`${BASE}.refresh`)} disabled={loading || busy} onClick={() => void load()}>
          {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
        </button>
      </div>
      {loading && <p className="text-muted-foreground">{t(`${BASE}.loading`)}</p>}
      {!loading && snapshot?.status === "ok" && <>
        <p className="text-muted-foreground">{fill(t(`${BASE}.count`), { count: snapshot.available_count ?? 0 })}</p>
        {snapshot.credits === null && snapshot.can_redeem && <div className="space-y-1">
          <p className="text-muted-foreground">{t(`${BASE}.count_only`)}</p>{useButton(null)}
        </div>}
        {snapshot.credits?.map((credit) => <div key={credit.id} className="space-y-1 rounded-md bg-muted/50 p-2">
          <p className="break-words font-medium text-foreground">{credit.title || t(`${BASE}.reset_name`)}</p>
          {credit.description && <p className="break-words text-muted-foreground">{credit.description}</p>}
          <p className="text-muted-foreground">{credit.expires_at
            ? fill(t(`${BASE}.expires`), { when: new Intl.DateTimeFormat(lang, { dateStyle: "medium", timeStyle: "short" }).format(new Date(credit.expires_at)) })
            : t(`${BASE}.expiry_unknown`)}</p>
          {useButton(credit)}
        </div>)}
      </>}
      {!loading && snapshot && snapshot.status !== "ok" && <p className="text-muted-foreground">{t(`${BASE}.${snapshot.status}`)}</p>}
      {confirm && !pending && <div role="group" aria-label={t(`${BASE}.confirm_title`)} className="space-y-2 rounded-md border border-border p-2">
        <p className="break-words text-foreground">{fill(t(`${BASE}.confirm_text`), { account: confirm.accountName, reset: confirm.credit?.title || t(`${BASE}.reset_name`) })}</p>
        <p className="text-muted-foreground">{t(`${BASE}.irreversible`)}</p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={BUTTON} disabled={busy} onClick={() => void redeem()}>{t(`${BASE}.confirm`)}</button>
          <button type="button" className={BUTTON} disabled={busy} onClick={() => setConfirm(null)}>{t(`${BASE}.cancel`)}</button>
        </div>
      </div>}
      {pending && <div className="space-y-2">
        <p className="text-muted-foreground">{t(`${BASE}.pending`)}</p>
        <button type="button" className={BUTTON} disabled={busy} onClick={() => void redeem()}>{t(`${BASE}.${busy ? "using" : "retry"}`)}</button>
      </div>}
      {error && <p role="alert" className="text-destructive">{t(`${BASE}.${error}`)}</p>}
      {outcome && <p role="status" className="text-foreground">{t(`${BASE}.${outcome}`)}</p>}
      {snapshot?.manage_url && <div className="space-y-1">
        <button type="button" className={BUTTON} onClick={async () => {
          if (!await openExternalUrl(snapshot.manage_url!)) setError("open_failed");
        }}><ExternalLink className="mr-1 inline h-3 w-3" />{t(`${BASE}.manage`)}</button>
        <p className="text-muted-foreground">{fill(t(`${BASE}.browser_account`), { account: accountName })}</p>
      </div>}
    </section>
  );
}
