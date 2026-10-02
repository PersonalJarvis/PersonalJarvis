import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Check, ChevronRight, Copy } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useT } from "@/i18n";
import { robustCopy } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import type { ToolBlock, TurnBlock, TurnStatus } from "./reduce";
import { narrateTurn, narrativeMarkdown, proseDuration, type NarrativeStep, type StepOutcome, type TraceNarrative } from "./traceNarrative";

/**
 * A finished turn's work as a written report (see traceNarrative.ts for the
 * wording rules): one overview paragraph, then every step as a numbered
 * sentence on a quiet thread — the model's reason above it in its own words,
 * what came of it below, the raw call one tap away. "Copy as text" puts the
 * same report on the clipboard as Markdown, so it can be kept or shared.
 */
/** The narrative of a turn's work, recomputed when the blocks or the language change. */
export function useTraceNarrative(blocks: TurnBlock[], status: TurnStatus, durationMs: number | null, model?: string): TraceNarrative {
  const t = useT();
  // The locale's own tag, read through the dictionary: plurals and lists
  // follow the language the sentences are written in.
  const lang = t("trace_report.locale");
  return useMemo(
    () => narrateTurn(blocks, { t, lang, status, durationMs, model }),
    [blocks, t, lang, status, durationMs, model],
  );
}

export function TraceReport({ narrative, renderDetails, className }: {
  narrative: TraceNarrative;
  /** The raw call (input, diff, output) for a step's "Details". */
  renderDetails?: (block: ToolBlock) => ReactNode;
  className?: string;
}) {
  const t = useT();
  let number = 0;
  return (
    <section data-testid="trace-report" aria-label={t("trace_report.title")} className={cn("min-w-0 pb-1 pt-0.5", className)}>
      {narrative.overview ? <p data-trace-overview className="text-sm leading-6 text-foreground-secondary [overflow-wrap:anywhere]">
        <Inline text={narrative.overview} />
      </p> : null}
      {narrative.steps.length ? <ol className="trace-report-steps mt-3">
        {narrative.steps.map((step) => {
          if (step.kind === "action") number += 1;
          return <Step key={step.id} step={step} number={step.kind === "action" ? number : null} renderDetails={renderDetails} />;
        })}
      </ol> : null}
      <CopyReport text={() => narrativeMarkdown(narrative, t)} />
    </section>
  );
}

const PROBLEM: ReadonlySet<StepOutcome> = new Set(["failed", "denied"]);

function Step({ step, number, renderDetails }: { step: NarrativeStep; number: number | null; renderDetails?: (block: ToolBlock) => ReactNode }) {
  const problem = PROBLEM.has(step.outcome);
  return (
    <li className="trace-report-step" data-outcome={step.outcome} data-kind={step.kind}>
      <span aria-hidden className="trace-report-marker">
        {number !== null ? <span className="trace-report-num">{number}</span> : <span className="trace-dot" />}
      </span>
      <div className="min-w-0 flex-1 space-y-1">
        {step.why.map((text, i) => <Why key={i} text={text} />)}
        {step.sentence ? <p className="text-sm leading-6 text-foreground [overflow-wrap:anywhere]">
          <Inline text={step.sentence} />
          {step.durationMs !== null && step.durationMs > 0
            ? <span className="ml-2 whitespace-nowrap text-xs tabular-nums text-muted-foreground">{proseDuration(step.durationMs)}</span> : null}
        </p> : null}
        {step.result ? <p data-trace-result className={cn("text-sm leading-6 [overflow-wrap:anywhere]", problem ? "text-destructive" : "text-muted-foreground")}>
          <Inline text={step.result} />
        </p> : null}
        {step.block && renderDetails ? <StepDetails>{renderDetails(step.block)}</StepDetails> : null}
      </div>
    </li>
  );
}

/** Markdown inside a sentence: inline code and emphasis, never HTML or blocks. */
function Inline({ text }: { text: string }) {
  return <ReactMarkdown
    allowedElements={["p", "code", "em", "strong"]}
    unwrapDisallowed
    components={{
      p: ({ children }) => <>{children}</>,
      code: ({ children }) => <code className="rounded bg-muted/70 px-1 py-0.5 font-mono text-[0.85em] text-foreground">{children}</code>,
    }}
  >{text}</ReactMarkdown>;
}

/** The model's own words before a step — read in full, folded when long. */
function Why({ text }: { text: string }) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !open) setOverflows(el.scrollHeight > el.clientHeight + 1);
  }, [text, open]);
  return <div data-trace-why>
    <div ref={ref} className={cn(
      "prose prose-sm max-w-none text-sm leading-6 text-foreground-secondary dark:prose-invert [overflow-wrap:anywhere]",
      "prose-p:my-1 prose-p:text-foreground-secondary prose-li:text-foreground-secondary prose-strong:text-foreground prose-pre:overflow-auto",
      "[&>:first-child]:mt-0 [&>:last-child]:mb-0",
      !open && "max-h-48 overflow-hidden",
      !open && overflows && "trace-report-fade",
    )}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
    {overflows || open ? <button type="button" onClick={() => setOpen(!open)}
      className="mt-0.5 rounded-sm text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      {t(open ? "trace_report.show_less" : "trace_report.show_more")}
    </button> : null}
  </div>;
}

function StepDetails({ children }: { children: ReactNode }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  return <div>
    <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
      className="group/details inline-flex items-center gap-1 rounded-sm text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      <ChevronRight aria-hidden className={cn("h-3 w-3 transition-transform", open && "rotate-90")} />
      {t("trace_report.details")}
    </button>
    {open ? <div className="min-w-0 pt-1">{children}</div> : null}
  </div>;
}

function CopyReport({ text }: { text: () => string }) {
  const t = useT();
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const copy = async () => {
    const ok = await robustCopy(text());
    setState(ok ? "copied" : "failed");
    window.setTimeout(() => setState("idle"), 2000);
  };
  const Icon = state === "copied" ? Check : Copy;
  return <button type="button" onClick={() => void copy()}
    className="mt-3 inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
    <Icon aria-hidden className="h-3.5 w-3.5" />
    <span aria-live="polite">{t(state === "copied" ? "trace_report.copied" : state === "failed" ? "trace_report.copy_failed" : "trace_report.copy")}</span>
  </button>;
}
