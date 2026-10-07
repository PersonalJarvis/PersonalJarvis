import { lazy, Suspense, useEffect } from "react";

import { VoiceStage } from "@/components/home/VoiceStage";
import { transcriptFromTimeline, useVoiceModeSwitch } from "@/components/home/assistantStatus";
import { useT } from "@/i18n";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";

const ChatStage = lazy(() =>
  import("@/components/home/ChatStage").then((module) => ({ default: module.ChatStage })),
);
// An agent's own chat, opened from the sidebar — society code, its own chunk.
const HomeAgentChat = lazy(() => import("@/components/home/HomeAgentChat"));

/**
 * The front page: ONE chat with the assistant (2026-10-01).
 *
 * Nothing but the conversation — no header row, no side card. An empty chat
 * is a short greeting over the composer in the middle of the page;
 * the composer itself carries every control,
 * including the round voice-mode button. Voice mode is a state of this
 * chat, not another page: it swaps the typed composer for the voice
 * composer under the spoken transcript (components/home/VoiceStage), whose
 * keyboard button brings the typing back.
 */
export function AssistantChat() {
  const t = useT();
  const surface = useHomeStore((s) => s.surface);
  const agentChatId = useHomeStore((s) => s.agentChatId);
  const voice = useVoiceModeSwitch();
  const chatItems = useAgentChatStore((s) => s.timeline.items);
  const seedTranscript = useHomeStore((s) => s.seedTranscript);
  const inVoice = surface === "voice" && !agentChatId;
  const freshVoicePending = useHomeStore((s) => s.freshVoicePending);
  // An explicitly selected archived voice chat owns the lane.
  const voiceThreadOpen = useEventStore((s) => s.activeKind === "voice" && Boolean(s.activeThreadId));
  // Only a selected chat may seed the idle lane. After hangup, delayed chat
  // updates must not put the completed conversation back on the blank page.
  useEffect(() => {
    if (!inVoice || voice.live || voiceThreadOpen || freshVoicePending) return;
    seedTranscript(transcriptFromTimeline(chatItems));
  }, [chatItems, freshVoicePending, inVoice, seedTranscript, voice.live, voiceThreadOpen]);
  const loading = (
    <div role="status" className="flex min-h-0 flex-1 items-center justify-center text-sm text-muted-foreground">
      {t("common.loading")}
    </div>
  );

  if (agentChatId) {
    return (
      <div className="relative flex min-h-0 flex-1 flex-col" data-testid="assistant-chat" data-agent={agentChatId}>
        <Suspense fallback={loading}>
          <HomeAgentChat agentId={agentChatId} />
        </Suspense>
      </div>
    );
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col" data-testid="assistant-chat">
      {surface === "chat" ? (
        <Suspense
          fallback={
            <div role="status" className="flex min-h-0 flex-1 items-center justify-center text-sm text-muted-foreground">
              {t("common.loading")}
            </div>
          }
        >
          <ChatStage />
        </Suspense>
      ) : (
        <VoiceStage onExit={voice.exit} />
      )}
    </div>
  );
}
