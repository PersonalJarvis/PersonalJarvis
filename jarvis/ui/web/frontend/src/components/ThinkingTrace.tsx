import { useEventStore } from "@/store/events";
import type { ThinkingTraceSnapshot } from "@/lib/thinkingSteps";
import { VoiceWorkTrace } from "@/components/agentchat/VoiceWorkTrace";
export { traceDuration as formatThinkingDuration } from "@/components/agentchat/WorkTrace";

export function ThinkingTrace() {
  const steps = useEventStore((s) => s.thinkingSteps);
  return <VoiceWorkTrace steps={steps} live />;
}

export function ThoughtTraceDisclosure({ trace }: { trace: ThinkingTraceSnapshot }) {
  return <VoiceWorkTrace steps={trace.steps} durationMs={trace.durationMs} />;
}
