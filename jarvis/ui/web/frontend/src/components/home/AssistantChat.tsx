import { lazy, Suspense } from "react";

import { VoiceStage } from "@/components/home/VoiceStage";
import { useVoiceModeSwitch } from "@/components/home/assistantStatus";
import { useT } from "@/i18n";
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
 * is a short greeting over the composer in the middle of the page, the way
 * the Claude app opens; the composer itself carries every control,
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
