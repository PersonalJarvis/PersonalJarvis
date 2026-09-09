import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CircleAlert, FileText, ImageIcon } from "lucide-react";
import { InternalMessageBubble, type InternalParticipant } from "./InternalMessageBubble";
import { MessageWithChips } from "./ToolChoiceChips";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { effortLabel } from "./AgentComposer";
import { TurnTrace, type Decide } from "./WorkTrace";
import type { TimelineItem, TurnItem, TextBlock } from "./reduce";
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
}: {
  items: TimelineItem[];
  assistantName: string;
  providerLabel: (providerId: string) => string;
  onDecide: Decide;
  /** Who an internal agent message was sent to — the chat owner (defaults to the assistant). */
  recipientName?: string;
  /** Sender faces by agent id, where the caller has a roster. */
  agentsById?: Record<string, InternalParticipant>;
}) {
  const t = useT();
  return (
    <>
      {items.map((item) => {
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
          return (
            <div
              key={item.id}
              className="flex justify-end"
              data-testid="agent-message-user"
              data-message-id={item.id}
            >
              <div className="jarvis-user-bubble max-w-[85%] rounded-lg px-4 py-3 text-reading">
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
                      file.kind === "image" && file.url ? (
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
              {item.text ? <span className="whitespace-pre-wrap text-muted-foreground">{item.text}</span> : null}
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
          />
        );
      })}
    </>
  );
}

const Turn = memo(function Turn({ turn, assistantName, providerLabel, onDecide }: {
  turn: TurnItem; assistantName: string; providerLabel: string; onDecide: Decide;
}) {
  const t = useT();
  return <div className="flex min-w-0 flex-col gap-3" data-testid="agent-turn" data-message-id={turn.id} data-status={turn.status}>
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span className="font-medium text-foreground">{assistantName}</span>
      <ProviderLogo providerId={turn.provider} label={providerLabel} size="sm" />
      <span>{providerLabel}{turn.model ? ` · ${turn.model}` : ""}</span>
      {turn.effort ? <span>{effortLabel(turn.effort, t)}</span> : null}
    </div>
    <TurnTrace turn={turn} onDecide={onDecide} renderText={(text, id) => <Prose block={{ kind: "text", text, id }} />} />
  </div>;
});

function Prose({ block }: { block: TextBlock }) {
  if (!block.text.trim()) return null;
  return (
    <div
      data-testid="agent-text"
      className={cn(
        "prose prose-neutral max-w-none text-[17px] leading-[30px] text-foreground dark:prose-invert dark:text-foreground [overflow-wrap:anywhere]",
        "prose-p:my-2 prose-p:text-foreground prose-li:text-foreground prose-strong:text-foreground-strong",
        "prose-headings:font-display prose-headings:tracking-tight prose-headings:text-foreground-strong prose-h1:text-xl prose-h2:text-lg prose-h3:text-base",
        "prose-a:text-foreground-strong prose-a:underline prose-a:decoration-border-strong prose-a:underline-offset-2",
        "prose-code:rounded prose-code:bg-secondary prose-code:px-1 prose-code:py-0.5 prose-code:font-mono prose-code:text-[0.85em] prose-code:font-normal prose-code:before:hidden prose-code:after:hidden",
        "prose-pre:my-2 prose-pre:bg-card prose-pre:text-[14px] prose-pre:leading-[22px]",
        "prose-li:my-0.5 prose-ul:my-2 prose-ol:my-2",
        "prose-table:my-3 prose-table:text-[15px] prose-table:leading-[22px]",
        "prose-thead:text-foreground-strong prose-th:text-foreground-strong prose-td:text-foreground",
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.text}</ReactMarkdown>
    </div>
  );
}
