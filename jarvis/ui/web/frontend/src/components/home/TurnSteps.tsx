import type { ThinkingStep } from "@/lib/thinkingSteps";
import { VoiceWorkTrace } from "@/components/agentchat/VoiceWorkTrace";

export interface TurnStepsProps {
  steps: ThinkingStep[];
  /** The turn is still running — the last active step shows a live spinner. */
  live?: boolean;
  /** Total thinking time (finished: of the turn; live: elapsed so far), for the header. */
  durationMs?: number;
  /** "provider · model" that answered, shown in the unfolded header of a finished turn. */
  model?: string;
  /** Tighter rows (the voice lane). */
  compact?: boolean;
  /** Finished traces start folded; live ones start open. */
  defaultOpen?: boolean;
  className?: string;
}

export function formatThoughtDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  if (total < 60) return `${Math.max(total, ms > 0 ? 1 : 0)}s`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}


export function traceWorthShowing(
  steps: ThinkingStep[],
  durationMs: number | undefined,
  live: boolean,
): boolean {
  if (live) return true;
  if (steps.length === 0) return false;
  const substantial = steps.some((s) => s.status === "error" || (s.kind !== "brain" && s.kind !== "note"));
  return substantial || (durationMs ?? 0) >= 1000;
}

export function TurnSteps({ steps, live = false, durationMs, className }: TurnStepsProps) {
  if (!traceWorthShowing(steps, durationMs, live)) return null;
  return <VoiceWorkTrace steps={steps} live={live} durationMs={durationMs} className={className} />;
}
