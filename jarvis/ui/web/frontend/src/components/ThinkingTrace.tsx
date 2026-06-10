/**
 * ThinkingTrace — the live reasoning card shown while the assistant works.
 *
 * Replaces the three bouncing dots with a precision-instrument look that
 * matches the app's dark/gold language: a pulsing core, a shimmering
 * "Thinking" wordmark, a live elapsed timer and a vertical step rail where
 * real backend events (tool calls, computer-use phases, worker dispatches)
 * appear as animated rows.
 *
 * ThoughtTraceDisclosure is the after-the-fact companion: once the reply
 * lands, the finished trace renders as a collapsed "Thought for 12.4s ·
 * 5 steps" row above the answer and can be expanded to replay the steps.
 */
import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  Bot,
  Brain,
  Check,
  ChevronRight,
  Info,
  MonitorDot,
  Wrench,
  X,
} from "lucide-react";
import { useEventStore } from "@/store/events";
import type {
  ThinkingStep,
  ThinkingStepKind,
  ThinkingTraceSnapshot,
} from "@/lib/thinkingSteps";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/** Rows visible in the live card — older ones collapse into a "+N" row. */
const VISIBLE_STEPS = 5;

const KIND_ICON: Record<ThinkingStepKind, typeof Wrench> = {
  brain: Brain,
  tool: Wrench,
  computer: MonitorDot,
  worker: Bot,
  note: Info,
};

export function formatThinkingDuration(ms: number): string {
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

/** Live elapsed readout. 100ms tick keeps the tenths digit feeling alive. */
function useElapsedMs(startedTs: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedTs === null) return;
    const id = window.setInterval(() => setNow(Date.now()), 100);
    return () => window.clearInterval(id);
  }, [startedTs]);
  return startedTs === null ? 0 : Math.max(0, now - startedTs);
}

function StatusNode({ status }: { status: ThinkingStep["status"] }) {
  if (status === "active") {
    return (
      <span
        aria-hidden
        className="relative z-10 h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-primary/25 border-t-primary bg-card"
      />
    );
  }
  if (status === "error") {
    return (
      <span
        aria-hidden
        className="relative z-10 flex h-3 w-3 shrink-0 items-center justify-center rounded-full border border-destructive/50 bg-destructive/10"
      >
        <X className="h-2 w-2 text-destructive" strokeWidth={3} />
      </span>
    );
  }
  return (
    <span
      aria-hidden
      className="relative z-10 flex h-3 w-3 shrink-0 items-center justify-center rounded-full border border-primary/50 bg-primary/15"
    >
      <Check className="h-2 w-2 text-primary" strokeWidth={3} />
    </span>
  );
}

function StepRow({ step, live }: { step: ThinkingStep; live: boolean }) {
  const t = useT();
  const reduced = useReducedMotion();
  const KindIcon = KIND_ICON[step.kind];
  const active = step.status === "active";

  return (
    <motion.li
      layout={!reduced}
      initial={live && !reduced ? { opacity: 0, y: 6 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
      className="relative flex min-w-0 items-center gap-2"
    >
      <StatusNode status={step.status} />
      <KindIcon
        aria-hidden
        className={cn(
          "h-3 w-3 shrink-0",
          active ? "text-primary/80" : "text-muted-foreground/50",
        )}
      />
      <span
        className={cn(
          "shrink-0 text-xs",
          active ? "thinking-shimmer font-medium" : "text-foreground/80",
          step.status === "error" && "text-destructive/90",
        )}
      >
        {t(step.labelKey)}
      </span>
      {step.detail && (
        <span className="min-w-0 truncate font-mono text-[10px] text-muted-foreground/80">
          {step.detail}
        </span>
      )}
      {step.durationMs !== undefined && step.durationMs > 0 && (
        <span className="ml-auto shrink-0 pl-2 font-mono text-[10px] tabular-nums text-muted-foreground/60">
          {formatThinkingDuration(step.durationMs)}
        </span>
      )}
    </motion.li>
  );
}

function StepRail({
  steps,
  live,
  hiddenCount,
}: {
  steps: ThinkingStep[];
  live: boolean;
  hiddenCount: number;
}) {
  const t = useT();
  return (
    <ol className="relative space-y-2">
      {/* Vertical rail behind the status nodes — fades out downward. */}
      <span
        aria-hidden
        className="absolute bottom-2 left-[5.5px] top-2 w-px bg-gradient-to-b from-primary/30 via-border to-transparent"
      />
      {hiddenCount > 0 && (
        <li className="relative flex items-center gap-2 pl-5 font-mono text-[10px] text-muted-foreground/50">
          +{hiddenCount} {t("thinking.earlier")}
        </li>
      )}
      {steps.map((s) => (
        <StepRow key={s.id} step={s} live={live} />
      ))}
    </ol>
  );
}

/** Pulsing gold core in the card header — two expanding rings + glow dot. */
function CoreOrb() {
  return (
    <span className="relative flex h-2.5 w-2.5 shrink-0" aria-hidden>
      <span className="thinking-ring absolute inline-flex h-full w-full rounded-full bg-primary/50" />
      <span className="thinking-ring absolute inline-flex h-full w-full rounded-full bg-primary/50 [animation-delay:0.9s]" />
      <span className="thinking-core relative inline-flex h-2.5 w-2.5 rounded-full bg-primary" />
    </span>
  );
}

/** The live card rendered in the transcript while `chatThinking` is true. */
export function ThinkingTrace() {
  const t = useT();
  const steps = useEventStore((s) => s.thinkingSteps);
  const startedTs = useEventStore((s) => s.thinkingStartedTs);
  const elapsed = useElapsedMs(startedTs);

  const visible = steps.slice(-VISIBLE_STEPS);
  const hidden = steps.length - visible.length;

  return (
    <div
      className="flex justify-start"
      role="status"
      aria-live="polite"
      aria-label={t("chats_view.thinking_aria")}
    >
      <div className="relative w-full max-w-[80%] overflow-hidden rounded-2xl rounded-bl-sm border border-primary/20 bg-card px-4 py-3 sm:max-w-[440px]">
        {/* Atmospheric breathing glow — gives the card a quiet "alive" depth. */}
        <span
          aria-hidden
          className="thinking-breathe pointer-events-none absolute -left-14 -top-14 h-36 w-36 rounded-full bg-primary/10 blur-3xl"
        />

        <div className="relative flex items-center gap-2">
          <CoreOrb />
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-primary">
            Jarvis
          </span>
          <span className="thinking-shimmer text-xs font-medium">
            {t("thinking.label")}
          </span>
          <span className="ml-auto font-mono text-[10px] tabular-nums text-primary/60">
            {formatThinkingDuration(elapsed)}
          </span>
        </div>

        {visible.length > 0 && (
          <div className="relative mt-3">
            <StepRail steps={visible} live hiddenCount={hidden} />
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Collapsed "Thought for Xs · N steps" disclosure above an assistant reply.
 * Expands to the finished step list — the conserved reasoning trace.
 */
export function ThoughtTraceDisclosure({
  trace,
}: {
  trace: ThinkingTraceSnapshot;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);

  return (
    <div className="mb-1.5 mt-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="group -ml-1 flex items-center gap-1.5 rounded-md px-1 py-0.5 text-[11px] text-muted-foreground transition-colors hover:text-primary"
      >
        <ChevronRight
          aria-hidden
          className={cn(
            "h-3 w-3 transition-transform duration-200",
            open && "rotate-90",
          )}
        />
        <span className="font-medium">
          {t("thinking.thought_for")} {formatThinkingDuration(trace.durationMs)}
        </span>
        <span className="text-muted-foreground/50">
          · {trace.steps.length} {t("thinking.steps")}
        </span>
      </button>
      {open && (
        <div className="mt-2 border-b border-border/60 pb-2">
          <StepRail steps={trace.steps} live={false} hiddenCount={0} />
        </div>
      )}
    </div>
  );
}
