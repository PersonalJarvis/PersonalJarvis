import { useEventStore, type VoiceState } from "@/store/events";
import { cn } from "@/lib/utils";

const STATE_STYLE: Record<VoiceState, { color: string; label: string; ring: string }> = {
  idle:      { color: "bg-blue-500",   label: "Idle",      ring: "ring-blue-500/40" },
  listening: { color: "bg-emerald-500", label: "Listening", ring: "ring-emerald-500/50 animate-pulse" },
  thinking:  { color: "bg-yellow-500", label: "Thinking",  ring: "ring-yellow-500/50 animate-pulse" },
  speaking:  { color: "bg-pink-500",   label: "Speaking",  ring: "ring-pink-500/50 animate-pulse" },
  error:     { color: "bg-red-500",    label: "Error",     ring: "ring-red-500/50" },
};

export function VoiceIndicator() {
  const state = useEventStore((s) => s.voiceState);
  const style = STATE_STYLE[state];
  return (
    <div
      role="status"
      aria-label={`Voice state: ${style.label}`}
      className={cn(
        "h-8 w-8 rounded-full ring-4 transition-colors",
        style.color,
        style.ring,
      )}
      title={style.label}
    />
  );
}
