import { CircleHelp, Radio, Workflow } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import type { KnownVoiceMode, VoiceMode } from "./types";

interface VoiceModeBadgeProps {
  mode: VoiceMode;
  prominence?: "compact" | "prominent";
  className?: string;
}

/**
 * The one badge recipe (24 px, 8 px radius, xs step). Realtime wears the
 * accent wash — it is the state worth noticing — pipeline and unknown stay
 * neutral. `prominent` adds the word "Mode" before the value.
 */
const MODE_STYLES: Record<KnownVoiceMode, string> = {
  realtime: "border-accent/20 bg-accent-soft text-accent",
  pipeline: "border-border bg-secondary text-foreground",
  unknown: "border-border bg-secondary text-muted-foreground",
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
      title={modeLabel}
      className={cn(
        "inline-flex h-6 shrink-0 items-center gap-1.5 rounded-md border text-xs font-medium",
        prominence === "prominent" ? "px-2" : "w-6 justify-center",
        MODE_STYLES[knownMode],
        className,
      )}
    >
      <ModeIcon aria-hidden="true" className="h-3.5 w-3.5" />
      {prominence === "prominent" && (
        <span className="opacity-70">{t("voice_mode.label")}</span>
      )}
      {/* Compact rows show the glyph alone; the word stays for readers. */}
      <span className={prominence === "prominent" ? undefined : "sr-only"}>{modeLabel}</span>
    </span>
  );
}
