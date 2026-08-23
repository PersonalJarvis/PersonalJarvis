import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { useEventStore, type ChatMessage } from "@/store/events";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ChatInput } from "@/components/ChatInput";
import { TurnSteps } from "@/components/home/TurnSteps";
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
 *
 * Scrolling follows the Claude app: when the person sends, their message
 * is brought to the TOP of the scroll area and the answer grows below it —
 * the eye stays where the new turn begins instead of chasing the bottom.
 * A spacer under the last turn makes that possible even when the turn is
 * short; it shrinks as the answer fills the viewport, so a finished answer
 * never trails a screen of blank paper. Nothing jumps on assistant output.
 */
export function ChatStage() {
  const t = useT();
  const messages = useEventStore((s) => s.messages);
  const chatThinking = useEventStore((s) => s.chatThinking);
  const activeThreadId = useEventStore((s) => s.activeThreadId);
  const hasContent = messages.length > 0 || chatThinking;

  const rootRef = useRef<HTMLDivElement | null>(null);
  const columnRef = useRef<HTMLDivElement | null>(null);
  const spacerRef = useRef<HTMLDivElement | null>(null);
  // The user message pinned to the top of the scroll area for the current
  // turn. Null until the person sends in this mounted column — opening an
  // old conversation lands at its end like before, with no spacer.
  const anchorIdRef = useRef<string | null>(null);
  const lastMessageIdRef = useRef<string | null>(null);
  const threadRef = useRef<string | null>(activeThreadId);

  // Refs, not state, on purpose: the spacer must be sized BEFORE the scroll
  // that relies on it, in the same layout pass — a state round-trip would
  // scroll first and grow the page afterwards, clamping the scroll short.
  const applySpacer = () => {
    const viewport = viewportOf(rootRef.current);
    const spacer = spacerRef.current;
    if (!viewport || !spacer) return;
    const anchor = anchorIdRef.current ? messageElement(viewport, anchorIdRef.current) : null;
    if (!anchor) {
      spacer.style.minHeight = "0px";
      return;
    }
    // Everything from the anchored message down to the spacer is the turn.
    const turnHeight =
      spacer.getBoundingClientRect().top - anchor.getBoundingClientRect().top;
    const room = viewport.clientHeight - turnHeight - TURN_BOTTOM_PAD_PX;
    spacer.style.minHeight = `${Math.max(0, Math.round(room))}px`;
  };

  // React to the newest message: a user line is the start of a turn and
  // gets pinned to the top; anything else, with no turn in flight, keeps
  // the old "follow the end" behaviour (first render of a conversation).
  // Switching conversations resets the anchor: the new thread is read from
  // its end, not from wherever the previous turn left the scroll position.
  useLayoutEffect(() => {
    if (threadRef.current !== activeThreadId) {
      threadRef.current = activeThreadId;
      anchorIdRef.current = null;
      lastMessageIdRef.current = null;
    }
    const viewport = viewportOf(rootRef.current);
    const last = messages[messages.length - 1];
    const lastId = last?.id ?? null;
    const isNew = lastId !== lastMessageIdRef.current;
    lastMessageIdRef.current = lastId;
    if (!viewport || !last) return;
    if (isNew && last.role === "user") {
      anchorIdRef.current = last.id;
      applySpacer();
      scrollMessageToTop(viewport, last.id);
      return;
    }
    applySpacer();
    if (isNew && anchorIdRef.current === null) scrollToEnd(viewport);
    // `applySpacer` reads refs only and is recreated per render by design.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, activeThreadId, chatThinking]);

  // The live steps grow without re-rendering this column, so the spacer
  // also follows the column's size where the platform has ResizeObserver.
  useEffect(() => {
    const column = columnRef.current;
    const viewport = viewportOf(rootRef.current);
    if (!column || !viewport || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => applySpacer());
    ro.observe(column);
    ro.observe(viewport);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasContent]);

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
      <ScrollArea ref={rootRef} className="min-h-0 w-full flex-1">
        <div
          ref={columnRef}
          className="relative mx-auto flex w-full max-w-[760px] flex-col gap-5 px-6 pb-6 pt-8"
        >
          {messages.map((m) => (
            <MessageRow key={m.id} message={m} />
          ))}
          {chatThinking && <LiveTurn />}
          <div ref={spacerRef} aria-hidden data-testid="chat-bottom-spacer" className="shrink-0" />
        </div>
      </ScrollArea>
      <div className="w-full max-w-[760px] px-6 pb-5 pt-2">
        <ChatInput />
      </div>
    </div>
  );
}

/** Room kept under the turn so the composer's shadow never kisses the text. */
const TURN_BOTTOM_PAD_PX = 24;

/** Radix renders the scrolling element as the viewport inside our ScrollArea root. */
function viewportOf(root: HTMLElement | null): HTMLElement | null {
  if (!root) return null;
  return (root.querySelector("[data-radix-scroll-area-viewport]") as HTMLElement | null) ?? root;
}

function messageElement(viewport: HTMLElement, id: string): HTMLElement | null {
  // Attribute compare instead of a selector: message ids carry characters
  // (":" , "." ) a CSS selector would need escaping for.
  for (const el of Array.from(viewport.querySelectorAll<HTMLElement>("[data-message-id]"))) {
    if (el.dataset.messageId === id) return el;
  }
  return null;
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
}

function scrollViewport(viewport: HTMLElement, top: number) {
  const behavior: ScrollBehavior = prefersReducedMotion() ? "auto" : "smooth";
  if (typeof viewport.scrollTo === "function") viewport.scrollTo({ top, behavior });
  else viewport.scrollTop = top;
}

/** Bring the message with `id` to the top edge of the viewport (plus a breath of padding). */
function scrollMessageToTop(viewport: HTMLElement, id: string) {
  const el = messageElement(viewport, id);
  if (!el) return;
  const top =
    el.getBoundingClientRect().top - viewport.getBoundingClientRect().top + viewport.scrollTop - 12;
  scrollViewport(viewport, Math.max(0, top));
}

function scrollToEnd(viewport: HTMLElement) {
  scrollViewport(viewport, viewport.scrollHeight);
}

/** Elapsed wall-clock since `startedTs`, ticking once a second while set. */
function useElapsedMs(startedTs: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedTs === null) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [startedTs]);
  return startedTs === null ? 0 : Math.max(0, now - startedTs);
}

/**
 * The assistant turn still in progress: byline plus the live steps. Reads
 * the store itself so the column does not re-render on every step event —
 * only this block does.
 */
function LiveTurn() {
  const assistantName = useEventStore((s) => s.assistantName);
  const steps = useEventStore((s) => s.thinkingSteps);
  const startedTs = useEventStore((s) => s.thinkingStartedTs);
  const elapsed = useElapsedMs(startedTs);
  return (
    <div className="flex flex-col gap-1.5 px-1" data-testid="chat-turn-live">
      <Byline name={assistantName} />
      <TurnSteps steps={steps} live durationMs={elapsed} />
    </div>
  );
}

function Byline({ name }: { name: string }) {
  return (
    <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-primary">
      <span className="h-1 w-1 rounded-full bg-primary" aria-hidden />
      {name}
    </div>
  );
}

/**
 * One message in the column. The person's lines sit right in a quiet
 * bubble; the assistant's run flush-left with a byline and, when there is
 * one, the turn's steps ("Thought for Ns" + tool calls) above the answer —
 * prose on the page, not a bubble.
 */
function MessageRow({ message }: { message: ChatMessage }) {
  const assistantName = useEventStore((s) => s.assistantName);
  const trace = useEventStore((s) => s.thinkingTraces[message.id]);
  const isUser = message.role === "user";

  if (message.role === "system") {
    return (
      <div
        data-message-id={message.id}
        className="mx-auto max-w-[85%] rounded-lg border border-border bg-secondary/50 px-3 py-2 text-center text-xs italic text-muted-foreground"
      >
        {message.content}
      </div>
    );
  }

  if (message.role === "preamble") {
    return (
      <div
        data-message-id={message.id}
        className="flex flex-col gap-1 text-xs italic leading-relaxed text-muted-foreground"
      >
        <span className="font-mono text-[10px] not-italic uppercase tracking-[0.14em] text-muted-foreground">
          {assistantName} · pre-ack
        </span>
        <div className="whitespace-pre-wrap">{message.content}</div>
      </div>
    );
  }

  if (isUser) {
    return (
      <div className="flex justify-end" data-testid="chat-message-user" data-message-id={message.id}>
        <div className="max-w-[78%] rounded-2xl rounded-br-md border border-border bg-secondary px-4 py-2.5 text-[15px] leading-relaxed text-foreground">
          <div className="whitespace-pre-wrap">{message.content}</div>
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex flex-col gap-1.5 px-1"
      data-testid="chat-message-assistant"
      data-message-id={message.id}
    >
      <Byline name={assistantName} />
      {trace && <TurnSteps steps={trace.steps} durationMs={trace.durationMs} className="mb-1" />}
      <div className={cn("whitespace-pre-wrap text-[15px] leading-relaxed text-foreground")}>
        {message.content}
      </div>
    </div>
  );
}
