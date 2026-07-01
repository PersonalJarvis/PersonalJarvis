/**
 * CliConnectCoach — companion panel for OAuth CLI logins.
 *
 * This file is a minimal stub after the frontend filesystem reset
 * on 2026-04-25. The full-featured variant (with a phase state machine,
 * /check polling, and an auth_check display) will be rebuilt in a
 * follow-up iteration. Until then: a simple status panel with the
 * login command and a manual "check status" action.
 *
 * Polling: handled centrally by `CliConnectPoller` (mounted in App.tsx).
 * On success the poller calls `setCoach(null)`, which unmounts this view.
 */
import { useState } from "react";
import { CheckCircle2, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useEventStore, type CliConnectCoach as CoachState } from "@/store/events";
import { useT } from "@/i18n";

export function CliConnectCoach({ coach }: { coach: CoachState }) {
  const t = useT();
  const setCoach = useEventStore((s) => s.setCliConnectCoach);
  const [connected] = useState(false);

  return (
    <aside className="flex w-[320px] shrink-0 flex-col border-l border-border bg-card/30">
      <header className="flex items-start justify-between gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="font-display text-sm font-semibold">
            {coach.displayName} {t("cli_connect_coach.connect")}
          </div>
          <div className="mt-0.5 flex items-center gap-1.5 text-[11px]">
            {connected ? (
              <>
                <CheckCircle2 className="h-3 w-3 text-primary" />
                <span className="text-primary">{t("cli_connect_coach.connected")}</span>
              </>
            ) : (
              <>
                <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                <span className="text-muted-foreground">{t("cli_connect_coach.waiting_for_login")}</span>
              </>
            )}
          </div>
        </div>
        <button
          type="button"
          onClick={() => setCoach(null)}
          className="text-muted-foreground hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      </header>
      <div className="space-y-3 p-4 text-xs">
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground/70">
            {t("cli_connect_coach.login_command")}
          </div>
          <code className="block break-all rounded-md border border-border bg-background px-2.5 py-1.5 font-mono text-[10px]">
            {coach.loginCommand}
          </code>
        </div>
        <p className="text-muted-foreground">
          {t("cli_connect_coach.instructions")}
        </p>
      </div>
      <footer className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
        <Button size="sm" variant="ghost" onClick={() => setCoach(null)}>
          {connected ? t("cli_connect_coach.done") : t("cli_connect_coach.close")}
        </Button>
      </footer>
    </aside>
  );
}
