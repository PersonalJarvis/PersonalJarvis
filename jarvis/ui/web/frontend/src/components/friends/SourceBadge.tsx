import { MessageCircle, Zap } from "lucide-react";
import type { ChannelLink } from "@/hooks/useFriends";
import { cn } from "@/lib/utils";

/**
 * Visualisiert, ueber welche Channels ein Friend erreichbar ist.
 *
 * - Telegram-only:  TG-Pille
 * - Jarvis-only:    Jarvis-Pille
 * - Beide:          beide Pillen kompakt nebeneinander
 *
 * Reine Display-Komponente — alle Logik lebt in der Channels-Liste.
 */
export function SourceBadge({
  channels,
  className,
}: {
  channels: ChannelLink[];
  className?: string;
}) {
  const hasTelegram = channels.some((c) => c.channel === "telegram");
  const hasJarvis = channels.some((c) => c.channel === "jarvis_pubkey");

  if (!hasTelegram && !hasJarvis) {
    return (
      <span
        className={cn(
          "inline-flex items-center rounded-full border border-border/40 bg-muted/30 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-muted-foreground",
          className
        )}
      >
        no source
      </span>
    );
  }

  return (
    <span
      className={cn("inline-flex items-center gap-0.5", className)}
      aria-label={[hasTelegram && "Telegram", hasJarvis && "Jarvis"]
        .filter(Boolean)
        .join(" + ")}
    >
      {hasTelegram && (
        <span
          title="Telegram"
          className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-sky-500/15 text-sky-400"
        >
          <MessageCircle className="h-2.5 w-2.5" />
        </span>
      )}
      {hasJarvis && (
        <span
          title="Jarvis Federation"
          className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-primary/15 text-primary"
        >
          <Zap className="h-2.5 w-2.5" />
        </span>
      )}
    </span>
  );
}
