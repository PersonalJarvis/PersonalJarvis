import { useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

type VoiceModeResp = { mode: string; realtime_available: boolean; active_provider: string | null };

export function useVoiceMode() {
  const qc = useQueryClient();
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

  return {
    mode: q.data?.mode ?? "pipeline",
    realtimeAvailable: q.data?.realtime_available ?? false,
    setMode: m.mutate,
    isLoading: q.isLoading,
    isSaving: m.isPending,
  };
}
