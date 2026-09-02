import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/**
 * A badge: 24 px tall, 8 px radius, the xs step, and quiet unless it carries
 * a status.
 *
 * The neutral default rests on --secondary with meta ink. The four coloured
 * variants (`accent`, `success`, `warning`, `destructive`) are SOFT washes —
 * the hue at 12 % under text in the hue — so a status never becomes the
 * loudest object on a screen the way a solid fill did. `solid` is the rare
 * deliberate --primary fill for the one chip that must be looked at.
 *
 * The older names (`life`, `fault`, `degraded`, `info`) stay as aliases onto
 * the soft variants so their call sites keep compiling and land on the new
 * look without an edit. A badge does not hover: it is a label, not a control.
 * It is also the ONE place `uppercase` is allowed, and callers opt into it.
 */
const badgeVariants = cva(
  "inline-flex h-6 items-center gap-1 whitespace-nowrap rounded-md border px-2 text-xs font-medium ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 [&>svg]:h-3.5 [&>svg]:w-3.5 [&>svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "border-border bg-secondary text-muted-foreground",
        secondary: "border-transparent bg-secondary text-muted-foreground",
        outline: "border-border-strong bg-transparent text-muted-foreground",
        solid: "border-transparent bg-primary text-primary-foreground",
        /** Informational: selected, new, a count worth noting. */
        accent: "border-accent/20 bg-accent-soft text-accent",
        /** Running, live, connected, on, passed. */
        success: "border-success/20 bg-success/[0.12] text-success",
        /** Paused, stale, partial, over quota. */
        warning: "border-warning/20 bg-warning/[0.12] text-warning",
        /** Failed, blocked, disconnected, error. */
        destructive: "border-destructive/20 bg-destructive/[0.12] text-destructive",
        // Aliases retained for existing call sites; use the four above.
        life: "border-success/20 bg-success/[0.12] text-success",
        fault: "border-destructive/20 bg-destructive/[0.12] text-destructive",
        degraded: "border-warning/20 bg-warning/[0.12] text-warning",
        info: "border-accent/20 bg-accent-soft text-accent",
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
