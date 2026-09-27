import { lazy, Suspense } from "react";
import { useHomeStore } from "@/store/home";
import { HomeHeader } from "@/components/home/HomeHeader";
import { VoiceStage } from "@/components/home/VoiceStage";
import { useT } from "@/i18n";

const ChatStage = lazy(() =>
  import("@/components/home/ChatStage").then((module) => ({ default: module.ChatStage })),
);

/**
 * The front page — the "chats" section.
 *
 * One header for who is talking, and under it one of two stages chosen by the
 * `Voice | Chat` switch at the top of the sidebar (store/home.ts):
 *
 *   Voice — the Jarvis bar, centred, with the live transcript above it.
 *   Chat  — a plain typed chat: one centred column, composer at the bottom.
 *
 * Both talk to the same assistant through the same event bus and share one
 * history; the switch changes how you address it, not whom. Replaces the
 * mission deck as the front page (maintainer, 2026-08-23): a plain ground,
 * no instruments, nothing that has to be read before you can speak.
 */
export function HomeView() {
  const t = useT();
  const surface = useHomeStore((s) => s.surface);
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="home-view" data-surface={surface}>
      <HomeHeader />
      {surface === "chat" ? (
        <Suspense fallback={<div role="status" className="flex min-h-0 flex-1 items-center justify-center text-sm text-muted-foreground">{t("common.loading")}</div>}>
          <ChatStage />
        </Suspense>
      ) : <VoiceStage />}
    </div>
  );
}
