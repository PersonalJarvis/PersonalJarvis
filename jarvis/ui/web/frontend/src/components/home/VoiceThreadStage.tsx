import { useLayoutEffect, useMemo } from "react";
import { AudioLines, Mic } from "lucide-react";

import { useEventStore, type ChatMessage } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ScrollToEndButton } from "@/components/ui/scroll-to-end-button";
import { useStickToBottom } from "@/hooks/useStickToBottom";
import { ThoughtTraceDisclosure } from "@/components/ThinkingTrace";
import { transcriptFromMessages } from "@/lib/homeTranscript";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * A spoken conversation, read on the chat surface.
 *
 * The sidebar's history mixes voice sessions with agent chats, so a click
 * has to land somewhere for BOTH. An agent chat opens in the agent timeline
 * next door; a voice session opens here — the same centred column, the words
 * as they were said, the reasoning steps that were kept, and no composer,
 * because a spoken thread is continued by speaking. Until this existed,
 * clicking a voice row while the chat surface was up changed nothing on
 * screen: the words were loaded into a store the stage did not read.
 *
 * "Continue by voice" hands the thread to the voice stage with its words
 * already in the lane — the same seeding the sidebar does when you open a
 * voice session while standing on that stage.
 */
export function VoiceThreadStage() {
  const t = useT();
  const messages = useEventStore((s) => s.messages);
  const activeThreadId = useEventStore((s) => s.activeThreadId);
  const conversations = useEventStore((s) => s.conversations);
  const setSurface = useHomeStore((s) => s.setSurface);
  const seedTranscript = useHomeStore((s) => s.seedTranscript);

  const title = useMemo(
    () => conversations.find((c) => c.kind === "voice" && c.id === activeThreadId)?.title ?? "",
    [activeThreadId, conversations],
  );

  // A reopened thread lands at its end, where the conversation stopped — but
  // a reader who has scrolled up into it stays there when the rest of a long
  // thread finishes loading (hooks/useStickToBottom).
  const { rootRef, contentRef, atEnd, jumpToEnd, follow } = useStickToBottom();
  useLayoutEffect(follow, [follow, activeThreadId, messages.length]);

  const continueByVoice = () => {
    seedTranscript(transcriptFromMessages(messages));
    setSurface("voice");
  };

  return (
    <div
      className="flex min-h-0 flex-1 flex-col items-center"
      data-testid="voice-thread-stage"
      data-thread={activeThreadId ?? ""}
    >
      <ScrollArea ref={rootRef} className="min-h-0 w-full flex-1">
        <div
          ref={contentRef}
          className="mx-auto flex w-full max-w-[760px] flex-col gap-4 px-6 pb-6 pt-8"
        >
          <header className="flex items-center gap-2.5 border-b border-border pb-3">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-secondary/50">
              <Mic aria-hidden className="h-3.5 w-3.5 text-primary" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate font-display text-sm font-semibold tracking-tight text-foreground">
                {title || t("voice_thread.untitled")}
              </span>
              <span className="block text-[11px] text-muted-foreground">
                {fill(t("voice_thread.subtitle"), { count: String(messages.length) })}
              </span>
            </span>
          </header>

          {messages.length === 0 ? (
            <p className="py-10 text-center text-xs text-muted-foreground">
              {t("voice_thread.empty")}
            </p>
          ) : (
            messages.map((m) => <SpokenTurn key={m.id} message={m} />)
          )}
        </div>
      </ScrollArea>

      <div className="relative w-full max-w-[760px] px-6 pb-5 pt-2">
        {messages.length > 0 && !atEnd && <ScrollToEndButton onClick={jumpToEnd} />}
        <div className="flex items-center gap-3 rounded-2xl border border-border bg-card/60 px-4 py-3">
          <p className="min-w-0 flex-1 text-xs leading-relaxed text-muted-foreground">
            {t("voice_thread.read_only")}
          </p>
          <button
            type="button"
            onClick={continueByVoice}
            data-testid="continue-by-voice"
            className={cn(
              "flex shrink-0 items-center gap-2 rounded-xl border border-primary/40 bg-primary/10 px-3 py-1.5",
              "text-xs font-medium text-primary transition-colors hover:bg-primary/20",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
            )}
          >
            <AudioLines aria-hidden className="h-3.5 w-3.5" />
            {t("voice_thread.continue")}
          </button>
        </div>
      </div>
    </div>
  );
}

/** One spoken turn — heard words on the right, the answer on the left. */
function SpokenTurn({ message }: { message: ChatMessage }) {
  const assistantName = useEventStore((s) => s.assistantName);
  const trace = useEventStore((s) => s.thinkingTraces[message.id]);
  const isUser = message.role === "user";

  if (message.role === "system") {
    return (
      <div className="mx-auto max-w-[85%] rounded-lg border border-border px-3 py-2 text-center text-xs italic text-muted-foreground">
        {message.content}
      </div>
    );
  }

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] px-4 py-3 text-sm leading-relaxed",
          isUser
            ? "rounded-2xl rounded-br-sm bg-foreground/70 text-primary-foreground"
            : "jarvis-message-surface rounded-2xl rounded-bl-sm border border-border text-foreground",
        )}
      >
        {!isUser && (
          <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-primary">
            <span aria-hidden className="h-1 w-1 rounded-full bg-foreground/70" />
            {assistantName}
          </div>
        )}
        {!isUser && trace && <ThoughtTraceDisclosure trace={trace} />}
        <div className={cn("whitespace-pre-wrap", !isUser && "mt-1.5")}>{message.content}</div>
      </div>
    </div>
  );
}
