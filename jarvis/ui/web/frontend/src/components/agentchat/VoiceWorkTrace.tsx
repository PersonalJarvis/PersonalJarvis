import { useMemo } from "react";
import { useT } from "@/i18n";
import type { ThinkingStep } from "@/lib/thinkingSteps";
import type { TurnBlock } from "./reduce";
import { WorkTrace } from "./WorkTrace";

/** Adapt existing voice receipts without changing their persisted contract. */
export function VoiceWorkTrace({ steps, live = false, durationMs, className }: {
  steps: ThinkingStep[]; live?: boolean; durationMs?: number; className?: string;
}) {
  const t = useT();
  const blocks = useMemo(() => steps.map((step): TurnBlock => {
    if (step.kind === "tool") return {
      kind: "tool", callId: step.id, name: step.detail || t(step.labelKey), input: step.args,
      output: step.error ?? step.result ?? (step.status === "active" ? null : ""),
      isError: step.status === "error", durationMs: step.durationMs ?? null,
      startedMs: step.startedTs, approval: null,
    };
    if (step.kind === "thought" || step.kind === "brain") return {
      kind: "reasoning", id: step.id, text: step.kind === "thought" ? step.detail ?? "" : "",
      durationMs: step.durationMs ?? null, live: live && step.status === "active", startedMs: step.startedTs,
    };
    // Non-tool actions keep their real error and status, never a fabricated success.
    return { kind: "tool", callId: step.id, name: t(step.labelKey), input: step.detail,
      output: step.error ?? step.result ?? (step.status === "active" ? null : ""),
      isError: step.status === "error", durationMs: step.durationMs ?? null,
      startedMs: step.startedTs, approval: null };
  }), [steps, live, t]);
  return <WorkTrace blocks={blocks} status={live ? "running" : steps.some(step => step.status === "error") ? "error" : "done"}
    startedMs={steps[0]?.startedTs ?? Date.now()} durationMs={durationMs ?? null} className={className} />;
}
