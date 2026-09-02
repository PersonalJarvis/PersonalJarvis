import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * The one multi-line field. Same rim, ground, placeholder ink and focus ring
 * as `Input`; the base step with a 20 px line by default, and callers that
 * hold running prose raise it to `text-base leading-7`.
 */
const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "flex min-h-[80px] w-full rounded-md border border-border-strong bg-input px-3 py-2 text-base text-foreground",
      "placeholder:text-foreground-faint",
      "transition-colors focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
      "disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";

export { Textarea };
