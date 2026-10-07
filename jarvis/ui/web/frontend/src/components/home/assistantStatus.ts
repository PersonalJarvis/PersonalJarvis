import { useCallback } from "react";

import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import type { TimelineItem } from "@/components/agentchat/reduce";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { bindVoiceChat, fetchVoiceChat } from "@/lib/agentChatApi";
import { transcriptFromMessages, type TranscriptLine } from "@/lib/homeTranscript";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";

/**
 * The chat on stage as the voice lane's lines: what was typed or said, and
 * the answers' prose. Tool rows, notices and errors stay in the chat view.
 */
export function transcriptFromTimeline(items: readonly TimelineItem[]): TranscriptLine[] {
  const messages: { role: string; content: string; ts: number }[] = [];
  for (const item of items) {
    if (item.type === "user") {
      messages.push({ role: "user", content: item.text, ts: item.tsMs });
    } else if (item.type === "turn") {
      const text = item.blocks
        .flatMap((block) => (block.kind === "text" ? [block.text] : []))
        .join("\n");
      messages.push({ role: "assistant", content: text, ts: item.startedMs });
    }
  }
  return transcriptFromMessages(messages);
}

/**
 * Entering and leaving the front-page chat's voice mode — the ONE path the
 * composer's round voice button and voice mode's "Back to typing" use.
 *
 * Entering shows the spoken transcript and opens the microphone through the
 * same start/stop request the Jarvis bar uses (useVoiceCall), so there is
 * still exactly one way a call begins. Leaving ends a running call: "back to
 * typing" with the microphone still open would be a hidden live mic.
 *
 * Voice mode continues the chat on stage. Before the call starts, the chat
 * is bound on the backend (`bindVoiceChat`): the call starts with its
 * history and its turns are filed into it. The lane opens with that chat's
 * lines, so talking reads as the next message, not as a new conversation.
 * A call from a blank page gets its own new chat; leaving voice mode opens it.
 * An archived voice chat opened from the history is continued as itself.
 */
export function useVoiceModeSwitch() {
  const surface = useHomeStore((s) => s.surface);
  const setSurface = useHomeStore((s) => s.setSurface);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const { active, busy, connecting, toggleCall } = useVoiceCall();
  const { connected } = useVoiceReadiness();
  const on = surface === "voice";

  const enter = useCallback(() => {
    const chat = useAgentChatStore.getState();
    const events = useEventStore.getState();
    // An archived voice chat on stage is continued as itself.
    const voiceThread = !chat.activeSessionId && events.activeKind === "voice" ? events.activeThreadId : null;
    if (voiceThread) useHomeStore.getState().setContinuedVoiceId(voiceThread);
    const startsCall = !active && !busy && !connecting && connected;
    // A call already running elsewhere keeps its own lane.
    if (!active && !connecting) {
      useHomeStore.getState().seedTranscript(
        voiceThread ? transcriptFromMessages(events.messages) : transcriptFromTimeline(chat.timeline.items),
      );
    }
    setSurface("voice");
    setActiveSection("chats");
    if (!startsCall) return;
    void bindVoiceChat(chat.activeSessionId, voiceThread)
      .catch((err: unknown) => {
        // The call still starts; it just falls back to the newest chat.
        console.info("Voice chat binding failed.", err);
      })
      .finally(() => void toggleCall());
  }, [active, busy, connected, connecting, setActiveSection, setSurface, toggleCall]);

  const exit = useCallback(() => {
    if (active && !busy) void toggleCall();
    setSurface("chat");
    if (useHomeStore.getState().freshVoicePending) return;
    if (useAgentChatStore.getState().activeSessionId) return;
    // A call from a blank page opened its chat on the backend: show it.
    void fetchVoiceChat()
      .then(({ session_id: sessionId }) => {
        const chat = useAgentChatStore.getState();
        if (!sessionId || chat.activeSessionId || useHomeStore.getState().freshVoicePending) return;
        chat.openSession(sessionId);
        void chat.loadSessions();
      })
      .catch((err: unknown) => {
        console.info("Voice chat lookup failed.", err);
      });
  }, [active, busy, setSurface, toggleCall]);

  return { on, live: active || connecting, busy, enter, exit, toggle: on ? exit : enter };
}
