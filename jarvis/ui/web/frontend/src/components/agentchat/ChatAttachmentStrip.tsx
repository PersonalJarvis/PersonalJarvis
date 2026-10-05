import { useState } from "react";
import { FileText, Film, ImageIcon, Loader2, Play, X } from "lucide-react";

import { attachmentMedia } from "@/components/agentchat/useChatAttachments";
import type { ChatAttachment } from "@/lib/agentChatApi";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The files waiting to go in with the next message.
 *
 * Each card shows the picture itself — a video's first frame under a play
 * mark — whenever it can be drawn: from the bytes that passed through this
 * window, or from the backend's copy of a file that arrived by path. And it
 * says what was actually LEARNED from the file, not merely that one is
 * attached. That distinction is the whole feature: "screenshot.png" tells the
 * person nothing about whether the model will be able to see it, while
 * "described" and "not described" are the two outcomes they need to tell
 * apart BEFORE pressing Send — and the second happens for real, on any
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
  /** A picture per image or video attachment, keyed by its name. */
  previews?: Record<string, string>;
}) {
  const t = useT();
  // A picture that failed to load (an older backend, a file swept since)
  // falls back to the file's icon instead of an empty frame.
  const [broken, setBroken] = useState<Record<string, boolean>>({});
  if (attachments.length === 0 && analyzing === 0) return null;
  return (
    <div
      data-testid="chat-attachments"
      className="flex max-h-40 shrink-0 flex-wrap items-center gap-2 overflow-y-auto px-1 scrollbar-jarvis"
    >
      {attachments.map((item) => {
        const read = item.described_by !== "none" && item.detail.length > 0;
        const media = attachmentMedia(item);
        const preview = media && !broken[item.name] ? previews[item.name] : undefined;
        const Icon = media === "video" ? Film : item.kind === "image" ? ImageIcon : FileText;
        const markBroken = () => setBroken((prev) => ({ ...prev, [item.name]: true }));
        return (
          <span
            key={item.name}
            data-testid={`chat-attachment-${item.name}`}
            data-media={preview ? media : undefined}
            title={
              read
                ? `${item.detail.slice(0, 400)}${item.detail.length > 400 ? "…" : ""}`
                : item.note || item.name
            }
            className={cn(
              "group/attachment relative flex max-w-[16rem] items-center gap-2.5 rounded-xl border border-border bg-background/40 py-1 pl-1 pr-2",
              preview ? "h-16" : "h-12",
            )}
          >
            <span
              className={cn(
                "relative flex shrink-0 items-center justify-center overflow-hidden rounded-lg bg-secondary text-muted-foreground",
                preview ? "h-14 w-14" : "h-10 w-10",
              )}
            >
              {preview && media === "video" ? (
                <>
                  <video
                    src={preview}
                    muted
                    playsInline
                    preload="metadata"
                    onError={markBroken}
                    className="h-full w-full object-cover"
                  />
                  <span aria-hidden className="pointer-events-none absolute inset-0 flex items-center justify-center">
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-black/55 text-white">
                      <Play className="ml-0.5 h-3 w-3 fill-current" />
                    </span>
                  </span>
                </>
              ) : preview ? (
                <img src={preview} alt="" onError={markBroken} className="h-full w-full object-cover" />
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
