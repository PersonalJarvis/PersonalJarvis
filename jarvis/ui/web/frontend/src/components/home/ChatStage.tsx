import { useEffect, useRef } from "react";

import { useEventStore, type ChatMessage } from "@/store/events";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ChatInput } from "@/components/ChatInput";
import { ThinkingTrace, ThoughtTraceDisclosure } from "@/components/ThinkingTrace";
import { Greeting } from "@/components/home/Greeting";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The chat stage — the typed half of the front page.
 *
 * One centred column, like a document: the greeting and the composer sit
 * in the middle of an empty page; once there are messages the column
 * scrolls and the composer docks to the bottom. No second pane — the
 * history moved into the sidebar (components/home/RecentChats), where it is
 * visible from every section instead of only this one.
 *
 * The composer is the one the classic view used (ChatInput): same send
 * path, same dictation, same thread routing. Only the room around it
 * changed.
 */
export function ChatStage() {
  const t = useT();
  const messages = useEventStore((s) => s.messages);
  const chatThinking = useEventStore((s) => s.chatThinking);
  const endRef = useRef<HTMLDivElement | null>(null);
  const hasContent = messages.length > 0 || chatThinking;

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, chatThinking]);

  if (!hasContent) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center" data-testid="chat-stage" data-empty="true">
        <div className="flex w-full max-w-[760px] flex-1 flex-col justify-center gap-8 px-6 pb-20">
          <Greeting subtitle={t("home.chat_empty_title")} />
          <ChatInput />
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center" data-testid="chat-stage" data-empty="false">
      <ScrollArea className="min-h-0 w-full flex-1">
        <div className="mx-auto flex w-full max-w-[760px] flex-col gap-5 px-6 pb-6 pt-8">
          {messages.map((m) => (
            <MessageRow key={m.id} message={m} />
          ))}
          {chatThinking && <ThinkingTrace />}
          <div ref={endRef} />
        </div>
      </ScrollArea>
      <div className="w-full max-w-[760px] px-6 pb-5 pt-2">
        <ChatInput />
      </div>
    </div>
  );
}

/**
 * One message in the column. The person's lines sit right in a quiet
 * bubble; the assistant's run flush-left with a byline and, when there is
 * one, the "Thought for Ns" disclosure — prose on the page, not a bubble.
 */
function MessageRow({ message }: { message: ChatMessage }) {
  const assistantName = useEventStore((s) => s.assistantName);
  const trace = useEventStore((s) => s.thinkingTraces[message.id]);
  const isUser = message.role === "user";

  if (message.role === "system") {
    return (
      <div className="mx-auto max-w-[85%] rounded-lg border border-border bg-secondary/50 px-3 py-2 text-center text-xs italic text-muted-foreground">
        {message.content}
      </div>
    );
  }

  if (message.role === "preamble") {
    return (
      <div className="flex flex-col gap-1 text-xs italic leading-relaxed text-muted-foreground">
        <span className="font-mono text-[10px] not-italic uppercase tracking-[0.14em] text-muted-foreground">
          {assistantName} · pre-ack
        </span>
        <div className="whitespace-pre-wrap">{message.content}</div>
      </div>
    );
  }

  if (isUser) {
    return (
      <div className="flex justify-end" data-testid="chat-message-user">
        <div className="max-w-[78%] rounded-2xl rounded-br-md border border-border bg-secondary px-4 py-2.5 text-[15px] leading-relaxed text-foreground">
          <div className="whitespace-pre-wrap">{message.content}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5 px-1" data-testid="chat-message-assistant">
      <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-primary">
        <span className="h-1 w-1 rounded-full bg-primary" aria-hidden />
        {assistantName}
      </div>
      {trace && <ThoughtTraceDisclosure trace={trace} />}
      <div className={cn("whitespace-pre-wrap text-[15px] leading-relaxed text-foreground")}>
        {message.content}
      </div>
    </div>
  );
}
