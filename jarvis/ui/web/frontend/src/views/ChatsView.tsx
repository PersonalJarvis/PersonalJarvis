import { useCallback, useEffect, useRef } from "react";
import { MessageSquare, Mic, Plus, Trash2, AudioLines } from "lucide-react";
import {
  useEventStore,
  type ChatMessage,
  type ConversationKind,
  type ConversationSummary,
} from "@/store/events";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ChatInput } from "@/components/ChatInput";
import { MascotGigi } from "@/components/MascotGigi";
import { ThinkingTrace, ThoughtTraceDisclosure } from "@/components/ThinkingTrace";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useResizablePane } from "@/hooks/useResizablePane";
import {
  ChatsApiError,
  deleteTextConversation,
  detailToMessages,
  fetchConversations,
  resumeConversation,
  speakInConversation,
} from "@/lib/chatsApi";

const LIST_REFRESH_MS = 5000;

export function ChatsView() {
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
  const endRef = useRef<HTMLDivElement | null>(null);

  // Drag-resizable history pane. Width persists across reloads (localStorage);
  // bounds keep it from collapsing or swallowing the chat column.
  const listPane = useResizablePane({
    storageKey: "chats.listWidth.v1",
    defaultWidth: 260,
    min: 200,
    max: 560,
  });

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, chatThinking]);

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
        width={listPane.width}
        conversations={conversations}
        activeId={activeThreadId}
        onOpen={openConversation}
        onNew={newChat}
        onDelete={removeConversation}
      />

      <PaneResizer
        onPointerDown={listPane.startResize}
        onDoubleClick={listPane.reset}
        active={listPane.isResizing}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <ViewHeader
          icon={<MessageSquare className="h-4 w-4 text-primary" />}
          title={activeTitle}
          subtitle={t("chats_view.subtitle")}
          right={
            <button
              type="button"
              onClick={speak}
              disabled={!activeThreadId}
              title={t("chats_view.speak")}
              className={cn(
                "flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
                activeThreadId
                  ? "border-primary/40 bg-primary/10 text-primary hover:bg-primary/20"
                  : "cursor-not-allowed border-border text-muted-foreground/50",
              )}
            >
              <AudioLines className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">{t("chats_view.speak")}</span>
              <span className="sm:hidden">{t("chats_view.speak_short")}</span>
            </button>
          }
        />

        <div className="flex-1 min-h-0">
          {!hasContent ? (
            <EmptyState />
          ) : (
            <ScrollArea className="h-full">
              <div className="space-y-3 px-6 py-4">
                {messages.map((m) => (
                  <MessageBubble key={m.id} message={m} />
                ))}
                {chatThinking && <ThinkingTrace />}
                <div ref={endRef} />
              </div>
            </ScrollArea>
          )}
        </div>

        <div className="border-t border-border px-6 py-4">
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
      className="flex h-full shrink-0 flex-col bg-card/30"
    >
      <div className="flex items-center justify-between border-b border-border px-3 py-3">
        <span className="font-display text-sm font-semibold tracking-tight">
          {t("chats_view.history")}
        </span>
        <button
          type="button"
          onClick={onNew}
          title={t("chats_view.new_chat")}
          className="flex items-center gap-1 rounded-lg border border-primary/40 bg-primary/10 px-2 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
        >
          <Plus className="h-3.5 w-3.5" />
          <span>{t("chats_view.new_chat")}</span>
        </button>
      </div>

      {conversations.length === 0 ? (
        <div className="flex flex-1 items-center justify-center px-4 text-center text-xs text-muted-foreground/60">
          {t("chats_view.empty_history")}
        </div>
      ) : (
        <ScrollArea className="flex-1">
          <div className="px-2 py-2">
            {groups.map(({ label, items }) => (
              <div key={label} className="mb-3">
                <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60">
                  {label}
                </div>
                <ul className="space-y-0.5">
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

/**
 * Vertical drag handle between the history list and the chat column.
 *
 * The visible seam is a 1px line (matching the old ``border-r``), but the hit
 * target is a wider 6px column so the grip is easy to catch. It lights up gold
 * on hover and while dragging. ``role="separator"`` keeps it accessible.
 */
function PaneResizer({
  onPointerDown,
  onDoubleClick,
  active,
}: {
  onPointerDown: (e: React.PointerEvent) => void;
  onDoubleClick: () => void;
  active: boolean;
}) {
  const t = useT();
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      title={t("chats_view.resize_hint")}
      onPointerDown={onPointerDown}
      onDoubleClick={onDoubleClick}
      className="group relative z-10 flex w-1.5 shrink-0 cursor-col-resize touch-none select-none items-stretch"
    >
      <span
        aria-hidden
        className={cn(
          "pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors",
          active ? "bg-primary" : "bg-border group-hover:bg-primary/60",
        )}
      />
    </div>
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
  const Icon = isVoice ? Mic : MessageSquare;
  const title = conversation.title || conversation.preview || t("chats_view.new_chat");

  return (
    <li className="group relative">
      <button
        type="button"
        onClick={onOpen}
        className={cn(
          "flex w-full items-start gap-2 rounded-lg px-2 py-2 text-left transition-colors",
          active
            ? "bg-background shadow-[inset_2px_0_0_hsl(var(--primary))]"
            : "hover:bg-background/60",
        )}
      >
        <Icon
          className={cn(
            "mt-0.5 h-3.5 w-3.5 shrink-0",
            isVoice ? "text-emerald-400" : "text-primary/80",
          )}
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium text-foreground">
            {title}
          </span>
          {conversation.preview && conversation.preview !== title && (
            <span className="block truncate text-[11px] text-muted-foreground">
              {conversation.preview}
            </span>
          )}
          <span className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground/60">
            <span className="rounded-sm bg-secondary/60 px-1 py-px uppercase tracking-wide">
              {isVoice ? t("chats_view.voice_badge") : t("chats_view.text_badge")}
            </span>
            <span>{formatTime(conversation.updated_ms)}</span>
          </span>
        </span>
      </button>
      {!isVoice && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          title={t("chats_view.delete")}
          className="absolute right-1 top-1.5 hidden rounded-md p-1 text-muted-foreground/60 transition-colors hover:bg-destructive/15 hover:text-destructive group-hover:block"
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

/**
 * Shared assistant byline. Replaces the old sparkle-icon + "Jarvis" header —
 * the sparkle read as a generic-AI tell. Now a calm gold dot + mono wordmark.
 */
function AssistantLabel() {
  return (
    <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-primary">
      <span className="h-1 w-1 rounded-full bg-primary" aria-hidden />
      Jarvis
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  const isPreamble = message.role === "preamble";
  // Finished reasoning trace for this reply (assistant messages only) —
  // renders as the collapsible "Thought for Xs" disclosure above the text.
  const trace = useEventStore((s) => s.thinkingTraces[message.id]);

  if (isSystem) {
    return (
      <div className="mx-auto max-w-[85%] rounded-lg border border-border bg-secondary/40 px-3 py-2 text-center text-xs italic text-muted-foreground">
        {message.content}
      </div>
    );
  }

  if (isPreamble) {
    return (
      <div className="flex justify-start">
        <div className="max-w-[80%] rounded-2xl rounded-bl-sm border border-dashed border-border bg-secondary/30 px-4 py-2.5 text-xs italic leading-relaxed text-muted-foreground">
          <div className="mb-1 flex items-center gap-1.5">
            <span className="rounded-sm border border-border bg-secondary/70 px-1.5 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
              pre-ack
            </span>
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
              Jarvis
            </span>
          </div>
          <div className="whitespace-pre-wrap">{message.content}</div>
        </div>
      </div>
    );
  }

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] px-4 py-3 text-sm leading-relaxed",
          isUser
            ? "rounded-2xl rounded-br-sm bg-primary text-primary-foreground"
            : "rounded-2xl rounded-bl-sm border border-border bg-card text-foreground",
        )}
      >
        {!isUser && <AssistantLabel />}
        {!isUser && trace && <ThoughtTraceDisclosure trace={trace} />}
        <div className={cn("whitespace-pre-wrap", !isUser && "mt-1.5")}>{message.content}</div>
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

  return (
    <div className="flex h-full flex-col items-center justify-center px-6 py-10 text-center">
      <div className="profile-rise mb-6 h-24 w-24" style={{ animationDelay: "0ms" }}>
        <MascotGigi size={96} reactToVoice enableComments={false} />
      </div>
      <h3
        className="profile-rise font-display text-3xl font-semibold tracking-tight text-foreground sm:text-4xl"
        style={{ animationDelay: "80ms" }}
      >
        {t("chats_view.empty_title")}
      </h3>
      <p
        className="profile-rise mt-3 max-w-md text-sm leading-relaxed text-muted-foreground sm:text-base"
        style={{ animationDelay: "160ms" }}
      >
        {t("chats_view.empty_subtitle")}
      </p>
    </div>
  );
}

export function ViewHeader({
  icon,
  title,
  subtitle,
  right,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
}) {
  return (
    <header className="flex items-center gap-3 border-b border-border px-6 py-4">
      <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-secondary/50">
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <h2 className="font-display text-base font-semibold tracking-tight">{title}</h2>
        {subtitle && (
          <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
        )}
      </div>
      {right}
    </header>
  );
}
