/**
 * The run-level pieces of the Artifacts section — what the Outputs section
 * used to show about a mission, now sitting next to the artifact it produced.
 *
 * - `RunStatusBadge`: the run's status word (running / success / error /
 *   cancelled / needs review), one colour language for rail and stage.
 * - `RunActions`: hold-to-abort while it runs, Continue / Restart when it
 *   ended early, the live-continuation chip, the GitHub link.
 * - `RunNotes`: why it ended (failure / cancellation / needs review), the
 *   summary, the error — only drawn when there is something to say.
 * - `RunFiles`: every deliverable the run left behind, in the document reader
 *   (`ArtifactViewer`), plumbing hidden and primary files first.
 *
 * All of it came over from `views/OutputsView.tsx` when that section folded
 * into Artifacts (2026-08-23); the i18n keys kept their `outputs_view.` prefix
 * because the strings did not change.
 */
import { useMemo } from "react";
import { FileText, Github, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { HoldToAbortButton } from "@/components/HoldToAbortButton";
import { RerunButton } from "@/components/RerunButton";
import {
  ArtifactViewer,
  deliverableDisplayPath,
} from "@/components/outputs/ArtifactViewer";
import {
  useArtifactsForOutput,
  useCancelMission,
  useOutputsCapabilities,
  type ArtifactSummary,
  type OutputStatus,
  type OutputSummary,
} from "@/hooks/useOutputs";

const STATUS_BADGE: Record<OutputStatus, string> = {
  success: "border-emerald-400/40 bg-emerald-400/10 text-emerald-400",
  error: "border-destructive/40 bg-destructive/10 text-destructive",
  running: "border-primary/40 bg-primary/10 text-primary",
  // Deliberate user abort — amber, not the destructive red of a failure.
  cancelled: "border-amber-400/40 bg-amber-400/10 text-amber-400",
  unknown: "border-border bg-secondary/40 text-muted-foreground",
};

const NEEDS_REVIEW_BADGE = "border-amber-400/40 bg-amber-400/10 text-amber-300";

/** Review ended without approval, but a genuine deliverable was retained. */
export function runNeedsReview(run: OutputSummary): boolean {
  return (run.status ?? "unknown") === "error" && !!run.needs_review;
}

/** Tiny ping dot inside the RUNNING badge — inherits the badge colour. */
export function PulseDot() {
  return (
    <span className="relative flex h-1.5 w-1.5" aria-hidden="true">
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-60" />
      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-current" />
    </span>
  );
}

export function RunStatusBadge({
  run,
  size = "sm",
}: {
  run: OutputSummary;
  size?: "sm" | "md";
}) {
  const t = useT();
  const statusKey = run.status ?? "unknown";
  const needsReview = runNeedsReview(run);
  const badgeClass = needsReview ? NEEDS_REVIEW_BADGE : STATUS_BADGE[statusKey];
  const label = needsReview ? t("outputs_view.needs_review") : statusKey;
  return (
    <span
      data-testid="run-status-badge"
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded border font-semibold uppercase tracking-wide",
        size === "sm" ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-[10px]",
        badgeClass,
      )}
    >
      {statusKey === "running" && <PulseDot />}
      {label}
    </span>
  );
}

/**
 * Shown on a terminal (cancelled / errored) run whose re-run continuation is
 * still running. Replaces the "Continue"/"Restart" button so the run stops
 * implying the work is idle and re-runnable — the truth is that it is alive in
 * a linked child mission. Clicking jumps the selection to that live child
 * (forensic 2026-06-28: a cancelled run and its running continuation looked
 * identical, so the user could not tell whether the mission was running).
 */
function ContinuationChip({ onJump }: { onJump: () => void }) {
  const t = useT();
  const label = t("outputs_view.continuation_running");
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      data-testid="continuation-chip"
      onClick={(e) => {
        e.stopPropagation();
        onJump();
      }}
      className={cn(
        "inline-flex shrink-0 select-none items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide transition-colors",
        "border-primary/40 bg-primary/10 text-primary hover:bg-primary/20",
      )}
    >
      <PulseDot />
      <span>{label}</span>
    </button>
  );
}

/**
 * What can be done about the run right now: abort it while it runs, continue
 * or restart it once it ended early, jump to its live continuation, open the
 * GitHub page it produced. Nothing for a run that simply finished.
 */
export function RunActions({
  run,
  onJumpToRun,
}: {
  run: OutputSummary;
  /** Select another run by slug — how the continuation chip jumps to the child. */
  onJumpToRun: (slug: string) => void;
}) {
  const t = useT();
  const cancel = useCancelMission();
  const statusKey = run.status ?? "unknown";
  const canAbort = statusKey === "running" && !!run.mission_id;
  // A cancelled/errored run whose re-run continuation is still running: show
  // a live "running" chip (jumps to the child) instead of a "Continue" button.
  const liveChildSlug =
    statusKey === "cancelled" || statusKey === "error"
      ? (run.active_child_slug ?? null)
      : null;

  return (
    <span className="flex shrink-0 items-center gap-1.5" data-testid="run-actions">
      {canAbort && (
        <HoldToAbortButton
          size="sm"
          pending={cancel.isPending}
          onConfirm={() => {
            if (run.mission_id) cancel.mutate(run.mission_id);
          }}
          label={
            cancel.isPending ? t("outputs_view.aborting") : t("outputs_view.abort_hold")
          }
        />
      )}
      {liveChildSlug ? (
        <ContinuationChip onJump={() => onJumpToRun(liveChildSlug)} />
      ) : (
        <>
          {statusKey === "cancelled" && run.mission_id && (
            <RerunButton missionId={run.mission_id} action="continue" size="sm" />
          )}
          {statusKey === "error" && run.mission_id && (
            <RerunButton missionId={run.mission_id} action="restart" size="sm" />
          )}
        </>
      )}
      {run.github_url && (
        <a
          href={run.github_url}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          title="GitHub"
          className="inline-flex items-center gap-1 rounded border border-border bg-secondary/40 px-1.5 py-0.5 text-[10px] text-muted-foreground hover:text-primary"
        >
          <Github className="h-3 w-3" aria-hidden />
          GitHub
        </a>
      )}
    </span>
  );
}

/* ------------------------------------------------------------------------- */

const URL_REGEX = /(https?:\/\/[^\s)]+[^\s.,;:!?)])/g;

/**
 * Linkifies URLs in the text. The regex is deliberately simple — proper
 * Markdown support would need a real parser, but the summary is plain text
 * with the occasional link.
 */
function LinkifiedText({ text }: { text: string }) {
  const parts = useMemo(() => {
    const out: Array<{ type: "text" | "url"; value: string }> = [];
    let last = 0;
    URL_REGEX.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = URL_REGEX.exec(text)) !== null) {
      if (match.index > last) {
        out.push({ type: "text", value: text.slice(last, match.index) });
      }
      out.push({ type: "url", value: match[0] });
      last = match.index + match[0].length;
    }
    if (last < text.length) {
      out.push({ type: "text", value: text.slice(last) });
    }
    return out;
  }, [text]);

  return (
    <>
      {parts.map((p, i) =>
        p.type === "url" ? (
          <a
            key={i}
            href={p.value}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary underline-offset-2 hover:underline"
          >
            {p.value}
          </a>
        ) : (
          <span key={i}>{p.value}</span>
        ),
      )}
    </>
  );
}

/** True when `RunNotes` would draw anything for this run. */
export function runHasNotes(run: OutputSummary): boolean {
  return !!(run.terminal_reason || runNeedsReview(run) || run.summary || run.error);
}

/**
 * Why the run ended the way it did, and what it said about itself. The
 * failure / cancellation / needs-review card first (the thing that needs a
 * decision), the summary next, a bare error last. Nothing at all for a run
 * with nothing to say — the caller skips the block.
 */
export function RunNotes({ run, className }: { run: OutputSummary; className?: string }) {
  const t = useT();
  const statusKey = run.status ?? "unknown";
  const needsReview = runNeedsReview(run);
  const isCancelled = statusKey === "cancelled";
  if (!runHasNotes(run)) return null;

  return (
    <div className={cn("flex flex-col gap-3", className)} data-testid="run-notes">
      {(run.terminal_reason || needsReview) && (
        <section
          data-testid={needsReview ? "output-needs-review" : "output-terminal-reason"}
          className={cn(
            "rounded-xl border p-3",
            needsReview || isCancelled
              ? "border-amber-400/30 bg-amber-400/5"
              : "border-destructive/30 bg-destructive/5",
          )}
        >
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            {needsReview
              ? t("outputs_view.needs_review")
              : isCancelled
                ? t("outputs_view.cancellation_reason")
                : t("outputs_view.failure_reason")}
          </div>
          {needsReview && (
            <p className="text-sm leading-relaxed text-foreground/90">
              {t("outputs_view.needs_review_hint")}
            </p>
          )}
          {!needsReview && !isCancelled && run.has_partial_output && (
            <p className="text-sm leading-relaxed text-foreground/90">
              {t("outputs_view.partial_output_hint")}
            </p>
          )}
          {run.terminal_reason && (
            <div className="mt-1 font-mono text-[11px] text-muted-foreground">
              {run.terminal_reason}
            </div>
          )}
        </section>
      )}

      {run.summary && (
        <section className="rounded-xl border border-border bg-card/40 p-3">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Summary
          </div>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
            <LinkifiedText text={run.summary} />
          </p>
        </section>
      )}

      {run.error && !run.terminal_reason && (
        <section className="rounded-xl border border-destructive/30 bg-destructive/5 p-3">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-destructive">
            {t("common.error")}
          </div>
          <pre className="whitespace-pre-wrap text-xs text-destructive/90">{run.error}</pre>
        </section>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------- */

// Pure plumbing the worker subprocess emits — never a user deliverable.
// Hiding it keeps the list to the actual files the worker created (under
// artifacts/files/) plus the captured diff, so a non-coder sees their result
// instead of stream logs (2026-05-29).
function isPlumbingArtifact(path: string): boolean {
  return (
    path.endsWith("stream.jsonl") ||
    path.endsWith("stderr.log") ||
    path.endsWith(".jarvis-mcp.json") ||
    path === "reflections.md"
  );
}

/** Put primary top-level files first, then group nested assets alphabetically. */
function compareDeliverablePaths(a: ArtifactSummary, b: ArtifactSummary): number {
  const left = deliverableDisplayPath(a.path);
  const right = deliverableDisplayPath(b.path);
  const depth = left.split("/").length - right.split("/").length;
  return (
    depth ||
    left.localeCompare(right, undefined, {
      numeric: true,
      sensitivity: "base",
    })
  );
}

/** The deliverables worth showing, primary files first. */
export function deliverableFiles(files: ArtifactSummary[]): ArtifactSummary[] {
  return files.filter((f) => !isPlumbingArtifact(f.path)).sort(compareDeliverablePaths);
}

/**
 * Every file the run left behind, in the document reader. The run's notes
 * (summary, why it ended) sit above the files: for a run that produced no
 * page — a research answer, a failed build — they ARE the result.
 */
export function RunFiles({ run }: { run: OutputSummary }) {
  const t = useT();
  const q = useArtifactsForOutput(run.slug);
  const caps = useOutputsCapabilities();
  const nativeActions = caps.data?.native_file_actions ?? false;
  const files = useMemo(() => deliverableFiles(q.data?.files ?? []), [q.data]);

  return (
    <div className="flex h-full flex-col gap-4 overflow-auto p-4" data-testid="run-files">
      <RunNotes run={run} />
      <section className="flex min-h-0 flex-col">
        <div className="mb-2 flex items-center gap-2">
          <FileText className="h-4 w-4 text-primary" aria-hidden />
          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            {t("outputs_view.results")}
          </span>
          {!q.isLoading && (
            <span className="text-[11px] text-muted-foreground">
              {files.length}{" "}
              {files.length === 1 ? t("outputs_view.file") : t("outputs_view.files")}
            </span>
          )}
        </div>
        {q.isLoading ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            {t("outputs_view.loading_artifacts")}
          </div>
        ) : q.isError ? (
          <div className="text-xs text-destructive">
            {t("outputs_view.artifacts_load_error")}: {String(q.error)}
          </div>
        ) : files.length === 0 ? (
          <div className="text-xs text-muted-foreground">{t("outputs_view.no_files")}</div>
        ) : (
          <ArtifactViewer
            key={run.slug}
            slug={run.slug}
            files={files}
            nativeActions={nativeActions}
          />
        )}
      </section>
    </div>
  );
}
