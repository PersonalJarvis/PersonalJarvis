import { useOpenPairConversation } from "./PairConversation";
import { useState } from "react";
import { ChatMarkdown } from "./ChatMarkdown";
import { ArrowRight, ChevronDown, MessagesSquare } from "lucide-react";

import { AgentSwatch } from "@/components/society/AgentSwatch";
import type { FigureRecipe } from "@/components/society/figures/figureRecipe";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import type { InternalMessageItem } from "./reduce";

/** Enough of a roster row to paint a face; the full SocietyAgent also fits. */
export interface InternalParticipant {
  name: string;
  figure: FigureRecipe | null;
  palette: { primary: string; secondary: string; accent: string };
}

/**
 * One sentence, at most — what a collapsed agent-to-agent message shows.
 * Markdown marks, code fences and line breaks are flattened so the row reads
 * as a sentence, never as a wall.
 */
export function internalPreview(text: string, max = 140): string {
  const flat = text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[`*_#>~|]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!flat) return "";
  const first = flat.split(/(?<=[.!?…])\s/)[0] ?? flat;
  const sentence = first.trim();
  if (sentence.length <= max) return sentence;
  return `${sentence.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

/** A collapsed message needs a fold only when the preview hides something. */
export function internalNeedsFold(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return false;
  return internalPreview(trimmed) !== trimmed.replace(/\s+/g, " ").trim();
}

function Initials({ name, size = 20 }: { name: string; size?: number }) {
  const letter = name.trim().charAt(0).toUpperCase() || "?";
  return (
    <span
      aria-hidden
      className="inline-flex shrink-0 items-center justify-center rounded-full border border-border bg-secondary font-medium text-muted-foreground"
      style={{ width: size, height: size, fontSize: size * 0.5 }}
    >
      {letter}
    </span>
  );
}

function Face({ who, size = 20 }: { who?: InternalParticipant | null; size?: number }) {
  if (!who) return null;
  if (who.figure || who.palette) {
    try {
      return <AgentSwatch agent={who} size={size} />;
    } catch {
      return <Initials name={who.name} size={size} />;
    }
  }
  return <Initials name={who.name} size={size} />;
}

/**
 * An agent-to-agent message, folded by default.
 *
 * Agent chats fill with long internal turns; showing each one whole buries
 * the conversation. The card therefore reads as one compact row — sender
 * face + name, arrow, recipient face + name, one-sentence preview, delivery
 * state — and a click unfolds the whole message. Short (single-sentence)
 * messages show whole with no toggle.
 *
 * Shared by the lead's timeline and the society's canonical agent chat.
 */
export function InternalMessageBubble({
  item,
  sender,
  recipient,
  recipientName,
}: {
  item: InternalMessageItem;
  sender?: InternalParticipant | null;
  recipient?: InternalParticipant | null;
  recipientName?: string;
}) {
  const t = useT();
  const openPair = useOpenPairConversation();
  const canOpenPair = Boolean(openPair && item.message.sender_kind !== "user");
  const [open, setOpen] = useState(false);
  const text = item.message.text.trim();
  const preview = internalPreview(text);
  const foldable = internalNeedsFold(text);
  const shown = !foldable || open;

  const senderName = sender?.name || item.message.sender_name;
  const toName = item.outgoing?.recipientName || recipient?.name || recipientName || "";
  const counterpart = item.outgoing
    ? { id: item.outgoing.recipientId, name: toName }
    : { id: item.message.sender_id, name: senderName };
  const status = item.message.status;
  const statusLabel = item.outgoing && status !== "failed"
    ? t("agent_chat.delivery_sent") : t(`agent_chat.delivery_${status}`);
  const statusTone =
    status === "failed"
      ? "text-destructive"
      : status === "delivered"
        ? "text-success"
        : "text-muted-foreground";

  return (
    <div
      data-testid="agent-message-internal"
      data-message-id={item.id}
      data-state={!foldable ? "whole" : open ? "open" : "folded"}
      className="max-w-[85%] self-start overflow-hidden rounded-xl border border-border bg-card"
    >
      <button
        type="button"
        onClick={() => canOpenPair ? openPair?.(counterpart) : foldable && setOpen((v) => !v)}
        aria-expanded={!canOpenPair && foldable ? open : undefined}
        aria-label={
          canOpenPair ? t("agent_chat.pair_open") : foldable
            ? open
              ? t("agent_chat.internal_collapse")
              : t("agent_chat.internal_expand")
            : undefined
        }
        data-testid="agent-message-internal-toggle"
        disabled={!canOpenPair && !foldable}
        className={cn(
          "flex w-full items-center gap-1.5 px-3 py-2 text-left",
          (canOpenPair || foldable) && "cursor-pointer hover:bg-secondary/50",
          !canOpenPair && !foldable && "cursor-default",
        )}
      >
        <MessagesSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <Face who={sender ?? { name: senderName, figure: null, palette: { primary: "#888", secondary: "#555", accent: "#aaa" } }} />
        <span className="truncate text-xs font-medium text-foreground">{senderName}</span>
        {toName ? (
          <>
            <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
            <Face who={recipient ?? { name: toName, figure: null, palette: { primary: "#888", secondary: "#555", accent: "#aaa" } }} />
            <span className="truncate text-xs text-muted-foreground">{toName}</span>
          </>
        ) : null}
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          <span
            className={cn(
              "hidden rounded-full border border-border px-1.5 py-px text-[10px] sm:inline",
              "text-muted-foreground",
            )}
          >
            {t("agent_chat.internal_message")}
          </span>
          <span className={cn("flex items-center gap-1 text-[11px]", statusTone)} aria-live="polite">
            <span
              aria-hidden
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                status === "failed" ? "bg-destructive" : status === "delivered" ? "bg-success" : "bg-muted-foreground",
              )}
            />
            {statusLabel}
          </span>
          {foldable && !canOpenPair ? (
            <ChevronDown
              className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open && "rotate-180")}
              aria-hidden
            />
          ) : null}
        </span>
      </button>
      <div className="px-3 pb-2.5 pl-[30px]" onClick={canOpenPair ? () => openPair?.(counterpart) : undefined} style={canOpenPair ? { cursor: "pointer" } : undefined}>
        {shown ? (
          <div data-testid="agent-message-internal-full" className="whitespace-pre-wrap text-sm leading-relaxed text-foreground [overflow-wrap:anywhere]">
            <ChatMarkdown text={text} />
          </div>
        ) : (
          <p
            data-testid="agent-message-internal-preview"
            className="truncate text-xs leading-relaxed text-muted-foreground"
            title={text}
          >
            {preview}
          </p>
        )}
        {item.message.error ? <div className="mt-1 text-xs text-destructive">{item.message.error}</div> : null}
      </div>
    </div>
  );
}
