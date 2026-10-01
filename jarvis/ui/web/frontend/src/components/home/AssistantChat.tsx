import { lazy, Suspense } from "react";
import { Keyboard } from "lucide-react";

import { VoiceStage } from "@/components/home/VoiceStage";
import { useVoiceModeSwitch } from "@/components/home/assistantStatus";
import { fill, useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";

const ChatStage = lazy(() =>
  import("@/components/home/ChatStage").then((module) => ({ default: module.ChatStage })),
);

/**
 * The front page: ONE chat with the assistant (2026-10-01).
 *
 * Nothing but the conversation — no header row, no side card. An empty chat
 * is a short greeting over the composer in the middle of the page, the way
 * the Claude app opens; the composer itself carries every control,
 * including the round voice-mode button. Voice mode is a state of this
 * chat, not another page: it swaps the typed column for the spoken
 * transcript and the Jarvis bar, and one quiet button in the corner brings
 * the typing back.
 */
export function AssistantChat() {
  const t = useT();
  const surface = useHomeStore((s) => s.surface);

  return (
    <div className="relative flex min-h-0 flex-1 flex-col" data-testid="assistant-chat">
      {surface === "voice" && <BackToTyping />}
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
        <VoiceStage />
      )}
    </div>
  );
}

/** Voice mode's way out — ends a running call and shows the composer again. */
function BackToTyping() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const voice = useVoiceModeSwitch();
  return (
    <button
      type="button"
      onClick={voice.exit}
      data-testid="voice-mode-exit"
      title={fill(t("assistant_chat.voice_exit_hint"), { name: assistantName })}
      className="absolute right-4 top-3 z-10 flex h-9 items-center gap-2 rounded-full border border-border bg-card px-3.5 text-sm font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <Keyboard aria-hidden className="h-4 w-4 text-muted-foreground" />
      {t("assistant_chat.voice_exit")}
    </button>
  );
}
