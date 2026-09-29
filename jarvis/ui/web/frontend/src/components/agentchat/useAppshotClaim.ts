/**
 * An appshot taken for "the next message" lands in the open chat composer.
 *
 * The backend parks a shortcut appshot for the next turn. While the front-page
 * chat is on screen, that next turn is the one being typed here, so the
 * composer claims the picture (single use — the backend drops its copy) and
 * holds it like a pasted screenshot: visible, removable, sent with the
 * sentence through the same attachment path every seat already understands.
 */
import { useEffect, useRef } from "react";

import { useT } from "@/i18n";
import { claimPendingAppshot, fetchPendingAppshot } from "@/lib/appshotApi";
import { useEventStore } from "@/store/events";

export function useAppshotClaim(attachFiles: (files: File[]) => void, enabled: boolean): void {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const eventId = useEventStore((s) => {
    const event = s.events.find((item) => item.name === "AppshotTaken");
    const payload = (event?.payload ?? {}) as { delivered_to?: unknown };
    return event && payload.delivered_to === "message" ? event.id : "";
  });
  const claiming = useRef(false);

  useEffect(() => {
    if (!enabled || claiming.current) return;
    claiming.current = true;
    void (async () => {
      try {
        const { appshot } = await fetchPendingAppshot();
        if (!appshot) return;
        // Once claimed the backend no longer holds it, so it is attached
        // even if this effect was superseded meanwhile — never dropped.
        const file = await claimPendingAppshot(appshot);
        if (!file) return;
        attachFiles([file]);
        pushToast("info", t("appshots.chip_label"));
      } catch {
        // Nothing parked or the backend is restarting: the picture stays
        // where it was, for the next spoken turn to use.
      } finally {
        claiming.current = false;
      }
    })();
  }, [enabled, eventId, attachFiles, pushToast, t]);
}
