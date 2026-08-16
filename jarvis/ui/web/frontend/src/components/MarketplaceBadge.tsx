import { Store } from "lucide-react";
import { translate } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * "This came from the marketplace."
 *
 * One component for all three kinds — a plugin card, a skill row, a wallpaper
 * tile — because the whole point is that the mark reads the same everywhere:
 * somebody who installed a skill yesterday should recognise the same badge on
 * a wallpaper today without being told.
 *
 * Colours come from theme tokens only, so it stays legible in light and dark
 * mode and over the wallpaper-tinted panes.
 */
export function MarketplaceBadge({
  publisher,
  compact = false,
  className,
}: {
  /** GitHub login of the publisher, when the entry carries one. */
  publisher?: string | null;
  /** Icon only — for a dense tile where a word would not fit. */
  compact?: boolean;
  className?: string;
}) {
  const label = translate("marketplace_origin.badge");
  const title = publisher
    ? `${translate("marketplace_origin.tooltip")} · ${publisher}`
    : translate("marketplace_origin.tooltip");
  return (
    <span
      title={title}
      aria-label={title}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border border-primary/30",
        "bg-primary/10 text-primary",
        compact ? "px-1.5 py-0.5" : "px-2 py-0.5",
        "text-[10px] font-medium uppercase tracking-wide",
        className,
      )}
    >
      <Store className="h-3 w-3" aria-hidden />
      {!compact && <span>{label}</span>}
    </span>
  );
}
