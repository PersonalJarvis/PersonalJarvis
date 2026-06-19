/**
 * CliConnectCoach — Begleit-Panel fuer OAuth-CLI-Logins.
 *
 * Diese Datei ist ein Minimal-Stub nach dem Frontend-Filesystem-Reset
 * vom 2026-04-25. Die ausgereifte Variante (mit Phase-State-Machine,
 * /check-Polling und auth_check-Display) wird in einer Folge-Iteration
 * neu aufgebaut. Bis dahin: einfaches Status-Panel mit dem Login-Command
 * und einer manuellen "Status pruefen"-Action.
 *
 * Polling: handled centrally by `CliConnectPoller` (mounted in App.tsx).
 * On success the poller calls `setCoach(null)`, which unmounts this view.
 */
import { useState } from "react";
import { CheckCircle2, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useEventStore, type CliConnectCoach as CoachState } from "@/store/events";

export function CliConnectCoach({ coach }: { coach: CoachState }) {
  const setCoach = useEventStore((s) => s.setCliConnectCoach);
  const [connected] = useState(false);

  return (
    <aside className="flex w-[320px] shrink-0 flex-col border-l border-border bg-card/30">
      <header className="flex items-start justify-between gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="font-display text-sm font-semibold">
            {coach.displayName} verbinden
          </div>
          <div className="mt-0.5 flex items-center gap-1.5 text-[11px]">
            {connected ? (
              <>
                <CheckCircle2 className="h-3 w-3 text-primary" />
                <span className="text-primary">verbunden</span>
              </>
            ) : (
              <>
                <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                <span className="text-muted-foreground">warte auf login…</span>
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
            Login-Command
          </div>
          <code className="block break-all rounded-md border border-border bg-background px-2.5 py-1.5 font-mono text-[10px]">
            {coach.loginCommand}
          </code>
        </div>
        <p className="text-muted-foreground">
          Folge den Anweisungen im Terminal links. Ich pruefe alle 3 Sekunden,
          ob der Login durch ist.
        </p>
      </div>
      <footer className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
        <Button size="sm" variant="ghost" onClick={() => setCoach(null)}>
          {connected ? "Fertig" : "Schliessen"}
        </Button>
      </footer>
    </aside>
  );
}
