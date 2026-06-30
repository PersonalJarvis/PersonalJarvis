import { useEventStore } from "@/store/events";

export interface VoiceReadiness {
  /**
   * The user cannot speak yet: either the socket is up but the voice stack is
   * still warming (`connected && !voiceReady`), or the fast-boot backend is
   * still binding (`!connected && wsWarming`).
   */
  warming: boolean;
  /** Socket up AND the voice stack can really hear + speak. */
  ready: boolean;
  /** Welcome-gated socket connection (typing is allowed once this is true). */
  connected: boolean;
  /** Connected but the voice stack is not ready yet (drives "Voice starting…"). */
  voiceWarming: boolean;
  /** Socket not up yet but the fast-boot backend is warming (drives "Starting…"). */
  bootWarming: boolean;
}

/**
 * Single source of truth for "can the user speak yet?".
 *
 * Before 2026-06-29 the Sidebar status line, the VoiceWarmingBanner and the
 * ChatsView empty-state each derived readiness on their own — and the
 * empty-state ignored it entirely, so the banner could honestly say "starting
 * up" while the centre of the screen claimed "Ready for commands". Centralising
 * the one true derivation here keeps every readiness surface in sync.
 *
 * The `warming` expression is the union the banner and sidebar already used:
 *   (connected && !voiceReady) || (!connected && wsWarming)
 */
export function useVoiceReadiness(): VoiceReadiness {
  const connected = useEventStore((s) => s.connected);
  const wsWarming = useEventStore((s) => s.wsWarming);
  const voiceReady = useEventStore((s) => s.voiceReady);

  const voiceWarming = connected && !voiceReady;
  const bootWarming = !connected && wsWarming;
  const warming = voiceWarming || bootWarming;

  return {
    warming,
    ready: connected && voiceReady,
    connected,
    voiceWarming,
    bootWarming,
  };
}
