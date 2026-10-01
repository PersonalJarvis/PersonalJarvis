import { useCallback } from "react";

import { useAgentChat } from "@/components/agentchat/AgentChatStoreContext";
import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";

/**
 * What the assistant is doing right now, in one word and one hue — read by
 * the chat's top bar and the assistant card so the two never disagree.
 *
 * A voice call outranks the typed chat: while the microphone is open the
 * spoken state is the news. Otherwise a running typed turn says "is working".
 * Only a dead socket reads as offline — voice still warming up does not take
 * the typed chat down with it.
 */
export type AssistantTone = "ready" | "busy" | "live" | "offline" | "error";

export interface AssistantStatus {
  tone: AssistantTone;
  label: string;
}

export function useAssistantStatus(): AssistantStatus {
  const t = useT();
  const voiceState = useEventStore((s) => s.voiceState);
  const { connected, bootWarming } = useVoiceReadiness();
  const turnRunning = useAgentChat((s) => {
    const items = s.timeline.items;
    for (let i = items.length - 1; i >= 0; i -= 1) {
      const item = items[i];
      if (item.type === "turn") return item.status === "running";
    }
    return false;
  });

  if (!connected) {
    return {
      tone: "offline",
      label: bootWarming ? t("voice_state.booting") : t("assistant_chat.status_offline"),
    };
  }
  if (voiceState === "error") return { tone: "error", label: t("voice_state.error") };
  if (
    voiceState === "connecting" ||
    voiceState === "listening" ||
    voiceState === "thinking" ||
    voiceState === "speaking" ||
    voiceState === "paused"
  ) {
    return { tone: "live", label: t(`voice_state.${voiceState}`) };
  }
  if (turnRunning) return { tone: "busy", label: t("assistant_chat.status_working") };
  return { tone: "ready", label: t("assistant_chat.status_ready") };
}

/** The status dot's fill per tone — status hues only, from theme tokens. */
export const TONE_DOT: Record<AssistantTone, string> = {
  ready: "bg-success",
  busy: "bg-accent",
  live: "bg-accent",
  offline: "bg-muted-foreground",
  error: "bg-destructive",
};

/**
 * Entering and leaving the chat's voice mode — the ONE path the top bar, the
 * assistant card and the empty chat's invitation all use.
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
