import { useEventStore, type VoiceState } from "@/store/events";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

// Exhaustive by construction: adding a member to VoiceState without a style
// here fails the TypeScript build instead of rendering an undefined swatch.
const STATE_STYLE: Record<VoiceState, { color: string; ring: string }> = {
  idle:      { color: "bg-blue-500",    ring: "ring-blue-500/40" },
  connecting: { color: "bg-amber-400",  ring: "ring-amber-400/50 animate-pulse" },
  listening: { color: "bg-emerald-500", ring: "ring-emerald-500/50 animate-pulse" },
  thinking:  { color: "bg-yellow-500",  ring: "ring-yellow-500/50 animate-pulse" },
  speaking:  { color: "bg-pink-500",    ring: "ring-pink-500/50 animate-pulse" },
  paused:    { color: "bg-amber-500",   ring: "ring-amber-500/40" },
  error:     { color: "bg-red-500",     ring: "ring-red-500/50" },
};

export function VoiceIndicator() {
  const t = useT();
  const state = useEventStore((s) => s.voiceState);
  const style = STATE_STYLE[state] ?? STATE_STYLE.idle;
  const label = t(`voice_state.${state}`);
  return (
    <div
      role="status"
      aria-label={`${t("voice_state.indicator_label")}: ${label}`}
      className={cn(
        "h-8 w-8 rounded-full ring-4 transition-colors",
        style.color,
        style.ring,
      )}
      title={label}
    />
  );
}
