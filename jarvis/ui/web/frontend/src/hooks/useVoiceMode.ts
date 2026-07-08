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
    onSuccess: () => qc.invalidateQueries({ queryKey: ["voice-mode"] }),
  });
  return {
    mode: q.data?.mode ?? "pipeline",
    realtimeAvailable: q.data?.realtime_available ?? false,
    setMode: m.mutate,
    isLoading: q.isLoading,
    isSaving: m.isPending,
  };
}
