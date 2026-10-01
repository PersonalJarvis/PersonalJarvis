import { lazy, Suspense, useCallback, useState } from "react";

import { AssistantProfilePanel } from "@/components/home/AssistantProfilePanel";
import { ChatTopBar } from "@/components/home/ChatTopBar";
import { VoiceStage } from "@/components/home/VoiceStage";
import { useT } from "@/i18n";
import { useHomeStore } from "@/store/home";

const ChatStage = lazy(() =>
  import("@/components/home/ChatStage").then((module) => ({ default: module.ChatStage })),
);

const PANEL_KEY = "jarvis.home.assistant-panel.v1";

function readPanelOpen(): boolean {
  try {
    return window.localStorage.getItem(PANEL_KEY) !== "closed";
  } catch {
    return true;
  }
}

function writePanelOpen(open: boolean): void {
  try {
    window.localStorage.setItem(PANEL_KEY, open ? "open" : "closed");
  } catch {
    /* not remembering the choice must not stop the panel from toggling */
  }
}

/**
 * The front page: ONE chat with the assistant (2026-10-01).
 *
 * A top bar (who, what they are doing, the voice-mode button), the
 * conversation under it, and the assistant card on the right on wide
 * windows. Voice mode is a state of this chat, not another page: it swaps
 * the typed column for the spoken transcript and the Jarvis bar, and the
 * same button brings the typing back. The card's open/closed choice is
 * remembered per browser.
 */
export function AssistantChat() {
  const t = useT();
  const surface = useHomeStore((s) => s.surface);
  const [panelOpen, setPanelOpen] = useState(readPanelOpen);
  const togglePanel = useCallback(() => {
    setPanelOpen((open) => {
      writePanelOpen(!open);
      return !open;
    });
  }, []);

  return (
    <div className="flex min-h-0 flex-1" data-testid="assistant-chat">
      <div className="flex min-w-0 flex-1 flex-col">
        <ChatTopBar panelOpen={panelOpen} onTogglePanel={togglePanel} />
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
      {panelOpen && <AssistantProfilePanel onClose={togglePanel} />}
    </div>
  );
}
