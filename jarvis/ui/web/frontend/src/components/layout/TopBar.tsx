import { useCallback, useEffect, useRef, useState } from "react";
import { RotateCw } from "lucide-react";

import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * Global top bar rendered above every view (see App.tsx). Its only job today is
 * to carry a single app-chrome action: a "Restart Jarvis" button that is
 * reachable from *every* screen — not just the Settings → Bar & Overlay panel
 * where the restart control historically lived.
 *
 * Why a global bar and not the per-view ``ViewHeader``: a couple of views
 * (DocsView's 3-column layout, SubAgentsView's departure board) don't render a
 * ViewHeader at all, and ~13 views already fill the header's ``right`` slot with
 * their own actions. Putting the button in the shell, above MainView, is the
 * only placement that is on every screen and collides with nothing.
 *
 * The backend already ships the whole self-restart machinery: this POSTs to the
 * existing ``/api/settings/restart-app`` endpoint, which spawns a detached,
 * cross-platform relauncher (see jarvis/ui/relauncher.py). On a headless host
 * the endpoint returns 503 and we recover the button with an honest toast.
 *
 * A restart tears down the whole app mid-task, so the button is a two-click
 * confirm (arm → confirm) rather than a fire-on-first-click control. We avoid a
 * native ``window.confirm`` on purpose — it blocks the pywebview event loop.
 */
const CONFIRM_TIMEOUT_MS = 4000;

export function TopBar() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [confirming, setConfirming] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const resetTimer = useRef<number | null>(null);

  const clearResetTimer = useCallback(() => {
    if (resetTimer.current !== null) {
      clearTimeout(resetTimer.current);
      resetTimer.current = null;
    }
  }, []);

  // Drop a pending "disarm" timer if the bar is ever unmounted.
  useEffect(() => clearResetTimer, [clearResetTimer]);

  async function doRestart() {
    clearResetTimer();
    setRestarting(true);
    try {
      const res = await fetch("/api/settings/restart-app", { method: "POST" });
      if (!res.ok) throw new Error(`restart-failed:${res.status}`);
      // On success the window goes away — keep the spinning state; never clear
      // it, so the user doesn't see the button flip back before the app dies.
      pushToast("info", t("topbar.restarting"));
    } catch {
      // Headless host (503) or a transient failure: recover the control so the
      // user isn't left with a dead button.
      setRestarting(false);
      setConfirming(false);
      pushToast("error", t("topbar.restart_failed"));
    }
  }

  function onClick() {
    if (restarting) return;
    if (!confirming) {
      // First click only arms the confirmation; auto-disarm after a few
      // seconds so a stray click never leaves a primed restart button behind.
      setConfirming(true);
      clearResetTimer();
      resetTimer.current = window.setTimeout(() => {
        setConfirming(false);
        resetTimer.current = null;
      }, CONFIRM_TIMEOUT_MS);
      return;
    }
    void doRestart();
  }

  const label = restarting
    ? t("topbar.restarting")
    : confirming
      ? t("topbar.restart_confirm")
      : t("topbar.restart");

  return (
    <div className="flex h-10 shrink-0 items-center justify-end border-b border-border bg-background/70 px-4 backdrop-blur-sm">
      <button
        type="button"
        onClick={onClick}
        disabled={restarting}
        title={t("topbar.restart_hint")}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors disabled:cursor-default disabled:opacity-70",
          confirming
            ? "border-amber-500/60 bg-amber-500/10 text-amber-500 hover:bg-amber-500/20"
            : "border-border bg-secondary/40 text-muted-foreground hover:border-primary/50 hover:text-foreground",
        )}
      >
        <RotateCw
          aria-hidden
          className={cn("h-3.5 w-3.5", restarting && "animate-spin")}
        />
        {label}
      </button>
    </div>
  );
}
