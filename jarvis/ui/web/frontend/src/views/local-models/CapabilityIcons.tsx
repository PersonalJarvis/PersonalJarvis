import { AudioLines, Brain, Eye, Wrench, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * A model's capabilities as ONE compact icon row — tools, thinking, vision,
 * audio — each glyph carrying its name as a tooltip. Replaces the four text
 * chips that used to repeat on every model row and card (v4, 2026-09-02).
 * Unknown capabilities are skipped: the row says what a glyph exists for.
 */
const CAPABILITY_ICON: Record<string, LucideIcon> = {
  tools: Wrench,
  thinking: Brain,
  vision: Eye,
  audio: AudioLines,
};

export function CapabilityIcons({
  capabilities,
  className,
  testId,
}: {
  capabilities: readonly string[];
  className?: string;
  testId?: string;
}) {
  const known = capabilities.filter((c) => c in CAPABILITY_ICON);
  if (known.length === 0) return null;
  return (
    <span
      className={cn("inline-flex items-center gap-1", className)}
      data-testid={testId}
      role="list"
      aria-label={known.join(", ")}
    >
      {known.map((cap) => {
        const Icon = CAPABILITY_ICON[cap];
        return (
          <span
            key={cap}
            role="listitem"
            title={cap}
            aria-label={cap}
            data-capability={cap}
            className="grid h-6 w-6 place-items-center rounded-md bg-secondary text-muted-foreground"
          >
            <Icon aria-hidden className="h-3.5 w-3.5" />
          </span>
        );
      })}
    </span>
  );
}
