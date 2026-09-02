import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * The one text field: 36 px, 8 px radius, a --border-strong rim on the input
 * ground, faint-ink placeholder, and the accent on focus. Native `<input>`
 * only — a `<select>` is the `Select` primitive.
 */
const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type = "text", ...props }, ref) => (
    <input
      type={type}
      ref={ref}
      className={cn(
        "flex h-9 w-full rounded-md border border-border-strong bg-input px-3 text-base text-foreground",
        "placeholder:text-foreground-faint",
        "transition-colors focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        "disabled:cursor-not-allowed disabled:opacity-50",
        "file:border-0 file:bg-transparent file:text-base file:font-medium",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export { Input };
