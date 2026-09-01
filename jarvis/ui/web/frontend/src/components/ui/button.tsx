import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/*
 * The interaction ladder only ever goes UP.
 *
 * `ghost` and `outline` used to hover to --accent, which in dark mode is pure
 * white: brushing a toolbar painted a white slab where a quiet control had
 * been. Both now answer with --secondary, one step up the surface ladder in
 * either theme, and keep their ink at --foreground.
 *
 * Focus is a --border-strong ring rather than --ring: a ring is a rim, and a
 * rim is never the brightest thing on the screen.
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-[background-color,border-color,color,transform] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong focus-visible:ring-offset-2 motion-safe:active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        destructive: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
        outline: "border border-border-strong bg-transparent hover:bg-secondary hover:text-foreground",
        // A lift-resting control answers with the float step above it.
        secondary: "bg-secondary text-secondary-foreground hover:bg-popover",
        ghost: "hover:bg-secondary hover:text-foreground",
        // --primary is a fill, never running text, so a link button reads in
        // body ink and marks itself with the underline instead.
        link: "text-foreground underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 rounded-md px-3 text-[13px]",
        lg: "h-10 rounded-md px-8",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
