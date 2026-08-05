/**
 * The way back to the app, from a surface that hid it.
 *
 * The coding sections open without the module rail (see `CODING_SECTIONS` in
 * `App.tsx`) — so something on those screens has to say where it went. This is
 * that something: the panel glyph every editor uses for exactly this, in the
 * top-left corner where exactly this button lives in every editor. Familiar
 * shape, familiar place, one click each way.
 *
 * It renders only where the rail is actually hidden. On a screen that has its
 * navigation it would be a button that toggles something already visible, which
 * is how a control stops meaning anything.
 */
import { PanelLeft, PanelLeftClose } from "lucide-react";

import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";

export interface NavRevealButtonProps {
  className?: string;
}

export function NavRevealButton({ className }: NavRevealButtonProps) {
  const revealed = useEventStore((s) => s.navRevealed);
  const setRevealed = useEventStore((s) => s.setNavRevealed);
  const label = revealed ? "Hide the app menu" : "Show the app menu";

  return (
    <button
      type="button"
      data-testid="nav-reveal"
      onClick={() => setRevealed(!revealed)}
      title={label}
      aria-label={label}
      aria-expanded={revealed}
      className={cn(
        "flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground",
        "transition-colors hover:bg-foreground/[0.06] hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        className,
      )}
    >
      {revealed ? (
        <PanelLeftClose className="h-4 w-4" aria-hidden />
      ) : (
        <PanelLeft className="h-4 w-4" aria-hidden />
      )}
    </button>
  );
}
