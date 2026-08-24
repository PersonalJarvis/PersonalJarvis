import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from "react";

import { useEventStore } from "@/store/events";
import { useAgentChatStore } from "@/store/agentChat";
import { ScrollArea } from "@/components/ui/scroll-area";
import { AgentComposer } from "@/components/agentchat/AgentComposer";
import { AgentTimeline } from "@/components/agentchat/AgentTimeline";
import { Greeting } from "@/components/home/Greeting";
import { VoiceThreadStage } from "@/components/home/VoiceThreadStage";
import type { ApprovalDecision } from "@/lib/agentChatApi";
import { useT } from "@/i18n";

/**
 * The chat stage — the typed half of the front page.
 *
 * Since 2026-08-23 this is the AGENT chat (jarvis/agent_chat): a coding-agent
 * session on whichever sub-agent — Claude Code, Codex, Antigravity, Grok
 * Build, or an API-key provider — the composer picks, with the model, the
 * reasoning effort and the permission mode dialled in the composer itself.
 * The voice stage next door still talks to the voice brain; this stage does
 * not (AP-9: nothing here touches the voice path).
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
  const items = useAgentChatStore((s) => s.timeline.items);
  const activeSessionId = useAgentChatStore((s) => s.activeSessionId);
  const catalog = useAgentChatStore((s) => s.catalog);
  const decide = useAgentChatStore((s) => s.decide);
  const loadCatalog = useAgentChatStore((s) => s.loadCatalog);
  const loadSessions = useAgentChatStore((s) => s.loadSessions);
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

  const subtitle = useMemo(() => t("agent_chat.empty_subtitle"), [t]);

  // A spoken thread was opened from the history: read it here, in the column
  // the composer would otherwise own. An agent chat opening clears this.
  if (voiceThreadId && !activeSessionId) return <VoiceThreadStage />;

  if (!hasContent) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center" data-testid="chat-stage" data-empty="true">
        <div className="flex w-full max-w-[760px] flex-1 flex-col justify-center gap-8 px-6 pb-20">
          <Greeting subtitle={subtitle} />
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
            assistantName={assistantName}
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
