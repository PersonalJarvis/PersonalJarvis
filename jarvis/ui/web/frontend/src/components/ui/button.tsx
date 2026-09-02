import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/*
 * The ONE button. Weight 500, 8 px radius, the base type step, and a focus
 * ring in the accent — the same ring every interactive element in the app
 * wears, so keyboard focus is one colour everywhere.
 *
 * The interaction ladder only ever goes UP: `outline`, `ghost` and `secondary`
 * answer a pointer with the next surface step, never with --primary (which in
 * dark mode is near-white and would paint a slab under the cursor).
 *
 *   default      the primary action: white on dark, black on light
 *   secondary    a lifted control that hovers to --surface-raised
 *   outline      a strong rim on a transparent ground
 *   ghost        no ground at rest, --secondary under the pointer
 *   link         the accent, underlined on hover
 *   destructive  red fill; only for the action that deletes or stops
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-base font-medium ring-offset-background transition-[background-color,border-color,color,transform] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 motion-safe:active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 [&>svg]:h-4 [&>svg]:w-4 [&>svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        destructive: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
        outline:
          "border border-border-strong bg-transparent text-foreground hover:bg-secondary",
        secondary: "bg-secondary text-secondary-foreground hover:bg-surface-raised",
        ghost: "text-foreground hover:bg-secondary",
        link: "h-auto px-0 text-accent underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4",
        sm: "h-8 px-3 text-sm",
        lg: "h-10 px-5",
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
