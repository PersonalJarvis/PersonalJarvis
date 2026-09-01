import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/**
 * A chip. Pill-shaped, 11px, and quiet unless it is carrying a status.
 *
 * The default used to be `bg-foreground/70` — a near-white slab that made a
 * neutral label the loudest object on a screen and inverted the ink ramp. It
 * now rests on --secondary, the same lift every other small surface uses, and
 * the three variants that mean something (`life`, `fault`, `degraded`) are the
 * only ones carrying hue. A status is never drawn in --foreground or
 * --primary: white "cancelled" outshouting a green "running" is exactly the
 * inversion those variants exist to prevent.
 *
 * `solid` is the rare deliberate --primary fill; reach for it when a chip is
 * genuinely the one thing to look at, never as a default.
 *
 * A badge does not hover: it is a label, not a control. It is also the ONE
 * place in the product where `uppercase` is allowed, and callers opt into it.
 */
const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors ring-offset-background focus:outline-none focus:ring-2 focus:ring-border-strong focus:ring-offset-2",
  {
    variants: {
      variant: {
        default: "border-border bg-secondary text-foreground",
        secondary: "border-transparent bg-secondary text-muted-foreground",
        outline: "border-border-strong bg-transparent text-muted-foreground",
        solid: "border-transparent bg-primary text-primary-foreground",
        /** Running, live, connected, on, passed. */
        life: "border-transparent bg-success text-primary-foreground",
        /** Failed, blocked, disconnected, error. */
        fault: "border-transparent bg-destructive text-destructive-foreground",
        /** Stale, partial, needs attention. */
        degraded: "border-transparent bg-warning text-primary-foreground",
        /**
         * Retained so the ~30 existing call sites keep compiling; `fault` is
         * the name to use for new code.
         */
        destructive: "border-transparent bg-destructive text-destructive-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
