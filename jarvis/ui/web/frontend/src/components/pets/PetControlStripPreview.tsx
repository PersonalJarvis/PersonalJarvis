import { Mic, Phone, SquarePen, Volume2 } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The desktop strip's own palette (`ui/orb/controls.py`, `PET_FILL` & co).
 * Literal on purpose, like the overlay-style previews: this is a picture of a
 * desktop object that is always dark, whatever the app's theme — on a light
 * page it looks exactly as the real strip does on a light wallpaper.
 */
const STRIP_FILL = "#171b26";
const STRIP_ICON = "#e8e9ee";
const STRIP_DIVIDER = "#303748";
/** The phone disc's call green (`PET_CALL_FILL`); it turns red during a call. */
const CALL_FILL = "#16a34a";
const CALL_ICON = "#ffffff";
/** The glossy talk orb: lit from the top left, deep blue at the rim. */
const ORB_GRADIENT =
  "radial-gradient(circle at 32% 30%, #e6f1ff 0 7%, #a9d0ff 15%, #4a7cf5 55%, #2747c8 100%)";

/**
 * A still picture of the control strip the desktop pet carries: the pen in
 * its own filled disc (new chat), then one filled pill holding the
 * microphone, the talk orb and the speaker, split by faint dividers, then the
 * green phone that calls Jarvis (red to hang up during a call). Shown on
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
}: {
  size?: "sm" | "md";
  className?: string;
}) {
  const small = size === "sm";
  const height = small ? "h-7" : "h-10";
  const disc = small ? "w-7" : "w-10";
  const slot = small ? "w-7" : "w-10";
  const icon = small ? "h-3.5 w-3.5" : "h-[18px] w-[18px]";
  const orb = small ? "h-[22px] w-[22px]" : "h-8 w-8";
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
        <SquarePen className={icon} strokeWidth={2} />
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
          <span
            data-testid="pet-strip-orb"
            className={cn("rounded-full", orb)}
            style={{ backgroundImage: ORB_GRADIENT }}
          />
        </span>
        <span className={cn("w-px", divider)} style={{ backgroundColor: STRIP_DIVIDER }} />
        <span className={cn("grid place-items-center", slot)}>
          <Volume2 className={icon} strokeWidth={2} />
        </span>
      </span>
      <span
        data-testid="pet-strip-call"
        className={cn("grid place-items-center rounded-full", height, disc)}
        style={{ backgroundColor: CALL_FILL, color: CALL_ICON }}
      >
        <Phone className={icon} strokeWidth={2} />
      </span>
    </div>
  );
}
