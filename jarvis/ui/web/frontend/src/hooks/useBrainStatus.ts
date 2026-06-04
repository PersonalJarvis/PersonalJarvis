import { useEffect } from "react";
import { useEventStore } from "@/store/events";

/**
 * Halt den Sidebar-Footer-Brain-Status im Zustand-Store synchron.
 *
 * Drei Update-Pfade — Defense-in-Depth:
 *   1. Mount-Fetch von /api/brain/status (initialer Wert).
 *   2. Live via WS-Event BrainProviderSwitched (siehe useWebSocket.ts:110).
 *   3. Custom-Event jarvis:brain-switched (vom ApiKeysView dispatched, falls
 *      WS-Pfad eine Race-Condition oder einen Disconnect hatte).
 *
 * Pfad 3 war bis 2026-04-25 nicht verdrahtet — Klick auf eine Provider-
 * Karte wechselte den Brain backendseitig, aber der Sidebar-Footer blieb
 * auf dem Mount-Wert haengen, wenn der WS-Event-Round-Trip aus irgendeinem
 * Grund nicht durchkam.
 */
export function useBrainStatus(): void {
  const setBrainProvider = useEventStore((s) => s.setBrainProvider);

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
        })
        .catch(() => {
          // Fetch fail (network/timeout) — Store bleibt auf altem Wert,
          // Sidebar zeigt "—" bis ein WS-Event die Lücke füllt.
        });

    void fetchStatus(ctrl.signal);

    const onBrainSwitched = () => {
      // Frischen Fetch — der Endpoint liest aus app.state.brain.active_provider,
      // also ist er nach dem Switch authoritativ.
      void fetchStatus();
    };
    window.addEventListener("jarvis:brain-switched", onBrainSwitched);

    return () => {
      cancelled = true;
      ctrl.abort();
      window.removeEventListener("jarvis:brain-switched", onBrainSwitched);
    };
  }, [setBrainProvider]);
}
