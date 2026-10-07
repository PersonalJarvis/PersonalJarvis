import { useCallback, useState } from "react";
import { useT } from "@/i18n";
import { startBrowserVoiceCall, stopBrowserVoiceCall } from "@/lib/browserVoiceCall";
import {
  fetchVoiceRuntimeState,
  requestVoiceCall,
  requestVoiceHangup,
} from "@/lib/voiceApi";
import { useEventStore, type VoiceState } from "@/store/events";
import { useHomeStore } from "@/store/home";

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

/** A start the host refused because it runs no speech pipeline (HTTP 503). */
function hostRunsNoPipeline(error: unknown): boolean {
  return (error as { status?: unknown } | null)?.status === 503;
}

/** Whether the host says this browser may hold the call itself. */
async function browserMayHoldTheCall(): Promise<boolean> {
  const runtime = await fetchVoiceRuntimeState();
  return runtime?.browserCall === true;
}

/**
 * The one start/stop path shared by every voice surface.
 *
 * Keeping the request and its user-visible failure handling here prevents the
 * toolbar button and the floating bubble from becoming two controls that look
 * alike but behave differently.
 *
 * A start asks the host first, exactly as the call hotkey does. Only when the
 * host has no speech pipeline at all (a VPS, `jarvis serve`) and says the
 * browser may hold the call does this browser open the call itself — the
 * microphone is the one in front of the person, not one the server lacks.
 */
export function useVoiceCall() {
  const t = useT();
  const voiceState = (useEventStore((store) => store.voiceState) ?? "idle") as VoiceState;
  const pushToast = useEventStore((store) => store.pushToast);
  const [requestBusy, setBusy] = useState(false);
  const selectionPending = useHomeStore((s) => s.voiceSelectionPending);
  const busy = requestBusy || selectionPending;
  const active = isVoiceActive(voiceState);

  const toggleCall = useCallback(async () => {
    if (busy || voiceState === "connecting") return;
    setBusy(true);
    try {
      if (active) {
        // A call this browser holds ends here; the host has nothing to hang up.
        if (!stopBrowserVoiceCall()) await requestVoiceHangup();
      } else {
        try {
          const { armed } = await requestVoiceCall();
          if (!armed) {
            pushToast("warning", t("agentic_grid.voice_bubble.start_failed"));
          }
        } catch (error) {
          if (
            !hostRunsNoPipeline(error) ||
            !(await browserMayHoldTheCall()) ||
            !startBrowserVoiceCall()
          ) {
            throw error;
          }
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
