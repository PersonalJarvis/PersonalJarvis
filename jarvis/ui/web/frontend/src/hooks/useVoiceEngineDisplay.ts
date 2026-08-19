import { useMemo } from "react";

import { useVoiceMode } from "@/hooks/useVoiceMode";
import {
  resolveVoiceEngineDisplay,
  type VoiceEngineDisplay,
} from "@/lib/voiceEngineDisplay";
import { useEventStore } from "@/store/events";

/**
 * The provider + model the current voice engine will use on the next spoken
 * turn. Same rule as the sidebar footer: realtime names the realtime pick
 * (and a live session outranks it); pipeline names the classic brain.
 */
export function useVoiceEngineDisplay(): VoiceEngineDisplay {
  const voiceMode = useVoiceMode();
  const brainProvider = useEventStore((s) => s.brainProvider);
  const brainModel = useEventStore((s) => s.brainModel);
  return useMemo(
    () =>
      resolveVoiceEngineDisplay({
        mode: voiceMode.mode,
        activeProvider: voiceMode.activeProvider,
        activeProviderLabel: voiceMode.activeProviderLabel,
        activeModel: voiceMode.activeModel,
        sessionActive: voiceMode.sessionActive,
        activeSessionMode: voiceMode.activeSessionMode,
        activeSessionProvider: voiceMode.activeSessionProvider,
        activeSessionModel: voiceMode.activeSessionModel,
        brainProvider,
        brainModel,
      }),
    [
      voiceMode.mode,
      voiceMode.activeProvider,
      voiceMode.activeProviderLabel,
      voiceMode.activeModel,
      voiceMode.sessionActive,
      voiceMode.activeSessionMode,
      voiceMode.activeSessionProvider,
      voiceMode.activeSessionModel,
      brainProvider,
      brainModel,
    ],
  );
}
