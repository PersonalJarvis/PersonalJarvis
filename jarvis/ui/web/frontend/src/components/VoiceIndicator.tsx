import { useEventStore, type VoiceState } from "@/store/events";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * Status fill per voice state. Exhaustive by construction: adding a member to
 * VoiceState without an entry here fails the TypeScript build instead of
 * rendering an undefined swatch.
 *
 * Only the three status hues appear, because a status is the one thing in the
 * product allowed to carry colour. What was here before — blue for idle, pink
 * for speaking, red for error — spent hue on states that are not faults and
 * left `listening`, the one state that means "this is running right now", in
 * plain grey. That inverts the whole ramp: the loudest mark on the screen sat
 * on a session doing nothing while a live microphone read as switched off.
 *
 * So: anything actually running is `--success`, the connecting handshake is
 * `--warning` (partial, not yet live), a fault is `--destructive`, and the two
 * states that are neither — idle and paused — stay neutral ink. The live
 * states are told apart by motion rather than by a second hue, and every
 * animation is behind `motion-safe`.
 */
const STATE_STYLE: Record<VoiceState, { fill: string; motion: string }> = {
  idle: { fill: "bg-faint-foreground", motion: "" },
  connecting: { fill: "bg-warning", motion: "motion-safe:animate-pulse" },
  listening: { fill: "bg-success", motion: "" },
  thinking: { fill: "bg-success", motion: "motion-safe:animate-pulse" },
  speaking: { fill: "bg-success", motion: "motion-safe:animate-pulse" },
  paused: { fill: "bg-muted-foreground", motion: "" },
  error: { fill: "bg-destructive", motion: "" },
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
        // A rim, not a bloom: the old translucent 4px halo was additive light
        // standing in for a fill, and it changed colour with the state as
        // well, which made every transition read as two separate changes.
        "h-8 w-8 rounded-full ring-2 ring-border-strong transition-colors",
        style.fill,
        style.motion,
      )}
      title={label}
    />
  );
}
