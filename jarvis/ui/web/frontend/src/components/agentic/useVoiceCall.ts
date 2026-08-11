import { useCallback, useState } from "react";
import { useT } from "@/i18n";
import { requestVoiceCall, requestVoiceHangup } from "@/lib/voiceApi";
import { useEventStore, type VoiceState } from "@/store/events";

/**
 * Whether the next voice-control press should end the current conversation.
 * Paused still belongs to the open call; connecting is kept separate so the
 * controls can stay disabled while the transport negotiates.
 */
export function isVoiceActive(state: VoiceState): boolean {
  return (
    state === "listening" ||
    state === "thinking" ||
    state === "speaking" ||
    state === "paused"
  );
}

/**
 * The one start/stop path shared by every Agentic IDE voice surface.
 *
 * Keeping the request and its user-visible failure handling here prevents the
 * toolbar button and the floating bubble from becoming two controls that look
 * alike but behave differently.
 */
export function useVoiceCall() {
  const t = useT();
  const voiceState = (useEventStore((store) => store.voiceState) ?? "idle") as VoiceState;
  const pushToast = useEventStore((store) => store.pushToast);
  const [busy, setBusy] = useState(false);
  const active = isVoiceActive(voiceState);

  const toggleCall = useCallback(async () => {
    if (busy || voiceState === "connecting") return;
    setBusy(true);
    try {
      if (active) {
        await requestVoiceHangup();
      } else {
        const { armed } = await requestVoiceCall();
        if (!armed) {
          pushToast("warning", t("agentic_grid.voice_bubble.start_failed"));
        }
      }
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setBusy(false);
    }
  }, [active, busy, pushToast, t, voiceState]);

  return {
    active,
    busy,
    connecting: voiceState === "connecting",
    toggleCall,
    voiceState,
  };
}
