/**
 * An appshot taken for "the next message" lands in the open chat composer.
 *
 * The backend parks a shortcut appshot for the next turn. While the front-page
 * chat is on screen, that next turn is the one being typed here, so the
 * composer claims the picture (single use — the backend drops its copy) and
 * holds it like a pasted screenshot: visible, removable, sent with the
 * sentence through the same attachment path every seat already understands.
 */
import { useEffect, useLayoutEffect, useRef } from "react";

import { useT } from "@/i18n";
import { claimPendingAppshot, fetchPendingAppshot } from "@/lib/appshotApi";
import { useEventStore } from "@/store/events";

export function useAppshotClaim(
  attachFiles: (files: File[]) => void, enabled: boolean, recipient = "",
): void {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const eventId = useEventStore((s) => {
    const event = s.events.find((item) => item.name === "AppshotTaken");
    const payload = (event?.payload ?? {}) as { delivered_to?: unknown };
    return event && payload.delivered_to === "message" ? event.id : "";
  });
  const claiming = useRef(false);
  const mounted = useRef(false);
  const requested = useRef(0);
  const latest = useRef({ attachFiles, pushToast, t, enabled });
  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useLayoutEffect(() => {
    latest.current = { attachFiles, pushToast, t, enabled };
  }, [attachFiles, pushToast, t, enabled]);

  useEffect(() => {
    requested.current += 1;
    if (!enabled || claiming.current) return;
    claiming.current = true;
    void (async () => {
      try {
        for (;;) {
          const version = requested.current;
          const { appshot } = await fetchPendingAppshot();
          if (!mounted.current || !latest.current.enabled) return;
          // A different chat/event arrived during the GET. Re-read serially
          // before consuming the picture for the newly active recipient.
          if (version !== requested.current) continue;
          if (appshot) {
            const destination = latest.current;
            const file = await claimPendingAppshot(appshot);
            if (file) {
              // "Next message" follows the currently enabled composer. Once
              // claimed, retain the original destination if that composer was
              // disabled meanwhile; never route it into a different surface.
              const current = latest.current.enabled ? latest.current : destination;
              current.attachFiles([file]);
              current.pushToast("info", current.t("appshots.chip_label"));
            }
          }
          if (version === requested.current || !latest.current.enabled) return;
        }
      } catch {
        // Nothing parked or the backend is restarting: the picture stays
        // where it was, for the next spoken turn to use.
      } finally {
        claiming.current = false;
      }
    })();
  }, [enabled, eventId, recipient]);
}
