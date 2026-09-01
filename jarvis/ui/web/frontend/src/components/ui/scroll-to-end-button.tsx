import { ArrowDown } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The way back to the newest message, for a surface that follows the rule in
 * hooks/useStickToBottom.
 *
 * Render it only while `atEnd` is false — a permanent one would be a control
 * that does nothing most of the time. It floats over whatever sits under the
 * conversation (a composer, the Jarvis bar), so its parent needs `relative`.
 *
 * The label lives under `home.` because that is where the voice stage that
 * first needed it put it; it says nothing home-specific and every surface
 * shares the one string rather than translating the same sentence twice.
 */
export function ScrollToEndButton({
  onClick,
  className,
  testId = "scroll-to-end",
}: {
  onClick: () => void;
  className?: string;
  testId?: string;
}) {
  const t = useT();
  const label = t("home.transcript_to_end");
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      aria-label={label}
      title={label}
      // This button hovers over the conversation, so it is opaque and carries
      // the float shadow and the strong rim — not a 95%-opaque card leaning on
      // a blur. It rests one rung below the floating layer so that hover still
      // has somewhere to go: --card up to --secondary, rather than lighting the
      // border with --primary, which on this ground is pure white.
      className={cn(
        "absolute -top-3 left-1/2 z-10 flex h-8 w-8 -translate-x-1/2 items-center justify-center",
        "rounded-full border border-border-strong bg-card text-muted-foreground shadow-float",
        "transition-colors duration-150 hover:bg-secondary hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
        className,
      )}
    >
      <ArrowDown aria-hidden className="h-4 w-4" />
    </button>
  );
}
