import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * The one card recipe: an OBJECT sized to its content, never a wall.
 *
 * --card may only paint something bounded by its content or by a max-width. A
 * wrapper carrying `flex-1`, `h-full`, `w-full` or `inset-0` is a full-bleed
 * region and belongs at --background or --sidebar; putting this component
 * around one is the mistake that produced "grey slabs" twice.
 *
 * Separation is fill first: the surface does the work, the hairline only
 * finishes the edge (--border is LIGHTER than --card, so on near-black it
 * reads as a lit top edge rather than a drawn outline), and the rim shadow
 * replaces the drop shadow that is mathematically invisible on this ground.
 * No `shadow-float` here — that belongs to menus, dialogs and tooltips.
 *
 * The 20px block padding lives on the parts rather than on the root, so a card
 * can also hold a full-bleed child — a table, a list, a code block — without
 * cancelling an outer padding with a negative margin.
 */
const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "rounded-lg border border-border bg-card text-foreground shadow-rim",
        className,
      )}
      {...props}
    />
  ),
);
Card.displayName = "Card";

const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("flex flex-col gap-1.5 p-5", className)} {...props} />
  ),
);
CardHeader.displayName = "CardHeader";

const CardTitle = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("text-[15px] font-semibold text-foreground-strong", className)}
      {...props}
    />
  ),
);
CardTitle.displayName = "CardTitle";

const CardDescription = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("text-[13px] text-muted-foreground", className)} {...props} />
  ),
);
CardDescription.displayName = "CardDescription";

const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("p-5 pt-0", className)} {...props} />
  ),
);
CardContent.displayName = "CardContent";

const CardFooter = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("flex items-center gap-2 p-5 pt-0", className)} {...props} />
  ),
);
CardFooter.displayName = "CardFooter";

export { Card, CardHeader, CardFooter, CardTitle, CardDescription, CardContent };
