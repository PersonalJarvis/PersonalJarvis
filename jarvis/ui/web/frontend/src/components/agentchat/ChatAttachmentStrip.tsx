import { FileText, ImageIcon, Loader2, X } from "lucide-react";

import type { ChatAttachment } from "@/lib/agentChatApi";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The files waiting to go in with the next message.
 *
 * Each card shows the picture itself when its bytes passed through this
 * window, and says what was actually LEARNED from the file, not merely that
 * one is attached. That distinction is the whole feature: "screenshot.png"
 * tells the person nothing about whether the model will be able to see it,
 * while "described" and "not described" are the two outcomes they need to
 * tell apart BEFORE pressing Send — and the second happens for real, on any
 * install whose providers cannot see images.
 *
 * A near-twin of the Agentic IDE's terminal strip
 * (components/agentic/PromptAttachments) and deliberately not shared with it:
 * that one is hardcoded English, which is right inside a terminal composer
 * whose every other label is too, and wrong in a chat that speaks the app's
 * language everywhere else.
 */
export function ChatAttachmentStrip({
  attachments,
  analyzing,
  onRemove,
  previews = {},
}: {
  attachments: ChatAttachment[];
  analyzing: number;
  onRemove: (name: string) => void;
  /** A local picture per image attachment, keyed by its name. */
  previews?: Record<string, string>;
}) {
  const t = useT();
  if (attachments.length === 0 && analyzing === 0) return null;
  return (
    <div
      data-testid="chat-attachments"
      className="flex max-h-32 shrink-0 flex-wrap items-center gap-2 overflow-y-auto px-1 scrollbar-jarvis"
    >
      {attachments.map((item) => {
        const read = item.described_by !== "none" && item.detail.length > 0;
        const preview = previews[item.name];
        const Icon = item.kind === "image" ? ImageIcon : FileText;
        return (
          <span
            key={item.name}
            data-testid={`chat-attachment-${item.name}`}
            title={
              read
                ? `${item.detail.slice(0, 400)}${item.detail.length > 400 ? "…" : ""}`
                : item.note || item.name
            }
            className="group/attachment relative flex h-12 max-w-[15rem] items-center gap-2.5 rounded-xl border border-border bg-background/40 py-1 pl-1 pr-2"
          >
            <span className="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-secondary text-muted-foreground">
              {preview ? (
                <img src={preview} alt="" className="h-full w-full object-cover" />
              ) : (
                <Icon className="h-4 w-4" aria-hidden />
              )}
            </span>
            <span className="flex min-w-0 flex-col pr-4">
              <span className="truncate text-xs font-medium text-foreground">{item.name}</span>
              <span
                className={cn(
                  "flex items-center gap-1 truncate text-micro",
                  read ? "text-muted-foreground" : "text-warning",
                )}
              >
                <span
                  aria-hidden
                  className={cn("h-1.5 w-1.5 shrink-0 rounded-full", read ? "bg-success" : "bg-warning")}
                />
                {read
                  ? item.described_by === "vision"
                    ? t("agent_chat.attach_described")
                    : t("agent_chat.attach_text_read")
                  : t("agent_chat.attach_not_described")}
              </span>
            </span>
            <button
              type="button"
              aria-label={fill(t("agent_chat.attach_remove"), { name: item.name })}
              data-testid={`chat-attachment-remove-${item.name}`}
              onClick={() => onRemove(item.name)}
              className="absolute right-1 top-1 inline-flex h-5 w-5 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:ring-1 focus-visible:ring-ring"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        );
      })}
      {analyzing > 0 && (
        <span
          data-testid="chat-attachment-working"
          className="flex h-12 items-center gap-2 rounded-xl border border-dashed border-border px-3 text-xs text-muted-foreground"
        >
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          {t("agent_chat.attach_working")}
        </span>
      )}
    </div>
  );
}
