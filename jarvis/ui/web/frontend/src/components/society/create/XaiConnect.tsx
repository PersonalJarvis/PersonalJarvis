/**
 * "Connect Grok subscription" under a blocked Grok access in the New-agent
 * dialog. The app asks xAI for a device code (`/api/agent-runtimes/xai-login`),
 * opens xAI's approval page and waits until the person approved it there; the
 * login is Jarvis' own for its Hermes / OpenClaw agents, so the Grok CLI's
 * login is never touched. Once connected the runtimes list refreshes and the
 * Grok subscription can be picked.
 */
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { fill, useT } from "@/i18n";
import { fetchXaiLogin, startXaiLogin, type XaiLoginPending } from "@/lib/agentRuntimesApi";
import { openExternalUrl } from "@/lib/openExternal";
import { AGENT_RUNTIMES_QUERY_KEY } from "../card/RuntimePicker";

/** How often the dialog asks whether the approval arrived. */
const POLL_MS = 3_000;

export function XaiConnect({ disabled }: { disabled: boolean }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<XaiLoginPending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!pending) return;
    let stopped = false;
    const timer = window.setInterval(() => {
      void fetchXaiLogin().then((status) => {
        if (stopped) return;
        if (status.connected) {
          setPending(null);
          void queryClient.invalidateQueries({ queryKey: AGENT_RUNTIMES_QUERY_KEY });
        } else if (!status.pending) {
          setPending(null);
          setError(status.error || "refused");
        }
      }).catch(() => {
        // A missed poll is retried on the next tick.
      });
    }, POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [pending, queryClient]);

  async function connect() {
    setBusy(true);
    setError("");
    try {
      const login = await startXaiLogin();
      setPending(login);
      await openExternalUrl(login.verification_url);
    } catch {
      setError("start_failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-1.5" data-testid="create-agent-xai-connect">
      {pending ? (
        <p className="text-xs text-muted-foreground">
          {fill(t("society.create_agent.xai_waiting"), { code: pending.user_code })}{" "}
          <button
            type="button"
            className="underline underline-offset-2 hover:text-foreground"
            onClick={() => void openExternalUrl(pending.verification_url)}
          >
            {t("society.create_agent.xai_open_again")}
          </button>
        </p>
      ) : (
        <Button type="button" size="sm" variant="outline" className="self-start" disabled={disabled || busy} onClick={() => void connect()}>
          {t("society.create_agent.xai_connect")}
        </Button>
      )}
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {t(error === "tier_denied" ? "society.create_agent.xai_tier_denied"
            : error === "declined" ? "society.create_agent.xai_declined"
              : error === "expired" ? "society.create_agent.xai_expired"
                : "society.create_agent.xai_failed")}
        </p>
      ) : null}
    </div>
  );
}
