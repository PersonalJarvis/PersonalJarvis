import * as React from "react";
import * as SwitchPrimitives from "@radix-ui/react-switch";
import { cn } from "@/lib/utils";

/**
 * ON is a life signal; OFF is a resting surface.
 *
 * The previous recipe read backwards. ON was a mid grey (`bg-foreground/70`)
 * carrying a dark thumb, while OFF put the brightest pixel in the row on the
 * thumb — so a disabled setting drew more attention than an enabled one. The
 * fix before that one had gone the other way and made OFF invisible by
 * painting the track --input over a near-identical ground.
 *
 * The states now sit on opposite sides of the system's own vocabulary: ON
 * takes --success, because "on" is the same fact as "running" and colour has
 * exactly three jobs; OFF takes --secondary, the lift surface, which is a
 * clear step above both the page and a card in either theme. The thumb is the
 * ink of whichever track it rides on, so the knob is always the readable part
 * and never the loudest.
 */
const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitives.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitives.Root>
>(({ className, ...props }, ref) => (
  <SwitchPrimitives.Root
    className={cn(
      "peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50",
      "data-[state=checked]:bg-success data-[state=unchecked]:bg-secondary",
      className,
    )}
    {...props}
    ref={ref}
  >
    <SwitchPrimitives.Thumb
      className={cn(
        "pointer-events-none block h-4 w-4 rounded-full ring-0 transition-transform duration-150",
        "data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0",
        // On the green track the thumb is the ink that sits on a fill; on the
        // grey track it is meta ink, visible without outshining the label.
        "data-[state=checked]:bg-primary-foreground data-[state=unchecked]:bg-muted-foreground",
      )}
    />
  </SwitchPrimitives.Root>
));
Switch.displayName = SwitchPrimitives.Root.displayName;

export { Switch };
