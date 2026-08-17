import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * One panel on the deck.
 *
 * Deliberately NOT a filled box: the app's convention (index.css, "Frontend
 * theming") is that the wallpaper shines through untinted and the stage's
 * text halo keeps ink readable on it. So a card is a hairline, an eyebrow
 * that names it in plain words, and its content — nothing that could hide
 * the artwork behind a slab.
 *
 * `onOpen` makes the whole eyebrow a jump into the section the card is a
 * window onto; the arrow says so.
 */
export function DeckCard({
  icon: Icon,
  title,
  meta,
  onOpen,
  openLabel,
  live,
  className,
  bodyClassName,
  children,
}: {
  icon: LucideIcon;
  title: string;
  /** Small trailing figure or word next to the title (a count, a state). */
  meta?: ReactNode;
  onOpen?: () => void;
  openLabel?: string;
  /** Something is happening in here right now — the eyebrow lights up. */
  live?: boolean;
  className?: string;
  bodyClassName?: string;
  children: ReactNode;
}) {
  const head = (
    <>
      <Icon className={cn("h-3 w-3 shrink-0", live ? "text-primary" : "text-muted-foreground")} />
      <span
        className={cn(
          "truncate font-mono text-[10px] uppercase tracking-[0.2em]",
          live ? "text-primary" : "text-muted-foreground",
        )}
      >
        {title}
      </span>
      {meta !== undefined && (
        <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
          {meta}
        </span>
      )}
      {onOpen && (
        <ArrowUpRight
          className={cn(
            "h-3 w-3 shrink-0 opacity-0 transition-opacity group-hover/card:opacity-100",
            meta === undefined && "ml-auto",
          )}
        />
      )}
    </>
  );

  return (
    <section
      className={cn(
        "group/card flex min-h-0 flex-col rounded-lg border border-border/70",
        live && "border-primary/40",
        className,
      )}
    >
      {onOpen ? (
        <button
          type="button"
          onClick={onOpen}
          title={openLabel}
          className="flex w-full items-center gap-2 border-b border-border/60 px-2.5 py-1.5 text-left transition-colors hover:bg-primary/5"
        >
          {head}
        </button>
      ) : (
        <div className="flex items-center gap-2 border-b border-border/60 px-2.5 py-1.5">{head}</div>
      )}
      <div className={cn("min-h-0 flex-1 overflow-hidden px-2.5 py-2", bodyClassName)}>
        {children}
      </div>
    </section>
  );
}
