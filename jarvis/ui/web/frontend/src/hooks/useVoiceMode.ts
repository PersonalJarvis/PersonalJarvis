import { useEffect, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { useEventStore } from "@/store/events";

type VoiceModeResp = {
  mode: string;
  realtime_available: boolean;
  active_provider: string | null;
  // Sidebar-footer display fields: pretty provider name + the model an idle
  // realtime session would use (configured pin or catalog default).
  active_provider_label: string | null;
  active_model: string | null;
  session_active: boolean;
  active_session_mode: "pipeline" | "realtime" | null;
  active_session_provider: string;
  active_session_model: string;
  transitioning: boolean;
};

export function useVoiceMode() {
  const qc = useQueryClient();
  const events = useEventStore((state) => state.events);
  const q = useQuery<VoiceModeResp>({
    queryKey: ["voice-mode"],
    queryFn: async () => (await fetch("/api/settings/voice-mode")).json(),
  });
  const m = useMutation({
    mutationFn: async (mode: string) => {
      const r = await fetch("/api/settings/voice-mode", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode, persist: true }),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    },
    // Optimistic: flip the cached mode BEFORE the PUT resolves, so the
    // Pipeline|Realtime segment's filled "live" state follows the click
    // instantly (the persist can take seconds on a busy backend, and the UI
    // used to only update after PUT + a full refetch — the switch felt dead).
    // A failed PUT rolls back to the previous server truth.
    onMutate: async (mode: string) => {
      await qc.cancelQueries({ queryKey: ["voice-mode"] });
      const prev = qc.getQueryData<VoiceModeResp>(["voice-mode"]);
      if (prev) qc.setQueryData<VoiceModeResp>(["voice-mode"], { ...prev, mode });
      return { prev };
    },
    onError: (_err, _mode, ctx) => {
      if (ctx?.prev) qc.setQueryData(["voice-mode"], ctx.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["voice-mode"] }),
  });

  // Activating a realtime provider CARD (ApiKeysView's ProviderCategory, the
  // "Set active" action on a Realtime tier card) flips `[voice].mode`
  // server-side too (provider_routes.py::realtime_switch, Feature A4) — that
  // write bypasses this hook's own mutation, so the "Active" badge would
  // otherwise go stale until an unrelated refetch. Listen for the event the
  // card activation already dispatches and refresh here.
  useEffect(() => {
    function onRealtimeSwitched() {
      qc.invalidateQueries({ queryKey: ["voice-mode"] });
    }
    window.addEventListener("jarvis:realtime-switched", onRealtimeSwitched);
    return () => window.removeEventListener("jarvis:realtime-switched", onRealtimeSwitched);
  }, [qc]);

  // Configured mode and effective in-flight mode are distinct. Refresh the
  // runtime snapshot on voice boundaries and on the accepted realtime
  // handshake so the switch never labels an old classic call as Realtime.
  const lastRuntimeEvent = useMemo(
    () =>
      events.find((event) =>
        ["VoiceSessionStarted", "RealtimeSessionReady", "VoiceSessionEnded"].includes(
          event.name,
        ),
      ) ?? null,
    [events],
  );
  useEffect(() => {
    if (lastRuntimeEvent !== null) {
      qc.invalidateQueries({ queryKey: ["voice-mode"] });
    }
  }, [lastRuntimeEvent, qc]);

  return {
    mode: q.data?.mode ?? "pipeline",
    realtimeAvailable: q.data?.realtime_available ?? false,
    // Distinguishes "the server SAID no realtime key" from "we never heard
    // back" (timeout/loading). Without it a failed status fetch showed the
    // false claim "Realtime needs an API key" and looked like a locked
    // toggle on a machine that merely had a slow/broken backend moment.
    statusKnown: q.isSuccess,
    activeProvider: q.data?.active_provider ?? null,
    activeProviderLabel: q.data?.active_provider_label ?? null,
    activeModel: q.data?.active_model ?? null,
    sessionActive: q.data?.session_active ?? false,
    activeSessionMode: q.data?.active_session_mode ?? null,
    activeSessionProvider: q.data?.active_session_provider ?? "",
    activeSessionModel: q.data?.active_session_model ?? "",
    transitioning: q.data?.transitioning ?? false,
    setMode: m.mutate,
    isLoading: q.isLoading,
    isSaving: m.isPending,
  };
}
