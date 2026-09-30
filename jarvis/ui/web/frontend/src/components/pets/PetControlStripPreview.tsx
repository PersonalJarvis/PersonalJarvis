import { Mic, PenLine, Volume2 } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * A still picture of the control strip the desktop pet carries: the pen disc
 * (new chat), then one pill holding the microphone, the talk orb and the
 * speaker. Shown on the settings page so the user knows what sits under the
 * pet before switching it on.
 *
 * Deliberately NOT a set of buttons: the real controls live on the desktop,
 * and a look-alike that reacted to clicks here would promise something it
 * cannot do. Hence no roles, no focus, and `aria-hidden`.
 */
export function PetControlStripPreview({
  size = "md",
  className,
}: {
  size?: "sm" | "md";
  className?: string;
}) {
  const small = size === "sm";
  const disc = small ? "h-6 w-6" : "h-8 w-8";
  const icon = small ? "h-3 w-3" : "h-3.5 w-3.5";
  const orb = small ? "h-3.5 w-3.5" : "h-[18px] w-[18px]";
  return (
    <div
      aria-hidden
      data-testid="pet-strip-preview"
      className={cn("pointer-events-none flex select-none items-center gap-1.5", className)}
    >
      <span
        className={cn(
          "grid place-items-center rounded-full border border-border-strong bg-card text-foreground-secondary shadow-rim",
          disc,
        )}
      >
        <PenLine className={icon} />
      </span>
      <span
        className={cn(
          "flex items-center rounded-full border border-border-strong bg-card text-foreground-secondary shadow-rim",
          small ? "h-6 gap-2 px-2" : "h-8 gap-3 px-3",
        )}
      >
        <Mic className={icon} />
        <span className={cn("rounded-full bg-accent ring-2 ring-accent/25", orb)} />
        <Volume2 className={icon} />
      </span>
    </div>
  );
}
