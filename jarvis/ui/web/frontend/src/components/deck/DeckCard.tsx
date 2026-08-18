import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { ArrowUpRight } from "lucide-react";
import { HudFrameOverlay, HudLamp, useElementSize, type HudVariant } from "@/components/deck/HudFrame";
import { cn } from "@/lib/utils";

/**
 * One instrument on the deck.
 *
 * Deliberately NOT a filled box: the app's convention (index.css, "Frontend
 * theming") is that the wallpaper shines through untinted and the stage's
 * text halo keeps ink readable on it. So a card is a HUD frame (see
 * HudFrame.tsx — bracket, chamfer or rail, chosen per card so the stage does
 * not read as a grid of identical rectangles), a title strip in plain words
 * with a status lamp, and its content.
 *
 * `onOpen` makes the title a jump into the section the card is a window onto;
 * `actions` sits at the right of the strip (expand, centre, …).
 */
export function DeckCard({
  icon: Icon,
  title,
  meta,
  onOpen,
  openLabel,
  live,
  variant = "chamfer",
  actions,
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
  /** Something is happening in here right now — the frame and lamp light up. */
  live?: boolean;
  variant?: HudVariant;
  /** Extra controls at the right of the title strip. */
  actions?: ReactNode;
  className?: string;
  bodyClassName?: string;
  children: ReactNode;
}) {
  const [ref, size] = useElementSize<HTMLElement>();

  const head = (
    <>
      <HudLamp on={Boolean(live)} />
      <Icon className={cn("h-3 w-3 shrink-0", live ? "text-primary" : "text-muted-foreground")} />
      <span
        className={cn(
          "truncate font-mono text-[10px] uppercase tracking-[0.2em]",
          live ? "text-primary" : "text-foreground/80",
        )}
      >
        {title}
      </span>
      {meta !== undefined && (
        <span className="ml-1 shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
          {meta}
        </span>
      )}
      {onOpen && (
        <ArrowUpRight className="ml-auto h-3 w-3 shrink-0 opacity-0 transition-opacity group-hover/card:opacity-100" />
      )}
    </>
  );

  return (
    <section
      ref={ref}
      className={cn("group/card relative flex min-h-0 flex-col", variant === "rail" ? "pl-2" : "", className)}
    >
      <HudFrameOverlay variant={variant} w={size.w} h={size.h} live={live} />

      <div className="relative flex items-center gap-1.5 px-2.5 py-1.5">
        {onOpen ? (
          <button
            type="button"
            onClick={onOpen}
            title={openLabel}
            className="flex min-w-0 flex-1 items-center gap-1.5 text-left transition-colors hover:[&_span]:text-primary"
          >
            {head}
          </button>
        ) : (
          <div className="flex min-w-0 flex-1 items-center gap-1.5">{head}</div>
        )}
        {actions && <div className="ml-auto flex shrink-0 items-center gap-1">{actions}</div>}
      </div>

      <div className={cn("relative min-h-0 flex-1 overflow-hidden px-2.5 pb-2", bodyClassName)}>
        {children}
      </div>
    </section>
  );
}

/** A small square control for the title strip (expand, centre, close). */
export function DeckIconButton({
  icon: Icon,
  label,
  onClick,
  active,
  className,
}: {
  icon: LucideIcon;
  label: string;
  onClick: () => void;
  active?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cn(
        "flex h-5 w-5 items-center justify-center border border-border/70 text-muted-foreground transition-colors",
        "hover:border-primary/60 hover:text-primary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/60",
        active && "border-primary/60 text-primary",
        className,
      )}
    >
      <Icon className="h-3 w-3" />
    </button>
  );
}
