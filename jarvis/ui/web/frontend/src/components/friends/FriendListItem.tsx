import type { FriendItem } from "@/hooks/useFriends";
import { SourceBadge } from "./SourceBadge";
import { cn } from "@/lib/utils";

/**
 * Ein Eintrag in der FriendsList (linke Seite des Chat-Tabs).
 *
 * Layout: Avatar-Initial + Name + Source-Badges + Note (truncated).
 * Selected-State markiert via Border + Background.
 */
export function FriendListItem({
  friend,
  selected,
  onClick,
}: {
  friend: FriendItem;
  selected: boolean;
  onClick: () => void;
}) {
  const initial = friend.display_name.slice(0, 1).toUpperCase();

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-start gap-3 rounded-lg border px-3 py-2 text-left transition-colors",
        selected
          ? "border-primary/40 bg-primary/10"
          : "border-transparent hover:border-border/60 hover:bg-card/40"
      )}
    >
      <div
        className={cn(
          "flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full border text-sm font-medium",
          selected
            ? "border-primary/40 bg-primary/15 text-primary"
            : "border-border bg-muted/30 text-muted-foreground"
        )}
        aria-hidden
      >
        {initial}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-sm font-medium text-foreground">
            {friend.display_name}
          </span>
          <SourceBadge channels={friend.channels} />
        </div>
        {friend.note && (
          <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
            {friend.note}
          </div>
        )}
      </div>
    </button>
  );
}
