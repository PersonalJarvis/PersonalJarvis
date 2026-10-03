import { AudioLines } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ChatRow } from "@/components/home/chatRows";

/**
 * The small mark in front of a history row that says what kind of chat it is:
 * an open ring for a typed chat, sound bars for a voice chat — the same bars
 * the composer's voice button wears. Both sit in one 14 px box in the same
 * muted tone, so the
 * column reads as one list with two clearly different kinds in it.
 */
export function ChatKindMark({
  kind,
  active = false,
  className,
}: {
  kind: ChatRow["kind"];
  active?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      data-kind-mark={kind}
      className={cn(
        "flex h-3.5 w-3.5 shrink-0 items-center justify-center",
        active ? "text-foreground" : "text-muted-foreground",
        className,
      )}
    >
      {kind === "voice" ? (
        <AudioLines className="h-3.5 w-3.5" strokeWidth={1.75} />
      ) : (
        <span className="h-[7px] w-[7px] rounded-full border-[1.5px] border-current" />
      )}
    </span>
  );
}
