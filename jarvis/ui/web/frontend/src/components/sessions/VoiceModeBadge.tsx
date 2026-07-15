import { CircleHelp, Radio, Workflow } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import type { KnownVoiceMode, VoiceMode } from "./types";

interface VoiceModeBadgeProps {
  mode: VoiceMode;
  prominence?: "compact" | "prominent";
  className?: string;
}

const MODE_STYLES: Record<KnownVoiceMode, string> = {
  realtime:
    "border-primary/70 bg-primary/20 text-primary shadow-[0_0_14px_hsl(var(--primary)/0.12)]",
  pipeline:
    "border-sky-500/50 bg-sky-500/15 text-sky-700 dark:text-sky-300",
  unknown: "border-border bg-muted/70 text-muted-foreground",
};

export function VoiceModeBadge({
  mode,
  prominence = "compact",
  className,
}: VoiceModeBadgeProps) {
  const t = useT();
  const knownMode: KnownVoiceMode =
    mode === "realtime"
      ? "realtime"
      : mode === "pipeline"
        ? "pipeline"
        : "unknown";
  const modeLabel = t(`voice_mode.${knownMode}`);
  const ModeIcon =
    knownMode === "realtime"
      ? Radio
      : knownMode === "pipeline"
        ? Workflow
        : CircleHelp;

  return (
    <span
      role="group"
      aria-label={`${t("voice_mode.label")}: ${modeLabel}`}
      data-voice-mode={knownMode}
      className={cn(
        "inline-flex shrink-0 items-center rounded-md border font-semibold",
        MODE_STYLES[knownMode],
        prominence === "prominent"
          ? "gap-2 px-2.5 py-1.5 text-xs"
          : "gap-1 px-1.5 py-0.5 text-[10px]",
        className,
      )}
    >
      {prominence === "prominent" && (
        <span className="text-[9px] font-bold uppercase tracking-[0.14em] opacity-75">
          {t("voice_mode.label")}
        </span>
      )}
      <ModeIcon
        aria-hidden="true"
        className={prominence === "prominent" ? "h-4 w-4" : "h-3 w-3"}
      />
      <span>{modeLabel}</span>
    </span>
  );
}
