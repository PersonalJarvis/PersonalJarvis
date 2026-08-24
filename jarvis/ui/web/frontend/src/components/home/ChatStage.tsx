import { useEffect, useLayoutEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { useEventStore, type ChatMessage } from "@/store/events";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ChatInput } from "@/components/ChatInput";
import { TurnSteps } from "@/components/home/TurnSteps";
import { Greeting } from "@/components/home/Greeting";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { prettyProviderName } from "@/lib/prettyProviderName";
import { VoiceThreadStage } from "@/components/home/VoiceThreadStage";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The chat stage — Jarvis with a keyboard.
 *
 * This is the SAME assistant the microphone talks to, on the same brain, with
 * the same tools, the same memory and the same conversation history. The only
 * difference from the voice stage next door is the input: here the person
 * types and reads instead of speaking and listening. Everything a spoken turn
 * can do — open an app, read the calendar, run a skill, ask back before a
 * consequential action — a typed turn does too, because it is literally the
 * same turn (jarvis/ui/desktop_app.py::_on_user_message → BrainManager).
 *
 * What it is NOT, and was between 2026-08-23 and 2026-08-24: a coding-agent
 * session that happened to run in the Jarvis folder, picked by provider,
 * model, reasoning effort and permission mode. Coding agents are a real thing
 * this app has — they are the Agentic IDE's sessions, where a workspace
 * folder is the whole point. On the front page that was the wrong assistant
 * wearing the right name (maintainer, 2026-08-24).
 *
 * The wiring, end to end:
 *   composer (components/ChatInput) sends a `message` frame on the app socket
 *   → MessageSent on the event bus
 *   → the brain answers, streaming its text as AssistantTextDelta("chat")
 *     and its progress as the reasoning steps this column shows live
 *   → the finished reply arrives as the assistant message and is stored in
 *     the thread, so the sidebar's history holds it like any other.
 *
 * A spoken session opened from that history is READ here, in
 * components/home/VoiceThreadStage: the same column, no composer, because a
 * spoken thread is continued by speaking.
 *
 * Scrolling follows the Claude app: when the person sends, their message is
 * brought to the TOP of the scroll area and the answer grows below it — the
 * eye stays where the new turn begins instead of chasing the bottom. A spacer
 * under the last turn makes that possible even when the turn is short; it
 * shrinks as the answer fills the viewport, so a finished answer never trails
 * a screen of blank paper. Nothing jumps on assistant output.
 */
export function ChatStage() {
  const t = useT();
  const messages = useEventStore((s) => s.messages);
  const chatThinking = useEventStore((s) => s.chatThinking);
  const activeThreadId = useEventStore((s) => s.activeThreadId);
  const isVoiceThread = useEventStore((s) => s.activeKind === "voice" && s.activeThreadId !== null);
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
    const turnHeight = spacer.getBoundingClientRect().top - anchor.getBoundingClientRect().top;
    const room = viewport.clientHeight - turnHeight - TURN_BOTTOM_PAD_PX;
    spacer.style.minHeight = `${Math.max(0, Math.round(room))}px`;
  };

  // React to the newest message: a user line is the start of a turn and gets
  // pinned to the top; anything else, with no turn in flight, keeps the old
  // "follow the end" behaviour (first render of a conversation). Switching
  // conversations resets the anchor: the new thread is read from its end, not
  // from wherever the previous turn left the scroll position.
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

  // The live turn grows without a new message (steps arrive, the answer
  // streams in), so the spacer also follows the column's own size where the
  // platform has ResizeObserver.
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

  // A spoken thread was opened from the history: read it here, in the column
  // the composer would otherwise own.
  if (isVoiceThread) return <VoiceThreadStage />;

  if (!hasContent) {
    return (
      <div
        className="flex min-h-0 flex-1 flex-col items-center"
        data-testid="chat-stage"
        data-empty="true"
      >
        <div className="flex w-full max-w-[760px] flex-1 flex-col justify-center gap-8 px-6 pb-20">
          <Greeting subtitle={t("home.chat_empty_title")} />
          <ChatInput />
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex min-h-0 flex-1 flex-col items-center"
      data-testid="chat-stage"
      data-empty="false"
    >
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
  // (":" , ".") a CSS selector would need escaping for.
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
 * The assistant turn still in progress: byline, the live steps, and the
 * answer as it is being written.
 *
 * The streaming text is the half that was missing while this column showed
 * only the steps: the brain publishes the answer as cumulative
 * AssistantTextDelta snapshots on the "chat" channel several times a second
 * (jarvis/core/text_stream.py), so a long reply reads as it arrives instead
 * of appearing at once when the turn ends. The store clears it the moment the
 * finished message lands, so the two never show the same words twice.
 *
 * Reads the store itself so the column does not re-render on every delta —
 * only this block does.
 */
function LiveTurn() {
  const steps = useEventStore((s) => s.thinkingSteps);
  const startedTs = useEventStore((s) => s.thinkingStartedTs);
  const liveText = useEventStore((s) => s.liveReply?.text ?? "");
  const elapsed = useElapsedMs(startedTs);
  return (
    <Turn live testId="chat-turn-live">
      <TurnSteps steps={steps} live durationMs={elapsed} />
      {liveText.trim() !== "" && <Prose text={liveText} testId="chat-live-text" />}
    </Turn>
  );
}

/**
 * One assistant turn: the byline that says who answered, and a hairline rail
 * everything the turn produced hangs off — the steps it took, then the answer.
 * The rail glows while the turn runs, so the block reads as one live unit
 * rather than a list of disconnected rows.
 *
 * The byline names the BRAIN, with its vendor's own mark: the provider and
 * model configured under Agents / API keys, which is what a typed turn runs
 * on. It is deliberately not the voice engine — that one only answers speech.
 */
function Turn({
  children,
  live = false,
  testId,
  messageId,
}: {
  children: React.ReactNode;
  live?: boolean;
  testId?: string;
  messageId?: string;
}) {
  const assistantName = useEventStore((s) => s.assistantName);
  const provider = useEventStore((s) => s.brainProvider);
  const model = useEventStore((s) => s.brainModel);
  const label = provider ? prettyProviderName(provider) : "";
  return (
    <div className="flex flex-col gap-1.5" data-testid={testId} data-message-id={messageId}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-1 font-mono text-[10px] uppercase tracking-[0.14em] text-primary">
        <span
          className={cn("h-1 w-1 rounded-full bg-primary", live && "motion-safe:animate-pulse")}
          aria-hidden
        />
        <span>{assistantName}</span>
        {provider && (
          <span className="inline-flex items-center gap-1.5 normal-case tracking-normal text-muted-foreground">
            <ProviderLogo providerId={provider} label={label} size="sm" />
            <span>{label}</span>
            {model && <span className="text-muted-foreground/70">· {model}</span>}
          </span>
        )}
      </div>
      <div className="relative flex flex-col gap-1.5 pl-4">
        <span
          aria-hidden
          data-testid="chat-turn-rail"
          className={cn(
            "absolute bottom-1 left-[3px] top-1 w-px rounded-full",
            live ? "agent-rail-live" : "bg-border",
          )}
        />
        {children}
      </div>
    </div>
  );
}

/**
 * An assistant answer, rendered as Markdown.
 *
 * Jarvis writes lists, short headings and the odd bit of code, and typed
 * answers are read rather than heard — so an answer is rendered, not printed
 * as source. Colours come from theme tokens (`prose-neutral` +
 * `dark:prose-invert`), so it reads correctly in light and dark. The person's
 * own lines stay plain text: they typed characters, not markup.
 */
function Prose({ text, testId }: { text: string; testId?: string }) {
  return (
    <div
      data-testid={testId}
      className={cn(
        "prose prose-neutral max-w-none text-[15px] leading-relaxed dark:prose-invert [overflow-wrap:anywhere]",
        "prose-p:my-2 prose-headings:font-display prose-headings:tracking-tight prose-h1:text-xl prose-h2:text-lg prose-h3:text-base",
        "prose-a:text-primary prose-a:no-underline hover:prose-a:underline",
        "prose-code:rounded prose-code:bg-muted/60 prose-code:px-1 prose-code:py-0.5 prose-code:font-mono prose-code:text-[0.85em] prose-code:font-normal prose-code:before:hidden prose-code:after:hidden",
        "prose-pre:my-2 prose-pre:border prose-pre:border-border prose-pre:bg-card/80 prose-pre:text-[13px]",
        "prose-li:my-0.5 prose-ul:my-2 prose-ol:my-2",
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

/**
 * One message in the column. The person's lines sit right in a quiet bubble;
 * the assistant's run flush-left with a byline and, when there is one, the
 * turn's steps ("Thought for Ns" + tool calls) above the answer — prose on
 * the page, not a bubble.
 */
function MessageRow({ message }: { message: ChatMessage }) {
  const assistantName = useEventStore((s) => s.assistantName);
  const liveTrace = useEventStore((s) => s.thinkingTraces[message.id]);
  // A replayed conversation carries its steps on the message itself; a reply
  // that happened in this session has them in the store's live map.
  const trace = liveTrace ?? message.trace;
  const isUser = message.role === "user";

  if (message.role === "system") {
    return (
      <div
        data-message-id={message.id}
        data-testid="chat-message-system"
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
    <Turn testId="chat-message-assistant" messageId={message.id}>
      {trace && <TurnSteps steps={trace.steps} durationMs={trace.durationMs} className="mb-1" />}
      <Prose text={message.content} />
    </Turn>
  );
}
