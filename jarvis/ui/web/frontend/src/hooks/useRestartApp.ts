import { useCallback, useState } from "react";

import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

/**
 * The one-click app restart a setting needs when it cannot apply live (a bar
 * <-> orb-window overlay switch, BUG-031). Shared by the display-style picker
 * and the My Pets page so both behave the same.
 *
 * `POST /api/settings/restart-app` answers 409 while missions are running,
 * because a restart would kill them. That arms `forceArmed`; the next call
 * resends with `?force=true`. On success the window goes away, so
 * `restarting` is never cleared there.
 */
export function useRestartApp() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [restarting, setRestarting] = useState(false);
  const [forceArmed, setForceArmed] = useState(false);

  const restart = useCallback(async () => {
    if (restarting) return;
    setRestarting(true);
    try {
      const url = forceArmed
        ? "/api/settings/restart-app?force=true"
        : "/api/settings/restart-app";
      const res = await fetch(url, { method: "POST" });
      if (res.status === 409) {
        // Live missions would be killed — surface the count and arm a force
        // restart instead of killing them silently.
        let count = 0;
        try {
          const body = await res.json();
          count = body?.detail?.missions?.length ?? 0;
        } catch {
          /* malformed body — still arm the override */
        }
        setRestarting(false);
        setForceArmed(true);
        pushToast("warning", `${count} ${t("topbar.restart_missions_running")}`);
        return;
      }
      if (!res.ok) throw new Error(`restart-failed:${res.status}`);
      pushToast("info", t("taskbar_view.restarting"));
    } catch (e) {
      setRestarting(false);
      pushToast("error", (e as Error).message);
    }
  }, [forceArmed, pushToast, restarting, t]);

  const buttonLabel = restarting
    ? t("taskbar_view.restarting")
    : forceArmed
      ? t("topbar.restart_force")
      : t("taskbar_view.restart_now");

  return { restart, restarting, forceArmed, buttonLabel };
}
