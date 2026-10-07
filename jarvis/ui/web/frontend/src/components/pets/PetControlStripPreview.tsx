import { Bell, Mic, Phone, SquarePen, Volume2 } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The desktop strip's own palette (`ui/orb/controls.py`, `PET_FILL` & co).
 * Literal on purpose, like the overlay-style previews: this is a picture of a
 * desktop object that is always dark, whatever the app's theme — on a light
 * page it looks exactly as the real strip does on a light wallpaper.
 */
const STRIP_FILL = "#0d1117";
const STRIP_ICON = "#e8e9ee";
const STRIP_DIVIDER = "#181c25";
/** The indicator strokes' sky blue (`PET_INDICATOR_SKY`). */
const STRIP_INDICATOR = "#7ebaff";

/**
 * A still picture of the control strip the desktop pet and the Jarvis Bar
 * share: the pen in its own filled disc (new chat; the pet has a bell there), then one filled pill holding the
 * microphone, the three-stroke voice indicator and the speaker, split by faint dividers, then the
 * phone that calls Jarvis (its handset lies flat to hang up). Shown on
 * the settings page so the user knows what sits under the pet before
 * switching it on.
 *
 * Deliberately NOT a set of buttons: the real controls live on the desktop,
 * and a look-alike that reacted to clicks here would promise something it
 * cannot do. Hence no roles, no focus, and `aria-hidden`.
 */
export function PetControlStripPreview({
  size = "md",
  className,
  companion = false,
}: {
  size?: "sm" | "md";
  className?: string;
  companion?: boolean;
}) {
  const small = size === "sm";
  const height = small ? "h-7" : "h-10";
  const disc = small ? "w-7" : "w-10";
  const slot = small ? "w-7" : "w-10";
  const icon = small ? "h-3.5 w-3.5" : "h-[18px] w-[18px]";
  const divider = small ? "h-3" : "h-5";
  return (
    <div
      aria-hidden
      data-testid="pet-strip-preview"
      className={cn("pointer-events-none flex select-none items-center gap-2", className)}
    >
      <span
        className={cn("grid place-items-center rounded-full", height, disc)}
        style={{ backgroundColor: STRIP_FILL, color: STRIP_ICON }}
      >
        {companion ? <Bell className={icon} strokeWidth={2} /> : <SquarePen className={icon} strokeWidth={2} />}
      </span>
      <span
        className={cn("flex items-center rounded-full", height, small ? "px-1" : "px-1.5")}
        style={{ backgroundColor: STRIP_FILL, color: STRIP_ICON }}
      >
        <span className={cn("grid place-items-center", slot)}>
          <Mic className={icon} strokeWidth={2} />
        </span>
        <span className={cn("w-px", divider)} style={{ backgroundColor: STRIP_DIVIDER }} />
        <span className={cn("grid place-items-center", small ? "w-8" : "w-11")}>
          <span className="flex items-center gap-1" data-testid="pet-strip-indicator">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className={cn("rounded-full", small ? "h-2 w-1" : "h-2.5 w-1.5")}
                style={{ backgroundColor: STRIP_INDICATOR }}
              />
            ))}
          </span>
        </span>
        <span className={cn("w-px", divider)} style={{ backgroundColor: STRIP_DIVIDER }} />
        <span className={cn("grid place-items-center", slot)}>
          <Volume2 className={icon} strokeWidth={2} />
        </span>
      </span>
      <span
        data-testid="pet-strip-call"
        className={cn("grid place-items-center rounded-full", height, disc)}
        style={{ backgroundColor: STRIP_FILL, color: STRIP_ICON }}
      >
        <Phone className={icon} strokeWidth={2} />
      </span>
    </div>
  );
}
