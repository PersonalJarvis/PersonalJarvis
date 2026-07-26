/**
 * The thin status ribbon above the tabs — "is anything happening right now?".
 *
 * It used to print the five raw pipeline buckets: "Captured 0 ·
 * Keyword-searchable 0 · Embedded 3237 · Distilled 1475 · Failed 0". Because
 * an item sits in exactly ONE bucket, those numbers described the state
 * machine rather than the corpus: a store where all 4 712 items were
 * keyword-searchable reported "Keyword-searchable 0", and the figures ran
 * backwards as work progressed. Worse, the strip added its own backlog while
 * the checklist below added a different one, and the two contradicted each
 * other on screen.
 *
 * Now it quotes the shared progress model (`jarvis/ultrawiki/progress.py`) and
 * says the same three things in words: how much is stored, how much can be
 * found, and how much is still queued. The Overview tab draws the full
 * picture, so the ribbon hides there rather than repeating it.
 *
 * The live controls stay: cancel a running job, retry the dead-lettered items.
 */
import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  PauseCircle,
  RotateCcw,
  XCircle,
} from "lucide-react";

import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { formatCount } from "@/components/ultrawiki/overview/primitives";
import {
  ULTRAWIKI_ACTIVE_JOB_STATUSES,
  cancelUltraWikiJob,
  requeueUltraWikiFailed,
  type UltraWikiJob,
  type UltraWikiPipeline,
  type UltraWikiProgress,
} from "@/lib/ultrawikiApi";

export function ImportProgress({
  progress,
  pipeline,
  jobs,
  onChanged,
  onOpenSources,
}: {
  /** `null` while the backend has not answered — the ribbon then shows only
   *  the pipeline state, never invented zeros. */
  progress: UltraWikiProgress | null;
  pipeline: UltraWikiPipeline;
  jobs: UltraWikiJob[];
  onChanged: () => void;
  onOpenSources?: () => void;
}): JSX.Element {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);

  async function handleRetryFailed() {
    setRetrying(true);
    try {
      const result = await requeueUltraWikiFailed();
      pushToast(
        "success",
        t("ultrawiki.progress.retry_failed_done").replace(
          "{0}",
          String(result.requeued),
        ),
      );
      onChanged();
    } catch (e) {
      pushToast(
        "error",
        t("ultrawiki.progress.retry_failed_error").replace(
          "{0}",
          (e as Error).message,
        ),
      );
    } finally {
      setRetrying(false);
    }
  }

  async function handleCancel(jobId: string) {
    setCancelling(jobId);
    try {
      await cancelUltraWikiJob(jobId);
      onChanged();
    } catch (e) {
      pushToast(
        "error",
        t("ultrawiki.progress.cancel_failed").replace(
          "{0}",
          (e as Error).message,
        ),
      );
    } finally {
      setCancelling(null);
    }
  }

  const activeJobs = jobs.filter((job) =>
    ULTRAWIKI_ACTIVE_JOB_STATUSES.includes(job.status),
  );
  const failed = progress?.failed ?? 0;

  return (
    <section
      aria-label={t("ultrawiki.progress.title")}
      className="border-b border-border bg-card/20 px-4 py-2"
      data-testid="ultrawiki-import-progress"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
        <PipelineState pipeline={pipeline} onOpenSources={onOpenSources} />
        {progress && progress.total > 0 && (
          <p
            className="text-muted-foreground"
            data-testid="ultrawiki-progress-summary"
          >
            <span className="font-mono tabular-nums text-foreground">
              {formatCount(progress.total)}
            </span>{" "}
            {t("ultrawiki.progress.stored")} ·{" "}
            <span className="font-mono tabular-nums text-foreground">
              {formatCount(progress.searchable)}
            </span>{" "}
            {t("ultrawiki.progress.searchable")}
            {progress.waiting > 0 && (
              <>
                {" · "}
                <span className="font-mono tabular-nums text-foreground">
                  {formatCount(progress.waiting)}
                </span>{" "}
                {t(`ultrawiki.overview.step_${progress.next_step ?? "processing"}`)}
              </>
            )}
          </p>
        )}
        {failed > 0 && (
          <button
            type="button"
            onClick={() => void handleRetryFailed()}
            disabled={retrying}
            data-testid="ultrawiki-retry-failed"
            className="inline-flex items-center gap-1 rounded-md border border-destructive/40 px-1.5 py-0.5 text-destructive hover:bg-destructive/10 disabled:opacity-50"
          >
            {retrying ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            ) : (
              <RotateCcw className="h-3 w-3" aria-hidden />
            )}
            {t("ultrawiki.progress.retry_failed").replace(
              "{0}",
              formatCount(failed),
            )}
          </button>
        )}
      </div>

      {pipeline.reason && (
        <p
          className="mt-1 text-[11px] leading-relaxed text-muted-foreground"
          data-testid="ultrawiki-pipeline-reason"
        >
          {pipeline.reason}
        </p>
      )}

      {activeJobs.length > 0 && (
        <ul className="mt-1.5 space-y-1" data-testid="ultrawiki-active-jobs">
          {activeJobs.map((job) => (
            <li
              key={job.job_id}
              className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground"
              data-testid={`ultrawiki-job-${job.job_id}`}
            >
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
              <span className="font-medium text-foreground">
                {job.source_id}
              </span>
              <span>{job.mode}</span>
              <span>·</span>
              <span>{job.status}</span>
              <span>·</span>
              <span className="font-mono tabular-nums">
                +{job.new} / ~{job.changed} / ={job.unchanged}
              </span>
              <button
                type="button"
                onClick={() => void handleCancel(job.job_id)}
                disabled={cancelling === job.job_id}
                data-testid={`ultrawiki-job-cancel-${job.job_id}`}
                className="inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-foreground hover:bg-muted disabled:opacity-50"
              >
                <XCircle className="h-3 w-3" aria-hidden />
                {t("ultrawiki.progress.cancel")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The honest headline of the strip.
 *
 * It used to read "Pipeline running" whenever the worker LOOP was alive —
 * including on a fresh activation with zero approved sources, where it read as
 * "something is already pulling my data" although nothing had been connected.
 * The four states come from the backend (one source of truth,
 * `PIPELINE_STATES`), each with its own honest wording; "waiting for sources"
 * additionally offers the way out, a link straight to the Sources tab.
 */
function PipelineState({
  pipeline,
  onOpenSources,
}: {
  pipeline: UltraWikiPipeline;
  onOpenSources?: () => void;
}): JSX.Element {
  const t = useT();
  const state = pipeline.state ?? (pipeline.running ? "processing" : "idle");

  if (state === "waiting_for_sources") {
    return (
      <span
        className="flex flex-wrap items-center gap-1.5 font-medium text-foreground"
        data-testid="ultrawiki-pipeline-state"
        data-state={state}
      >
        <AlertTriangle className="h-3 w-3 shrink-0 text-[#ffb84d]" aria-hidden />
        {t("ultrawiki.progress.state_waiting_for_sources")}
        {onOpenSources && (
          <button
            type="button"
            onClick={onOpenSources}
            className="underline underline-offset-2 hover:text-primary"
            data-testid="ultrawiki-open-sources-link"
          >
            {t("ultrawiki.progress.open_sources")}
          </button>
        )}
      </span>
    );
  }

  const { icon, label } =
    state === "processing"
      ? {
          icon: (
            <Loader2
              className="h-3 w-3 animate-spin text-primary"
              aria-hidden
              data-testid="ultrawiki-pipeline-running"
            />
          ),
          // No count here: the summary beside it already says how many are
          // left and what for. The strip used to print the same number three
          // times in one line — state, summary, and the backend's reason.
          label: t("ultrawiki.progress.state_processing"),
        }
      : state === "paused"
        ? {
            icon: (
              <PauseCircle className="h-3 w-3 shrink-0 text-[#ffb84d]" aria-hidden />
            ),
            label: t("ultrawiki.progress.state_paused"),
          }
        : {
            icon: (
              <CheckCircle2 className="h-3 w-3 shrink-0 text-[#5bd4a4]" aria-hidden />
            ),
            label: t("ultrawiki.progress.state_idle"),
          };

  return (
    <span
      className="flex items-center gap-1.5 font-medium text-foreground"
      data-testid="ultrawiki-pipeline-state"
      data-state={state}
    >
      {icon}
      {label}
    </span>
  );
}
