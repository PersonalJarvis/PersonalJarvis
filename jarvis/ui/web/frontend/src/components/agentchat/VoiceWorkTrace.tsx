import { useMemo } from "react";
import { useT } from "@/i18n";
import type { ThinkingStep } from "@/lib/thinkingSteps";
import type { ToolBlock, TurnBlock } from "./reduce";
import { WorkTrace } from "./WorkTrace";

function toolBlock(step: ThinkingStep, name: string, input: unknown): ToolBlock {
  return {
    kind: "tool", callId: step.id, name, input,
    output: step.error ?? step.result ?? (step.status === "active" ? null : ""),
    isError: step.status === "error", durationMs: step.durationMs ?? null,
    startedMs: step.startedTs,
    // A refused call is told as refused, never as a call that ran and failed.
    approval: step.denied ? { approvalId: "", summary: step.error ?? "", decision: "deny" } : null,
  };
}

/**
 * Jarvis's own reasoning steps (voice, the history) as trace blocks.
 *
 * The brain call is the turn itself, not a step: it names the model in the
 * report and draws no row (it used to become an empty "Thought" row on
 * every turn). Computer-use and worker steps become the tool families that
 * describe them, so the report says "Controlled the screen" and "Handed a
 * task to a worker" instead of quoting a status label.
 */
export function stepsToBlocks(steps: ThinkingStep[], live: boolean, label: (key: string) => string): TurnBlock[] {
  const blocks: TurnBlock[] = [];
  for (const step of steps) {
    if (step.kind === "brain") continue;
    if (step.kind === "tool") {
      blocks.push(toolBlock(step, step.detail || label(step.labelKey), step.args));
    } else if (step.kind === "note" && step.status === "error") {
      // A note that failed is a step that failed, counted and told as one.
      blocks.push(toolBlock(step, label(step.labelKey), step.detail ? { text: step.detail } : {}));
    } else if (step.kind === "thought" || step.kind === "note") {
      blocks.push({
        kind: "reasoning", id: step.id, text: step.detail ?? "",
        durationMs: step.durationMs ?? null, live: live && step.status === "active", startedMs: step.startedTs,
      });
    } else if (step.kind === "computer") {
      blocks.push(toolBlock(step, "computer_use", step.detail ? { target: step.detail } : {}));
    } else {
      blocks.push(toolBlock(step, "spawn_worker", step.detail ? { task: step.detail } : {}));
    }
  }
  return blocks;
}

/** Adapt existing voice receipts without changing their persisted contract. */
export function VoiceWorkTrace({ steps, live = false, durationMs, className }: {
  steps: ThinkingStep[]; live?: boolean; durationMs?: number; className?: string;
}) {
  const t = useT();
  const blocks = useMemo(() => stepsToBlocks(steps, live, t), [steps, live, t]);
  // A step that failed is told in the report; the turn itself still ended —
  // calling it "Failed" because one tool did would misreport a turn that
  // went on to answer.
  return <WorkTrace blocks={blocks} status={live ? "running" : "done"}
    startedMs={steps[0]?.startedTs ?? Date.now()} durationMs={durationMs ?? null} className={className} companion />;
}
