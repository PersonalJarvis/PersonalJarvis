import { useCallback, useEffect, useLayoutEffect, type ReactNode } from "react";
import { PageHeader } from "@/components/layout/PageHeader";
import { MessageSquare, Mic, Plus, Trash2, AudioLines } from "lucide-react";
import { identityInitial, identityMark } from "@/lib/identityHue";
import {
  useEventStore,
  type ChatMessage,
  type ConversationKind,
  type ConversationSummary,
} from "@/store/events";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ScrollToEndButton } from "@/components/ui/scroll-to-end-button";
import { ChatInput } from "@/components/ChatInput";
import { MascotGigi } from "@/components/MascotGigi";
import { ThinkingTrace, ThoughtTraceDisclosure } from "@/components/ThinkingTrace";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useResizablePane } from "@/hooks/useResizablePane";
import { useStickToBottom } from "@/hooks/useStickToBottom";
import { PaneResizer } from "@/components/layout/PaneResizer";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import {
  ChatsApiError,
  deleteTextConversation,
  detailToMessages,
  fetchConversations,
  resumeConversation,
  speakInConversation,
} from "@/lib/chatsApi";

const LIST_REFRESH_MS = 5000;

export function ChatsView({
  headerAccessory,
}: {
  /**
   * The surface switch, handed down by the shell that owns the mode. Absent
   * when this view is rendered on its own (tests, detached window), which is
   * why it stays optional rather than required.
   */
  headerAccessory?: ReactNode;
} = {}) {
  const t = useT();
  const messages = useEventStore((s) => s.messages);
  const chatThinking = useEventStore((s) => s.chatThinking);
  const conversations = useEventStore((s) => s.conversations);
  const activeThreadId = useEventStore((s) => s.activeThreadId);
  const activeKind = useEventStore((s) => s.activeKind);
  const setConversations = useEventStore((s) => s.setConversations);
  const setActiveConversation = useEventStore((s) => s.setActiveConversation);
  const setMessages = useEventStore((s) => s.setMessages);
  const pushToast = useEventStore((s) => s.pushToast);

  // Drag-resizable history pane. Width persists across reloads (localStorage);
  // bounds keep it from collapsing or swallowing the chat column.
  const listPane = useResizablePane({
    storageKey: "chats.listWidth.v1",
    defaultSize: 260,
    min: 200,
    max: 560,
  });

  // Follow the answer, but never drag the reader back: `chatThinking` ticks
  // through a whole reply, so an unconditional scroll here made it impossible
  // to look back at anything while the assistant was still writing.
  const { rootRef, contentRef, atEnd, jumpToEnd, follow } = useStickToBottom();
  useLayoutEffect(follow, [follow, messages.length, chatThinking]);

  const refresh = useCallback(async () => {
    try {
      setConversations(await fetchConversations());
    } catch {
      /* offline / headless — leave the list as-is */
    }
  }, [setConversations]);

  // Load on mount + poll lightly so new threads, titles and ordering stay
  // fresh as the user chats (GET /api/chats is a fast local query).
  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), LIST_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  const openConversation = useCallback(
    async (kind: ConversationKind, id: string) => {
      setActiveConversation(kind, id);
      try {
        const detail = await resumeConversation(kind, id);
        setMessages(detailToMessages(detail));
      } catch {
        setMessages([]);
      }
    },
    [setActiveConversation, setMessages],
  );

  const newChat = useCallback(() => {
    setActiveConversation("text", null);
    setMessages([]);
  }, [setActiveConversation, setMessages]);

  const removeConversation = useCallback(
    async (id: string) => {
      try {
        await deleteTextConversation(id);
      } catch {
        /* ignore */
      }
      if (activeThreadId === id) newChat();
      void refresh();
    },
    [activeThreadId, newChat, refresh],
  );

  const speak = useCallback(async () => {
    if (!activeThreadId) return;
    try {
      await speakInConversation(activeKind, activeThreadId);
      pushToast("success", t("chats_view.speak_started"));
    } catch (e) {
      if (e instanceof ChatsApiError && e.status === 503) {
        pushToast("warning", t("chats_view.speak_unavailable"));
      } else {
        pushToast("error", t("chats_view.speak_unavailable"));
      }
    }
  }, [activeKind, activeThreadId, pushToast, t]);

  const hasContent = messages.length > 0 || chatThinking;
  const activeTitle =
    conversations.find((c) => c.id === activeThreadId)?.title || t("chats_view.title");

  return (
    <div className="flex h-full min-h-0">
      <ConversationList
        width={listPane.size}
        conversations={conversations}
        activeId={activeThreadId}
        onOpen={openConversation}
        onNew={newChat}
        onDelete={removeConversation}
      />

      <PaneResizer
        onPointerDown={listPane.startResize}
        onDoubleClick={listPane.reset}
        onNudge={listPane.nudge}
        active={listPane.isResizing}
        title={t("chats_view.resize_hint")}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <ViewHeader
          icon={<MessageSquare className="h-4 w-4" />}
          title={activeTitle}
          subtitle={t("chats_view.subtitle")}
          right={
            <div className="flex items-center gap-row">
              <button
                type="button"
                onClick={speak}
                disabled={!activeThreadId}
                title={t("chats_view.speak")}
                className={cn(
                  "flex items-center gap-row rounded-md px-3 py-1.5 text-body font-medium transition-colors",
                  activeThreadId
                    ? "text-muted-foreground hover:bg-secondary hover:text-foreground-strong"
                    : "cursor-not-allowed text-faint-foreground",
                )}
              >
                <AudioLines className="h-4 w-4" />
                <span className="hidden sm:inline">{t("chats_view.speak")}</span>
                <span className="sm:hidden">{t("chats_view.speak_short")}</span>
              </button>
              {headerAccessory}
            </div>
          }
        />

        {/*
         * The transcript and the composer share one measure. Rule 1 — lift
         * scales inversely with area — is the reason the bubbles are capped at
         * 720px rather than allowed to run the window: a message the width of a
         * monitor is a slab, and message length stops being visual rhythm.
         */}
        <div className="flex-1 min-h-0">
          {!hasContent ? (
            <EmptyState />
          ) : (
            <ScrollArea ref={rootRef} className="h-full">
              {/* Turn gap 24px, then 40px of air so the composer below reads as
                  a tool rather than as the last thing said. */}
              <div
                ref={contentRef}
                className="mx-auto w-full max-w-reading space-y-6 px-6 pb-10 pt-6"
              >
                {messages.map((m) => (
                  <MessageBubble key={m.id} message={m} />
                ))}
                {chatThinking && <ThinkingTrace />}
              </div>
            </ScrollArea>
          )}
        </div>

        {/* No rule above the composer: it has a real fill of its own, and a
            hairline plus a fill is the border this system does not draw. */}
        <div className="relative mx-auto w-full max-w-reading px-6 pb-6">
          {hasContent && !atEnd && <ScrollToEndButton onClick={jumpToEnd} />}
          <ChatInput />
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Left pane — conversation history
// ----------------------------------------------------------------------

function ConversationList({
  width,
  conversations,
  activeId,
  onOpen,
  onNew,
  onDelete,
}: {
  width: number;
  conversations: ConversationSummary[];
  activeId: string | null;
  onOpen: (kind: ConversationKind, id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  const t = useT();
  const groups = groupByDay(conversations, t);

  return (
    <aside
      style={{ width }}
      className="flex h-full shrink-0 flex-col bg-sidebar"
    >
      {/* No rule under the header. The rail is already a different fill from
          the room, and a list that reads on its own needs no chrome around it —
          Grok Bot's conversation list contains zero dividers. */}
      <div className="flex items-center justify-between gap-row px-3 py-3">
        <span className="text-title font-semibold text-foreground-strong">
          {t("chats_view.history")}
        </span>
        <button
          type="button"
          onClick={onNew}
          title={t("chats_view.new_chat")}
          className="flex shrink-0 items-center gap-1.5 rounded-md bg-primary px-2.5 py-1.5 text-meta font-medium text-primary-foreground transition-opacity hover:opacity-90"
        >
          <Plus className="h-3.5 w-3.5" />
          <span>{t("chats_view.new_chat")}</span>
        </button>
      </div>

      {conversations.length === 0 ? (
        <div className="flex flex-1 items-center justify-center px-4 text-center text-meta text-muted-foreground">
          {t("chats_view.empty_history")}
        </div>
      ) : (
        <ScrollArea className="flex-1">
          {/* 32px between day groups — the step the product was missing, and
              the reason a history list used to read as one long mesh. */}
          <div className="space-y-group px-2 pb-block pt-1">
            {groups.map(({ label, items }) => (
              <div key={label}>
                <div className="px-2 pb-1.5 text-meta text-muted-foreground">
                  {label}
                </div>
                <ul>
                  {items.map((c) => (
                    <ConversationRow
                      key={`${c.kind}-${c.id}`}
                      conversation={c}
                      active={c.id === activeId}
                      onOpen={() => onOpen(c.kind, c.id)}
                      onDelete={() => onDelete(c.id)}
                    />
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </ScrollArea>
      )}
    </aside>
  );
}

function ConversationRow({
  conversation,
  active,
  onOpen,
  onDelete,
}: {
  conversation: ConversationSummary;
  active: boolean;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  const isVoice = conversation.kind === "voice";
  const title = conversation.title || conversation.preview || t("chats_view.new_chat");
  // Second line: the preview where it says something the title does not,
  // otherwise the time on its own. The old row stacked title, preview, a
  // kind badge AND a timestamp into 56px of four type sizes; two lines carry
  // the same facts and let the row breathe to the reference height.
  const preview =
    conversation.preview && conversation.preview !== title ? conversation.preview : null;
  const time = formatTime(conversation.updated_ms);

  return (
    <li className="group relative">
      <button
        type="button"
        onClick={onOpen}
        className={cn(
          // Selection is the whole row: a full-width fill inset from the
          // column edge, never a hairline and never an accent on the icon.
          "flex w-full items-center gap-stack rounded-md px-2 py-3 text-left transition-colors",
          active ? "bg-secondary" : "hover:bg-secondary",
        )}
      >
        <span
          aria-hidden
          style={identityMark(title)}
          className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-meta font-semibold"
        >
          {identityInitial(title)}
          {isVoice && (
            // The kind used to be an all-caps chip on its own line. It is one
            // fact about the conversation, so it rides the mark instead.
            <span className="absolute -bottom-0.5 -right-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-sidebar">
              <Mic className="h-2.5 w-2.5 text-muted-foreground" />
            </span>
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span
            className={cn(
              "block truncate text-title font-semibold",
              active ? "text-foreground-strong" : "text-foreground",
            )}
          >
            {title}
          </span>
          <span className="mt-0.5 block truncate text-meta text-muted-foreground">
            {preview ?? time}
          </span>
        </span>
        {preview && (
          <span className="shrink-0 self-start pt-0.5 text-meta tabular-nums text-muted-foreground">
            {time}
          </span>
        )}
      </button>
      {!isVoice && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          title={t("chats_view.delete")}
          className="absolute right-1.5 top-1/2 hidden -translate-y-1/2 rounded-md bg-secondary p-1.5 text-muted-foreground transition-colors hover:text-destructive group-hover:block"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      )}
    </li>
  );
}

interface DayGroup {
  label: string;
  items: ConversationSummary[];
}

function groupByDay(
  conversations: ConversationSummary[],
  t: (key: string) => string,
): DayGroup[] {
  const today = startOfDay(Date.now());
  const yesterday = today - 86_400_000;
  const buckets: Record<string, ConversationSummary[]> = {
    today: [],
    yesterday: [],
    earlier: [],
  };
  for (const c of conversations) {
    const day = startOfDay(c.updated_ms);
    if (day >= today) buckets.today.push(c);
    else if (day >= yesterday) buckets.yesterday.push(c);
    else buckets.earlier.push(c);
  }
  const order: Array<[string, string]> = [
    ["today", "chats_view.group_today"],
    ["yesterday", "chats_view.group_yesterday"],
    ["earlier", "chats_view.group_earlier"],
  ];
  return order
    .filter(([k]) => buckets[k].length > 0)
    .map(([k, labelKey]) => ({ label: t(labelKey), items: buckets[k] }));
}

function startOfDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function formatTime(ms: number): string {
  if (!ms) return "";
  const d = new Date(ms);
  const today = startOfDay(Date.now());
  if (startOfDay(ms) >= today) {
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

// ----------------------------------------------------------------------
// Right pane helpers (unchanged behaviour)
// ----------------------------------------------------------------------

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  const isPreamble = message.role === "preamble";
  // Finished reasoning trace for this reply (assistant messages only) —
  // renders as the collapsible "Thought for Xs" disclosure above the text.
  const trace = useEventStore((s) => s.thinkingTraces[message.id]);

  // A system note is an aside about the conversation, not a turn in it. It used
  // to be a bordered card in the middle of the column, which made the timeline
  // read as three kinds of box; centred meta ink says the same thing quieter.
  if (isSystem) {
    return (
      <div className="px-4 text-center text-meta text-muted-foreground">
        {message.content}
      </div>
    );
  }

  // The pre-acknowledgement — the short thing said before the real answer
  // arrives. Same speaker as the reply that follows, so it is not given a
  // second bubble: the timeline doctrine forbids nested and near-duplicate
  // boxes, and the "pre-ack" chip only restated what the italic already said.
  if (isPreamble) {
    return (
      <div className="max-w-[85%] whitespace-pre-wrap text-reading italic text-muted-foreground">
        {message.content}
      </div>
    );
  }

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          // Two independent signals for one fact: the fill says who spoke and
          // so does the side it sits on. That is why the byline above the
          // assistant's text is gone — it was a third signal for the same fact,
          // set in --primary, which this system reserves for fills.
          "max-w-[85%] rounded-lg px-4 py-3 text-reading",
          isUser
            ? "jarvis-user-bubble"
            : "jarvis-message-surface text-foreground",
        )}
      >
        {!isUser && trace && <ThoughtTraceDisclosure trace={trace} />}
        <div className="whitespace-pre-wrap">{message.content}</div>
      </div>
    </div>
  );
}

/**
 * Empty state — Claude-style: calm, centered, no suggestion cards. A quiet
 * mascot, a centered greeting and a one-line subtitle. The composer below is
 * the focus (it carries the new mic / dictation button). Deliberately minimal —
 * the user explicitly asked to drop the canned prompt cards.
 */
function EmptyState() {
  const t = useT();
  // Honest readiness: while the voice stack is still warming, the centre of the
  // screen must NOT claim "Ready for commands" while the banner says "starting
  // up". Both now read the same useVoiceReadiness source. Typing already works
  // (the input is connected-gated, not voice-gated), so the warming copy says so.
  const { warming } = useVoiceReadiness();

  return (
    <div className="flex h-full flex-col items-center justify-center px-6 py-10 text-center">
      <div className="profile-rise mb-6 h-24 w-24" style={{ animationDelay: "0ms" }}>
        <MascotGigi size={96} reactToVoice enableComments={false} />
      </div>
      <h3
        className="profile-rise font-display text-display text-foreground-strong"
        style={{ animationDelay: "80ms" }}
      >
        {warming ? t("chats_view.warming_title") : t("chats_view.empty_title")}
      </h3>
      <p
        className="profile-rise mt-3 max-w-form text-reading text-muted-foreground"
        style={{ animationDelay: "160ms" }}
      >
        {warming ? t("chats_view.warming_subtitle") : t("chats_view.empty_subtitle")}
      </p>
    </div>
  );
}

export function ViewHeader({
  icon,
  title,
  titleBadge,
  subtitle,
  right,
}: {
  icon: ReactNode;
  title: string;
  titleBadge?: ReactNode;
  subtitle?: string;
  right?: ReactNode;
}) {
  // The one header every view wears (v4): `PageHeader` at the view gutter.
  // `titleBadge` rides in the actions slot, before the view's own controls.
  return (
    <div className="shrink-0 px-8">
      <PageHeader
        icon={icon}
        title={title}
        description={subtitle}
        className="pb-4"
        actions={
          titleBadge || right ? (
            <>
              {titleBadge}
              {right}
            </>
          ) : undefined
        }
      />
    </div>
  );
}
