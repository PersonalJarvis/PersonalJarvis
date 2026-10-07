import { Fragment, memo, useMemo } from "react";
import { ChatMarkdown, MediaPreview, mediaKind } from "@/components/agentchat/ChatMarkdown";
import { CircleAlert, FileText, ImageIcon } from "lucide-react";
import { InternalMessageBubble, type InternalParticipant } from "./InternalMessageBubble";
import { CodingThreadActivity } from "@/components/agentic/threads/CodingThreadLink";
import { codingThreadOf, foldRepeatedThreadStatus } from "@/components/agentic/threads/openCodingThread";
import { MessageWithChips } from "./ToolChoiceChips";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { effortLabel } from "./AgentComposer";
import { TurnTrace, type Decide, type TraceLook } from "./WorkTrace";
import type { TimelineItem, TurnItem, TextBlock } from "./reduce";
import { TraceMessageLine } from "./TraceTimeline";
import { attachTurnMessages, type TraceMessage } from "./turnMessages";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/** Shared transcript shell; the work trace owns all execution presentation. */
export function AgentTimeline({
  items,
  assistantName,
  providerLabel,
  onDecide,
  recipientName,
  agentsById,
  bubbles = false,
  traceLook = "rail",
  traceCompanion = false,
}: {
  items: TimelineItem[];
  assistantName: string;
  providerLabel: (providerId: string) => string;
  onDecide: Decide;
  /** Who an internal agent message was sent to — the chat owner (defaults to the assistant). */
  recipientName?: string;
  /** Sender faces by agent id, where the caller has a roster. */
  agentsById?: Record<string, InternalParticipant>;
  /**
   * The front page's conversation look (2026-10-01):
   * your words in a soft rounded bubble on the right, the assistant's answer
   * as plain text with no name-and-model header above it, and a quiet
   * centred time stamp wherever the conversation paused. The Agentic IDE
   * keeps the labelled document look (the default).
   */
  bubbles?: boolean;
  /** How turns draw their work; the Agentic IDE keeps the classic rows. */
  traceLook?: TraceLook;
  /** Jarvis's own chat: live traces show the user's pet at work. */
  traceCompanion?: boolean;
}) {
  const t = useT();
  // On the rail look an agent's answer to a working turn is a quiet line in
  // that turn's trace, so the reply stays the last thing in the chat.
  const { items: shown, messagesByTurn } = useMemo(() => {
    // A coding thread's unchanged status is one row, however often it arrived.
    const folded = foldRepeatedThreadStatus(items);
    return traceLook === "rail" ? attachTurnMessages(folded) : { items: folded, messagesByTurn: NO_MESSAGES };
  }, [items, traceLook]);
  const stamps = bubbles ? timeStamps(shown) : null;
  return (
    <>
      {shown.map((item) => {
        const stamp = stamps?.get(item.id);
        const node = renderItem(item);
        return stamp ? (
          <Fragment key={item.id}>
            <div className="flex justify-center pt-2" data-testid="agent-time-stamp" aria-hidden>
              <span className="text-xs font-medium text-muted-foreground">{stamp}</span>
            </div>
            {node}
          </Fragment>
        ) : node;
      })}
    </>
  );

  function renderItem(item: TimelineItem) {
    if (item.type === "internal" && codingThreadOf(item.message.sender_id)) {
      return <CodingThreadActivity key={item.id} label={item.message.text}
        threadId={codingThreadOf(item.message.sender_id)} failed={item.message.status === "failed"} />;
    }
    if (item.type === "internal") {
      return (
        <InternalMessageBubble
          key={item.id}
          item={item}
          sender={agentsById?.[item.message.sender_id] ?? null}
          recipientName={recipientName ?? assistantName}
        />
      );
    }
    if (item.type === "user") {
      if (item.origin === "control") return <div key={item.id} data-message-id={item.id} className="text-xs text-muted-foreground">{t("slash.control_turn")}{item.attachments.map((file) => <span key={file.name} className="ml-2">{file.name}</span>)}</div>;
      return (
        <div
          key={item.id}
          className="flex justify-end"
          data-testid="agent-message-user"
          data-message-id={item.id}
        >
          <div
            className={cn(
              "text-[15px] leading-[23px]",
              bubbles
                ? "jarvis-user-bubble max-w-[80%] rounded-[20px] px-4 py-2.5"
                : "jarvis-user-bubble max-w-[85%] rounded-lg px-4 py-3",
            )}
          >
            <MessageWithChips text={item.text} choices={item.toolChoices ?? []} />
            {item.attachments.length > 0 && (
              <div
                data-testid="agent-message-attachments"
                className={cn("flex flex-wrap gap-row", item.text && "mt-2.5")}
              >
                {item.attachments.map((file) =>
                  // The picture itself, where there is one to fetch: a
                  // screenshot dropped on a pane is a thing the person
                  // wants to SEE in their turn, not a file name with an
                  // icon (maintainer, 2026-08-27). A file with no url —
                  // a document, or the front page's chat, whose drops
                  // are read and not stored — keeps the chip.
                  file.url && (mediaKind(file.url) === "video" || mediaKind(file.url) === "audio") ? (
                    <MediaPreview key={file.name} src={file.url} label={file.name} kind={mediaKind(file.url)!} />
                  ) : (file.kind === "image" || (file.url && mediaKind(file.url) === "image")) && file.url ? (
                    <img
                      key={file.name}
                      src={file.url}
                      alt={file.name}
                      title={file.name}
                      loading="lazy"
                      decoding="async"
                      data-testid="agent-message-image"
                      // A picture is its own fill. The frame and the wash
                      // behind it were describing an object that was
                      // already fully described.
                      className="max-h-60 max-w-full rounded-lg object-contain"
                    />
                  ) : (
                  <span
                    key={file.name}
                    // The receipt says whether the model could actually
                    // READ it. "sent" and "could not be read" are the two
                    // outcomes, and the second happens for real wherever no
                    // provider can see an image.
                    title={
                      file.describedBy === "none"
                        ? t("agent_chat.attach_not_described")
                        : file.name
                    }
                    // No box. The user bubble is the loudest surface the
                    // ladder has, so a chip inside it has nowhere to step
                    // up to; the icon and the mono name carry the chip's
                    // whole job on their own.
                    className="flex items-center gap-1.5 text-micro opacity-80"
                  >
                    {file.kind === "image" ? (
                      <ImageIcon className="h-3 w-3 shrink-0" aria-hidden />
                    ) : (
                      <FileText className="h-3 w-3 shrink-0" aria-hidden />
                    )}
                    <span className="max-w-[12rem] truncate font-mono">{file.name}</span>
                  </span>
                  ),
                )}
              </div>
            )}
          </div>
        </div>
      );
    }
    if (item.type === "error") {
      return (
        <div
          key={item.id}
          data-message-id={item.id}
          // Fault ink on a normal surface. A red-washed panel makes the
          // failure the brightest object on the screen and buries what it
          // says under what it looks like.
          className="mx-auto flex max-w-[85%] items-center gap-row rounded-lg bg-card px-4 py-3 text-body text-destructive"
        >
          <CircleAlert className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>{item.text}</span>
        </div>
      );
    }
    if (item.type === "notice") {
      if (item.kind === "native_goal_verdict") return <p key={item.id} className="text-xs text-muted-foreground">{t("slash.verifying")}</p>;
      if (item.kind === "coding_thread") {
        const started = t("society.chat.coding_thread_started").replace("{0}", String(item.data.agent ?? ""));
        return <CodingThreadActivity key={item.id} label={`${started} · ${String(item.data.title ?? "")}`}
          threadId={String(item.data.thread_id ?? "")} />;
      }
      // The society reporting back on a task Jarvis handed out: the
      // agent's name as the headline, its summary underneath. Muted and
      // centred like a stamp — it is not Jarvis speaking.
      const headline =
        item.kind === "society_result"
          ? t(item.status === "done" ? "society.chat.result_done" : "society.chat.result_blocked").replace(
              "{0}",
              item.agentName || t("society.chat.result_agent"),
            )
          : item.agentName;
      return (
        <div
          key={item.id}
          data-message-id={item.id}
          className="mx-auto flex max-w-[85%] flex-col gap-0.5 rounded-lg bg-card px-4 py-3 text-body"
        >
          {headline ? <span className="font-medium text-foreground">{headline}</span> : null}
          {item.text ? <ChatMarkdown text={item.text} className="text-muted-foreground" /> : null}
        </div>
      );
    }
    return (
      <Turn
        key={item.id}
        turn={item}
        assistantName={assistantName}
        providerLabel={providerLabel(item.provider)}
        onDecide={onDecide}
        bubbles={bubbles}
        traceLook={traceLook}
        traceCompanion={traceCompanion}
        messages={messagesByTurn.get(item.id)}
      />
    );
  }
}

/** A pause this long between two messages earns a new time stamp. */
const STAMP_GAP_MS = 20 * 60 * 1000;

function itemTime(item: TimelineItem): number {
  return item.type === "turn" ? item.startedMs : item.tsMs;
}

/**
 * Which items open with a time stamp, and what it says: the first one, and
 * every one after a pause of {@link STAMP_GAP_MS}. Today shows the clock
 * only; any other day leads with the date, the way a messenger does.
 */
export function timeStamps(items: TimelineItem[], now: Date = new Date()): Map<string, string> {
  const out = new Map<string, string>();
  let last = 0;
  for (const item of items) {
    const ts = itemTime(item);
    if (!ts || !Number.isFinite(ts)) continue;
    if (!last || ts - last >= STAMP_GAP_MS) out.set(item.id, stampLabel(new Date(ts), now));
    last = ts;
  }
  return out;
}

function stampLabel(at: Date, now: Date): string {
  const time = at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (at.toDateString() === now.toDateString()) return time;
  const date = at.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(at.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
  return `${date}, ${time}`;
}

const NO_MESSAGES = new Map<string, TraceMessage[]>();

const Turn = memo(function Turn({ turn, assistantName, providerLabel, onDecide, bubbles = false, traceLook, traceCompanion = false, messages }: {
  turn: TurnItem; assistantName: string; providerLabel: string; onDecide: Decide; bubbles?: boolean; traceLook: TraceLook; traceCompanion?: boolean;
  /** Agent messages that arrived while this turn worked. */
  messages?: TraceMessage[];
}) {
  const t = useT();
  const extras = useMemo(
    () => messages?.map((message) => ({ key: message.id, node: <TraceMessageLine message={message} /> })),
    [messages],
  );
  return <div className="flex min-w-0 flex-col gap-3" data-testid="agent-turn" data-message-id={turn.id} data-status={turn.status}>
    {!bubbles && (
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">{assistantName}</span>
        <ProviderLogo providerId={turn.provider} label={providerLabel} size="sm" />
        <span>{providerLabel}{turn.model ? ` · ${turn.model}` : ""}</span>
        {turn.effort ? <span>{effortLabel(turn.effort, t)}</span> : null}
      </div>
    )}
    <TurnTrace turn={turn} look={traceLook} companion={traceCompanion} extras={extras} onDecide={onDecide} renderText={(text, id) => <Prose block={{ kind: "text", text, id }} />} />
  </div>;
});

function Prose({ block }: { block: TextBlock }) {
  if (!block.text.trim()) return null;
  return (
    <div
      data-testid="agent-text"
      className={cn(
        // Compact reading size: headings stay close to body
        // size and set apart by weight and spacing, not by scale.
        "prose prose-neutral max-w-none text-[15px] leading-[25px] text-foreground dark:prose-invert dark:text-foreground [overflow-wrap:anywhere]",
        "[&>div>:first-child]:mt-0 [&>div>:last-child]:mb-0",
        "prose-p:my-2.5 prose-p:text-foreground prose-li:text-foreground prose-strong:text-foreground-strong",
        "prose-headings:mb-1.5 prose-headings:mt-5 prose-headings:font-semibold prose-headings:tracking-normal prose-headings:text-foreground-strong",
        "prose-h1:text-[17px] prose-h1:leading-[25px] prose-h2:text-[16px] prose-h2:leading-[25px] prose-h3:text-[15px] prose-h3:leading-[25px] prose-h4:text-[15px]",
        "prose-a:text-foreground-strong prose-a:underline prose-a:decoration-border-strong prose-a:underline-offset-2",
        "prose-code:rounded prose-code:bg-secondary prose-code:px-1 prose-code:py-0.5 prose-code:font-mono prose-code:text-[0.85em] prose-code:font-normal prose-code:before:hidden prose-code:after:hidden",
        "prose-pre:my-2.5 prose-pre:bg-card prose-pre:text-[13px] prose-pre:leading-[20px]",
        "prose-li:my-1 prose-ul:my-2.5 prose-ol:my-2.5 prose-ul:pl-5 prose-ol:pl-5 prose-li:pl-1",
        "prose-hr:my-5",
        "prose-table:my-3 prose-table:text-[14px] prose-table:leading-[21px]",
        "prose-thead:text-foreground-strong prose-th:text-foreground-strong prose-td:text-foreground",
      )}
    >
      <ChatMarkdown text={block.text} />
    </div>
  );
}
