/**
 * PlanStepList — Stub nach Filesystem-Reset 2026-04-25.
 *
 * Voller Component mit Status-Icons + expandable Errors folgt; aktuell
 * eine flache Liste die alle Step-Felder als kompakte Karten zeigt.
 */
import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PlanStep } from "@/hooks/useOutputs";

const STATUS_ICON: Record<string, JSX.Element> = {
  done: <CheckCircle2 className="h-4 w-4 text-primary" />,
  failed: <XCircle className="h-4 w-4 text-destructive" />,
  running: <Loader2 className="h-4 w-4 animate-spin text-primary" />,
  skipped: <Circle className="h-4 w-4 text-muted-foreground/40" />,
  pending: <Circle className="h-4 w-4 text-muted-foreground/40" />,
};

export function PlanStepList({ steps }: { steps: PlanStep[] }) {
  if (!steps || steps.length === 0) {
    return (
      <div className="text-xs text-muted-foreground">
        Kein strukturierter Plan — Single-Shot-Run.
      </div>
    );
  }
  return (
    <ol className="space-y-1.5">
      {steps.map((s, idx) => (
        <li
          key={s.step_id}
          className={cn(
            "rounded-md border px-3 py-2 text-xs",
            s.status === "failed"
              ? "border-destructive/30 bg-destructive/5"
              : s.status === "done"
                ? "border-primary/30 bg-primary/5"
                : "border-border bg-card/40",
          )}
        >
          <div className="flex items-center gap-2">
            {STATUS_ICON[s.status] ?? STATUS_ICON.pending}
            <span className="text-muted-foreground/50">{idx + 1}.</span>
            <span className="font-medium">{s.name || s.step_id}</span>
            {typeof s.duration_s === "number" && (
              <span className="ml-auto text-[10px] text-muted-foreground">
                {s.duration_s.toFixed(1)}s
              </span>
            )}
          </div>
          {s.error && (
            <div className="mt-1 break-words font-mono text-[10px] text-destructive">
              {s.error}
            </div>
          )}
          {s.output && s.status === "done" && (
            <details className="mt-1">
              <summary className="cursor-pointer text-[10px] text-muted-foreground hover:text-foreground">
                Output anzeigen
              </summary>
              <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-background/40 p-2 font-mono text-[10px]">
                {s.output}
              </pre>
            </details>
          )}
        </li>
      ))}
    </ol>
  );
}
