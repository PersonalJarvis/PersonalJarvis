import { useCallback } from "react";

import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";

/**
 * Entering and leaving the front-page chat's voice mode — the ONE path the
 * composer's round voice button and voice mode's "Back to typing" use.
 *
 * Entering shows the spoken transcript and opens the microphone through the
 * same start/stop request the Jarvis bar uses (useVoiceCall), so there is
 * still exactly one way a call begins. Leaving ends a running call: "back to
 * typing" with the microphone still open would be a hidden live mic.
 */
export function useVoiceModeSwitch() {
  const surface = useHomeStore((s) => s.surface);
  const setSurface = useHomeStore((s) => s.setSurface);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const { active, busy, connecting, toggleCall } = useVoiceCall();
  const { connected } = useVoiceReadiness();
  const on = surface === "voice";

  const enter = useCallback(() => {
    setSurface("voice");
    setActiveSection("chats");
    if (!active && !busy && !connecting && connected) void toggleCall();
  }, [active, busy, connected, connecting, setActiveSection, setSurface, toggleCall]);

  const exit = useCallback(() => {
    if (active && !busy) void toggleCall();
    setSurface("chat");
  }, [active, busy, setSurface, toggleCall]);

  return { on, live: active || connecting, busy, enter, exit, toggle: on ? exit : enter };
}
