import { lazy, Suspense } from "react";
import type { ThinkingStep } from "@/lib/thinkingSteps";

/**
 * The trace renderer is code-split out of the entry chunk.
 *
 * The voice stage is the front page, but a trace only exists once a turn has
 * run — and the renderer behind it (WorkTrace) carries the whole markdown
 * stack (react-markdown, micromark, mdast/hast, remark-gfm) plus the tool and
 * brand logo tables, over 300 KB the WebView would otherwise parse before its
 * first paint just to show an empty greeting. `MainView` warms this chunk in its first idle slot through
 * {@link loadTurnTrace}, so by the time a turn starts it is already resolved
 * and renders synchronously.
 */
export const loadTurnTrace = () =>
  import("@/components/agentchat/VoiceWorkTrace").then((m) => ({ default: m.VoiceWorkTrace }));

const VoiceWorkTrace = lazy(loadTurnTrace);

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
  // No placeholder while the chunk is in flight: the trace appears with its
  // turn anyway, and an empty slot is quieter than a row that swaps shape.
  return (
    <Suspense fallback={null}>
      <VoiceWorkTrace steps={steps} live={live} durationMs={durationMs} className={className} />
    </Suspense>
  );
}
