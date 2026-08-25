import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from "react";

import { useEventStore } from "@/store/events";
import { useAgentChat } from "@/components/agentchat/AgentChatStoreContext";
import { ScrollArea } from "@/components/ui/scroll-area";
import { AgentComposer } from "@/components/agentchat/AgentComposer";
import { AgentTimeline } from "@/components/agentchat/AgentTimeline";
import { Greeting } from "@/components/home/Greeting";
import { VoiceThreadStage } from "@/components/home/VoiceThreadStage";
import type { ApprovalDecision } from "@/lib/agentChatApi";
import { fill, useT } from "@/i18n";
import { folderLeaf } from "@/lib/folderPath";
import { FolderCode } from "lucide-react";

/**
 * The chat stage — the typed half of the front page: Jarvis with a keyboard.
 *
 * What is typed here goes to the same assistant the microphone reaches —
 * the same memory, the same tools, the same voice in the answers — carried
 * by an agent-chat session on the `jarvis` surface (jarvis/agent_chat). The
 * composer's picks — provider, model, reasoning effort, permission mode —
 * decide what Jarvis runs on for THIS chat; they never reach the voice
 * path (AP-9: nothing here touches it). The Agentic IDE lists its coding
 * sessions on its own surface (`agent`), so none of them appear here.
 *
 * One centred column, like a document: the greeting and the composer sit
 * in the middle of an empty page; once there are messages the column
 * scrolls and the composer docks to the bottom. The history is the
 * sidebar's (components/home/RecentChats), visible from every section.
 *
 * One conversation is on stage at a time. The sidebar's history mixes agent
 * chats with VOICE sessions, and a voice session opened from here is read in
 * components/home/VoiceThreadStage — the same column, no composer. Which of
 * the two shows is decided by the event store's active thread: opening either
 * kind clears the other (components/home/chatRows), so the two can never both
 * claim the stage and leave a click looking like it did nothing.
 *
 * Scrolling follows the Claude app: when the person sends, their message
 * is brought to the TOP of the scroll area and the answer grows below it —
 * the eye stays where the new turn begins instead of chasing the bottom.
 * A spacer under the last turn makes that possible even when the turn is
 * short; it shrinks as the answer fills the viewport. Nothing jumps on
 * assistant output.
 */
export function ChatStage() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const surface = useAgentChat((s) => s.surface);
  const items = useAgentChat((s) => s.timeline.items);
  const cwd = useAgentChat((s) => s.draft.cwd);
  const activeSessionId = useAgentChat((s) => s.activeSessionId);
  const catalog = useAgentChat((s) => s.catalog);
  const decide = useAgentChat((s) => s.decide);
  const loadCatalog = useAgentChat((s) => s.loadCatalog);
  const loadSessions = useAgentChat((s) => s.loadSessions);
  const voiceThreadId = useEventStore((s) => (s.activeKind === "voice" ? s.activeThreadId : null));
  const hasContent = items.length > 0;

  useEffect(() => {
    if (!catalog) void loadCatalog();
    void loadSessions();
  }, [catalog, loadCatalog, loadSessions]);

  const providerLabel = useCallback(
    (id: string) => catalog?.providers.find((p) => p.id === id)?.label ?? id,
    [catalog],
  );
  const onDecide = useCallback(
    (approvalId: string, decision: ApprovalDecision) => void decide(approvalId, decision),
    [decide],
  );

  const rootRef = useRef<HTMLDivElement | null>(null);
  const columnRef = useRef<HTMLDivElement | null>(null);
  const spacerRef = useRef<HTMLDivElement | null>(null);
  // The user message pinned to the top of the scroll area for the current
  // turn. Null until the person sends in this mounted column — opening an
  // old conversation lands at its end like before, with no spacer.
  const anchorIdRef = useRef<string | null>(null);
  const lastItemIdRef = useRef<string | null>(null);
  const sessionRef = useRef<string | null>(activeSessionId);

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
    const turnHeight = spacer.getBoundingClientRect().top - anchor.getBoundingClientRect().top;
    const room = viewport.clientHeight - turnHeight - TURN_BOTTOM_PAD_PX;
    spacer.style.minHeight = `${Math.max(0, Math.round(room))}px`;
  };

  const lastItem = items[items.length - 1];
  const lastItemId = lastItem?.id ?? null;
  const lastItemIsUser = lastItem?.type === "user";

  useLayoutEffect(() => {
    if (sessionRef.current !== activeSessionId) {
      sessionRef.current = activeSessionId;
      anchorIdRef.current = null;
      lastItemIdRef.current = null;
    }
    const viewport = viewportOf(rootRef.current);
    const isNew = lastItemId !== lastItemIdRef.current;
    lastItemIdRef.current = lastItemId;
    if (!viewport || !lastItemId) return;
    if (isNew && lastItemIsUser) {
      anchorIdRef.current = lastItemId;
      applySpacer();
      scrollMessageToTop(viewport, lastItemId);
      return;
    }
    applySpacer();
    if (isNew && anchorIdRef.current === null) scrollToEnd(viewport);
    // `applySpacer` reads refs only and is recreated per render by design.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastItemId, lastItemIsUser, activeSessionId, items.length]);

  // The live blocks grow without a new item; the spacer follows the
  // column's size where the platform has ResizeObserver.
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

  // The two surfaces open with different words because they are different
  // things to be doing: here Jarvis reads with your memory and tools, there a
  // coding agent reads the folder. Same layout, so nothing moves.
  const isJarvis = surface === "jarvis";
  const subtitle = useMemo(
    () => t(isJarvis ? "agent_chat.empty_subtitle" : "agent_chat.empty_subtitle_agent"),
    [t, isJarvis],
  );

  // A spoken thread was opened from the history: read it here, in the column
  // the composer would otherwise own. An agent chat opening clears this.
  // Only the front page shares its column with the voice archive; the IDE's
  // chat is coding sessions and never shows a spoken thread.
  if (surface === "jarvis" && voiceThreadId && !activeSessionId) return <VoiceThreadStage />;

  if (!hasContent) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center" data-testid="chat-stage" data-empty="true">
        <div className="flex w-full max-w-[760px] flex-1 flex-col justify-center gap-8 px-6 pb-20">
          {isJarvis ? (
            <Greeting subtitle={subtitle} />
          ) : (
            <FolderHeadline folder={cwd} subtitle={subtitle} />
          )}
          <AgentComposer autoFocus />
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
          <AgentTimeline
            items={items}
            assistantName={isJarvis ? assistantName : t("agent_chat.surface_agent")}
            providerLabel={providerLabel}
            onDecide={onDecide}
          />
          <div ref={spacerRef} aria-hidden data-testid="chat-bottom-spacer" className="shrink-0" />
        </div>
      </ScrollArea>
      <div className="w-full max-w-[760px] px-6 pb-5 pt-2">
        <AgentComposer />
      </div>
    </div>
  );
}

/**
 * The IDE chat's opening line: which folder the coding agent works in.
 *
 * Not a greeting — nobody is being greeted by a coding agent, and the front
 * page's "Good afternoon" would say the wrong thing about who is answering.
 * Same typography and the same place on the page, so the two surfaces stay
 * one design.
 */
function FolderHeadline({ folder, subtitle }: { folder: string; subtitle: string }) {
  const t = useT();
  return (
    <div className="flex flex-col items-center text-center" data-testid="chat-folder-headline">
      <h1 className="flex items-center gap-3 font-display text-3xl font-semibold tracking-tight text-foreground [text-wrap:balance]">
        <FolderCode className="h-[30px] w-[30px] shrink-0 text-muted-foreground" aria-hidden />
        <span>{fill(t("agent_chat.empty_title_agent"), { folder: folderLeaf(folder) })}</span>
      </h1>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">{subtitle}</p>
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
  // Attribute compare instead of a selector: ids carry characters a CSS
  // selector would need escaping for.
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
