/**
 * The buttons a health check can offer, in one place.
 *
 * Two surfaces render checks now — the problem list on the overview and the
 * full checklist behind "all checks" — and a check's action must do the same
 * thing from both. Keeping the mapping here means adding a new action kind in
 * `jarvis/ultrawiki/health.py` is a one-line change on this side, instead of
 * two handlers that drift until one of them silently does nothing.
 */
import { useState } from "react";

import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  requeueUltraWikiFailed,
  syncAllUltraWikiSources,
  type UltraWikiHealthCheck,
} from "@/lib/ultrawikiApi";

export interface CheckActionHandlers {
  onOpenSources: () => void;
  onOpenSettings: () => void;
  onEnableMode?: () => void;
  /** Refresh whatever the caller is showing once an action changed something. */
  onChanged: () => void;
}

export function useCheckActions(handlers: CheckActionHandlers): {
  busy: boolean;
  runAction: (check: UltraWikiHealthCheck) => void;
} {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [busy, setBusy] = useState(false);

  async function syncAll() {
    setBusy(true);
    try {
      const result = await syncAllUltraWikiSources();
      // `started` empty is not a failure — every source may already be up to
      // date, and reporting that as an error trains people to ignore toasts.
      pushToast(result.started.length ? "success" : "info", result.detail);
      handlers.onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function retryFailed() {
    setBusy(true);
    try {
      const result = await requeueUltraWikiFailed();
      pushToast(
        "success",
        t("ultrawiki.progress.retry_failed_done").replace(
          "{0}",
          String(result.requeued),
        ),
      );
      handlers.onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function runAction(check: UltraWikiHealthCheck) {
    switch (check.action?.kind) {
      case "sync_all":
        void syncAll();
        return;
      case "retry_failed":
        void retryFailed();
        return;
      case "open_sources":
        handlers.onOpenSources();
        return;
      case "open_settings":
        handlers.onOpenSettings();
        return;
      case "enable_mode":
        handlers.onEnableMode?.();
        return;
      default:
        // An action kind this build does not know about. Saying so beats a
        // button that looks live and does nothing when clicked.
        pushToast("info", t("ultrawiki.health.action_unknown"));
    }
  }

  return { busy, runAction };
}
