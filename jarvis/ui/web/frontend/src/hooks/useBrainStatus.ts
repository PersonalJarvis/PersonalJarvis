import { useEffect } from "react";
import { useEventStore } from "@/store/events";

/**
 * Keeps the sidebar-footer brain status in sync with the Zustand store.
 *
 * Three update paths — defense in depth:
 *   1. Mount fetch of /api/brain/status (initial value).
 *   2. Live via WS event BrainProviderSwitched (see useWebSocket.ts:110).
 *   3. Custom event jarvis:brain-switched (dispatched by ApiKeysView, in case
 *      the WS path had a race condition or a disconnect).
 *
 * Path 3 wasn't wired up until 2026-04-25 — clicking a provider card
 * switched the brain on the backend, but the sidebar footer stayed
 * stuck on the mount value whenever the WS event round trip didn't
 * come through for whatever reason.
 */
export function useBrainStatus(): void {
  const setBrainProvider = useEventStore((s) => s.setBrainProvider);
  const setBrainModel = useEventStore((s) => s.setBrainModel);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();

    const fetchStatus = (signal?: AbortSignal) =>
      fetch("/api/brain/status", { signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (cancelled || !data) return;
          if (typeof data.provider === "string" && data.provider) {
            setBrainProvider(data.provider);
          }
          // The endpoint always returns a model; "unknown" means the provider
          // has no model configured — surface an empty string then so the
          // sidebar shows only the provider rather than a literal "unknown".
          if (typeof data.model === "string") {
            setBrainModel(data.model === "unknown" ? "" : data.model);
          }
        })
        .catch(() => {
          // Fetch failed (network/timeout) — store stays on the old value,
          // sidebar shows "—" until a WS event fills the gap.
        });

    void fetchStatus(ctrl.signal);

    const onBrainSwitched = () => {
      // Fresh fetch — the endpoint reads from app.state.brain.active_provider,
      // so it's authoritative right after the switch.
      void fetchStatus();
    };
    window.addEventListener("jarvis:brain-switched", onBrainSwitched);

    return () => {
      cancelled = true;
      ctrl.abort();
      window.removeEventListener("jarvis:brain-switched", onBrainSwitched);
    };
  }, [setBrainProvider, setBrainModel]);
}
