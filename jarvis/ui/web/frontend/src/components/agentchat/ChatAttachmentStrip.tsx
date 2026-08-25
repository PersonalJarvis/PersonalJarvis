import { FileText, ImageIcon, Loader2, X } from "lucide-react";

import type { ChatAttachment } from "@/lib/agentChatApi";
import { fill, useT } from "@/i18n";

/**
 * The files waiting to go in with the next message.
 *
 * Each chip says what was actually LEARNED from the file, not merely that one
 * is attached. That distinction is the whole feature: "screenshot.png" tells
 * the person nothing about whether the model will be able to see it, while
 * "described" and "not described" are the two outcomes they need to tell apart
 * BEFORE pressing Send — and the second happens for real, on any install whose
 * providers cannot see images.
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
}: {
  attachments: ChatAttachment[];
  analyzing: number;
  onRemove: (name: string) => void;
}) {
  const t = useT();
  if (attachments.length === 0 && analyzing === 0) return null;
  return (
    <div
      data-testid="chat-attachments"
      className="flex max-h-20 shrink-0 flex-wrap items-center gap-1.5 overflow-y-auto px-1 scrollbar-jarvis"
    >
      {attachments.map((item) => {
        const read = item.described_by !== "none" && item.detail.length > 0;
        return (
          <span
            key={item.name}
            data-testid={`chat-attachment-${item.name}`}
            title={
              read
                ? `${item.detail.slice(0, 400)}${item.detail.length > 400 ? "…" : ""}`
                : item.note || item.name
            }
            className={
              "flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] " +
              (read
                ? "border-primary/40 bg-primary/10 text-foreground"
                : "border-border text-muted-foreground")
            }
          >
            {item.kind === "image" ? (
              <ImageIcon className="h-3 w-3 shrink-0" aria-hidden />
            ) : (
              <FileText className="h-3 w-3 shrink-0" aria-hidden />
            )}
            <span className="max-w-[12rem] truncate font-mono">{item.name}</span>
            <span className="shrink-0 text-[10px] text-muted-foreground">
              {read
                ? item.described_by === "vision"
                  ? t("agent_chat.attach_described")
                  : t("agent_chat.attach_text_read")
                : t("agent_chat.attach_not_described")}
            </span>
            <button
              type="button"
              aria-label={fill(t("agent_chat.attach_remove"), { name: item.name })}
              data-testid={`chat-attachment-remove-${item.name}`}
              onClick={() => onRemove(item.name)}
              className="shrink-0 rounded text-muted-foreground transition-colors hover:text-destructive"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        );
      })}
      {analyzing > 0 && (
        <span
          data-testid="chat-attachment-working"
          className="flex items-center gap-1.5 rounded-md border border-dashed border-border px-2 py-1 text-[11px] text-muted-foreground"
        >
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          {t("agent_chat.attach_working")}
        </span>
      )}
    </div>
  );
}
