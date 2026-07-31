import { useState } from "react";
import { KeyboardOff, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useInputIsolation } from "@/hooks/useInputIsolation";

/**
 * App-wide alert: outside input software cannot type into this window.
 *
 * Dictation and voice-typing apps, OS accessibility voice control, text
 * expanders, clipboard managers, and password-manager auto-type all inject
 * synthetic keystrokes into the focused window. Windows blocks that for any
 * window owned by a higher-integrity process, so while the app runs elevated
 * they appear to do nothing here while working in every other app — and neither
 * side reports an error, which is precisely what makes it undiagnosable from the
 * outside (live report 2026-07-25: "dictation just doesn't work in the Jarvis
 * window").
 *
 * Renders nothing whenever the window is reachable, which is the normal case on
 * every platform — including when the privilege state could not be read at all
 * (we never nag on a guess).
 */
export function InputIsolationBanner() {
  const t = useT();
  const pushToast = useEventStore((state) => state.pushToast);
  const { report } = useInputIsolation();
  const [dismissed, setDismissed] = useState(false);
  const [restarting, setRestarting] = useState(false);

  if (!report?.blocked || dismissed) return null;

  async function restartUnelevated() {
    if (restarting) return;
    setRestarting(true);
    try {
      const response = await fetch("/api/settings/restart-unelevated", {
        method: "POST",
      });
      if (response.ok) return; // the window closes and comes back unelevated
      const body = await response.json().catch(() => null);
      const error = body?.detail?.error;
      if (error === "missions_running") {
        pushToast("warning", t("topbar.restart_missions_running"));
      } else if (error === "deescalation_failed") {
        // Honest failure: the app is still up and still elevated.
        pushToast("error", body?.detail?.message ?? t("input_isolation.restart_failed"));
      } else {
        pushToast("error", t("input_isolation.restart_failed"));
      }
    } catch {
      pushToast("error", t("input_isolation.restart_failed"));
    }
    setRestarting(false);
  }

  return (
    <div
      data-testid="input-isolation-banner"
      data-reason={report.reason}
      role="alert"
      className="border-b-2 border-amber-500/50 bg-amber-500/10 text-amber-100"
    >
      <div className="flex items-start gap-3 px-4 py-2.5">
        <KeyboardOff className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold leading-tight">
            {t("input_isolation.title")}
          </p>
          <p className="mt-0.5 text-xs leading-snug text-amber-200/90">
            {t("input_isolation.impact")}
          </p>
          {!report.can_restart_unelevated && (
            <p className="mt-1 text-xs leading-snug text-amber-200/70">
              {t("input_isolation.manual_hint")}
            </p>
          )}
        </div>
        {report.can_restart_unelevated && (
          <Button
            size="sm"
            disabled={restarting}
            onClick={() => void restartUnelevated()}
          >
            {restarting && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            {t(restarting ? "input_isolation.restarting" : "input_isolation.restart_now")}
          </Button>
        )}
        <Button
          size="icon"
          variant="ghost"
          className="h-7 w-7 shrink-0"
          aria-label={t("input_isolation.dismiss")}
          title={t("input_isolation.dismiss")}
          onClick={() => setDismissed(true)}
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </Button>
      </div>
    </div>
  );
}
