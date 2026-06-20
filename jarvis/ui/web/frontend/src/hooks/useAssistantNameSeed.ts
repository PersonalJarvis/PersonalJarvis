import { useEffect } from "react";
import { useEventStore } from "@/store/events";

/**
 * Seed the resolved assistant name into the global store on mount.
 *
 * The assistant's display name (header wordmark + every assistant byline) must
 * follow the configured identity — `[persona].name`, else the name derived from
 * the wake phrase — instead of a hardcoded "Jarvis". This hook reads the current
 * value once via GET /api/settings/assistant-name (mirroring useVoiceStatus /
 * useBrainStatus) and writes `resolved` into the store.
 *
 * The existing useAssistantName hook dispatches `jarvis:assistant-name-changed`
 * after a Settings rename, so we re-fetch on that event to keep every byline
 * live without a reload. A fetch failure (offline / headless host) is a no-op:
 * the store keeps its default and the next event/mount re-seeds it.
 */
export function useAssistantNameSeed(): void {
  const setAssistantName = useEventStore((s) => s.setAssistantName);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();

    const seed = () => {
      void fetch("/api/settings/assistant-name", { signal: ctrl.signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (cancelled || !data) return;
          const resolved =
            typeof data.resolved === "string" ? data.resolved.trim() : "";
          if (resolved) setAssistantName(resolved);
        })
        .catch(() => {
          // Network/timeout/headless — keep the store value; a later rename
          // event (or remount) re-seeds it.
        });
    };

    seed();
    const onChanged = () => seed();
    window.addEventListener("jarvis:assistant-name-changed", onChanged);

    return () => {
      cancelled = true;
      ctrl.abort();
      window.removeEventListener("jarvis:assistant-name-changed", onChanged);
    };
  }, [setAssistantName]);
}
