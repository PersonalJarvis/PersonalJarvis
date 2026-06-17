import { useEffect } from "react";
import { useEventStore } from "@/store/events";

/**
 * Seed the voice-boot readiness flag on mount.
 *
 * The desktop window connects in ~1s, but the voice feature warms up ~20s in
 * the background. The live signal is the WS event `VoiceBootStatus` (handled in
 * useWebSocket.ts) — but WS events are not persistent, so a client mounting
 * AFTER the backend already reported readiness would never see it. This hook
 * reads the current value once via GET /api/voice/status (the REST counterpart),
 * exactly mirroring useBrainStatus's mount-fetch.
 *
 * Fetch-fail (offline / headless host without the route) is a no-op: the store
 * keeps its default `false`, and the WS event fills the gap once the socket
 * delivers it.
 */
export function useVoiceStatus(): void {
  const setVoiceReady = useEventStore((s) => s.setVoiceReady);

  useEffect(() => {
    // `cancelled` guards against the fetch resolving AFTER the component
    // unmounted (HMR / immediate navigation) — without it we would call
    // setVoiceReady on an unmounted component. The live WS event keeps the
    // store correct regardless of how this one-shot seed resolves.
    let cancelled = false;
    const ctrl = new AbortController();

    void fetch("/api/voice/status", { signal: ctrl.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        if (typeof data.ready === "boolean") {
          setVoiceReady(data.ready);
        }
      })
      .catch(() => {
        // Network/timeout/headless — keep the store default; the WS
        // VoiceBootStatus event seeds it once the socket is live.
      });

    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [setVoiceReady]);
}
