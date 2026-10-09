import { memo, useId, useRef, useState, type FormEvent } from "react";
import { Check, KeyRound, ShieldCheck, X } from "lucide-react";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { AgentChatApiError, declineAgentChatCredential, submitAgentChatCredential, type CredentialResult } from "@/lib/agentChatApi";
import { useAgentChat } from "./AgentChatStoreContext";
import type { CredentialState } from "./reduce";

/**
 * An agent's secure credential field (jarvis/agent_chat/credential_requests.py).
 *
 * The person pastes a token the agent asked for; the value goes from this
 * field to the agent's vault in one request and is never kept in the chat:
 * no event, no store and no reply carries it. The agent only learns that the
 * environment variable is set.
 */

export const CredentialCard = memo(function CredentialCard({ credential }: { credential: CredentialState }) {
  const t = useT();
  const descriptionId = useId();
  const agent = credential.asker || t("credential_card.fallback_agent");
  return (
    <div className="my-2 w-full max-w-2xl space-y-3">
      <p id={descriptionId} className="whitespace-pre-wrap text-sm leading-6 text-foreground [overflow-wrap:anywhere]">
        {credential.description || fill(t("credential_card.subtitle"), { agent, env: credential.env })}
      </p>
      {credential.status !== null ? <ClosedCard credential={credential} /> : (
        <OpenCard key={credential.requestId} credential={credential} descriptionId={descriptionId} />
      )}
    </div>
  );
});

function OpenCard({ credential, descriptionId }: { credential: CredentialState; descriptionId: string }) {
  const t = useT();
  const sessionId = useAgentChat((s) => s.activeSessionId);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resolved, setResolved] = useState<CredentialResult["status"] | null>(null);
  const errorId = useId();
  const sending = useRef(false);
  const agent = credential.asker || t("credential_card.fallback_agent");

  const failure = (err: unknown): string => {
    if (err instanceof AgentChatApiError) {
      const keys: Record<string, string> = {
        invalid_token: "invalid_token", network_error: "network_error", validation_timeout: "validation_timeout",
        storage_failed: "storage_failed", request_busy: "request_busy", request_closed: "expired",
      };
      const key = keys[err.code];
      if (key) return t(`credential_card.${key}`);
    }
    if (err instanceof AgentChatApiError && (err.status === 400 || err.status === 422)) return t("credential_card.invalid");
    if (err instanceof AgentChatApiError && (err.status === 404 || err.status === 410)) return t("credential_card.expired");
    if (err instanceof TypeError) return t("credential_card.network_error");
    return t("credential_card.failed");
  };

  const run = async (action: (sid: string) => Promise<CredentialResult>, expected: CredentialResult["status"]) => {
    if (sending.current || !sessionId) return;
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      const result = await action(sessionId);
      if (!result.ok || result.status !== expected || result.request_id !== credential.requestId) {
        throw new AgentChatApiError("credential-unconfirmed", 502);
      }
      setValue("");
      // The HTTP receipt is sufficient even while the event stream reconnects.
      setResolved(result.status);
    } catch (err) {
      setError(failure(err));
      sending.current = false;
      setBusy(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const secret = value.trim();
    if (secret) void run((sid) => submitAgentChatCredential(sid, credential.requestId, secret), "saved");
  };

  if (resolved) return <ClosedCard credential={{ ...credential, status: resolved }} />;

  return (
    <section
      role="group"
      aria-label={t("credential_card.aria")}
      aria-describedby={descriptionId}
      data-testid="credential-card"
      data-state="open"
      className="w-full rounded-2xl border border-border bg-card p-4 text-foreground shadow-sm"
    >
      <header className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold leading-6 [overflow-wrap:anywhere]">{credential.label}</h3>
          {credential.replace ? (
            <p className="mt-1 text-xs leading-5 text-muted-foreground [overflow-wrap:anywhere]">
              {fill(t("credential_card.replace_note"), { agent })}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => void run((sid) => declineAgentChatCredential(sid, credential.requestId), "declined")}
          disabled={busy}
          aria-label={t("credential_card.decline")}
          title={t("credential_card.decline")}
          className="-mr-1 -mt-0.5 shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        >
          <X aria-hidden className="h-5 w-5" />
        </button>
      </header>

      <form onSubmit={submit} className="mt-4 flex flex-col gap-2 sm:flex-row">
        <input
          type="password"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          disabled={busy}
          autoComplete="new-password"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          data-1p-ignore
          data-lpignore="true"
          name={`credential-${credential.requestId}`}
          maxLength={16384}
          placeholder={credential.placeholder || fill(t("credential_card.placeholder"), { label: credential.label })}
          aria-label={credential.label}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? `${descriptionId} ${errorId}` : descriptionId}
          className="min-w-0 flex-1 rounded-xl border border-border bg-background px-4 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={busy || !value.trim()}
          className={cn(
            "shrink-0 rounded-xl px-4 py-2.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            "bg-primary text-primary-foreground hover:opacity-90",
            "disabled:bg-secondary disabled:text-muted-foreground disabled:hover:opacity-100",
          )}
        >
          {busy ? t("credential_card.saving") : t("credential_card.save")}
        </button>
      </form>

      <footer className="mt-3 space-y-1 text-xs leading-5 text-muted-foreground">
        <p className="flex items-center gap-1.5">
          <ShieldCheck aria-hidden className="h-3.5 w-3.5 shrink-0" />
          <span>{t("credential_card.footer")}</span>
        </p>
        <p>{t("credential_card.stays_open")}</p>
        {error ? (
          <p id={errorId} role="alert" className="text-destructive">
            {error}
          </p>
        ) : null}
      </footer>
    </section>
  );
}

function ClosedCard({ credential }: { credential: CredentialState }) {
  const t = useT();
  const saved = credential.status === "saved";
  const key = saved ? "saved" : credential.status === "declined" ? "declined" : credential.status === "timeout" ? "timeout" : "cancelled";
  return (
    <div
      role="status"
      data-testid="credential-card"
      data-state={credential.status ?? ""}
      className="my-1 flex w-full max-w-2xl items-center gap-2 rounded-xl border border-border px-4 py-2.5 text-sm text-muted-foreground"
    >
      {saved ? <Check aria-hidden className="h-4 w-4 shrink-0 text-primary" /> : <KeyRound aria-hidden className="h-4 w-4 shrink-0" />}
      <span className="min-w-0 [overflow-wrap:anywhere]">
        {fill(t(`credential_card.${key}`), { label: credential.label, env: credential.env })}
      </span>
    </div>
  );
}
