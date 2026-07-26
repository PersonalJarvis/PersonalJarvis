/**
 * Honest staged import-progress strip (design doc 04 "Import progress").
 *
 * Renders the per-stage backlog counts (captured → keyword-searchable →
 * embedded → distilled, plus the failed dead-letter) exactly as the store
 * reports them — the same "honest funnel" idiom as WikiCaptureFunnelStrip —
 * and the active/recent sync jobs with a cancel button per active job.
 * Polling cadence is owned by the parent (UltraWikiPanel speeds the status
 * poll up to a few seconds while a job is active).
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

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  ULTRAWIKI_ACTIVE_JOB_STATUSES,
  cancelUltraWikiJob,
  requeueUltraWikiFailed,
  type UltraWikiCounts,
  type UltraWikiJob,
  type UltraWikiPipeline,
} from "@/lib/ultrawikiApi";

const STAGES = [
  ["captured", "ultrawiki.progress.stage_captured"],
  ["keyword_indexed", "ultrawiki.progress.stage_keyword_indexed"],
  ["embedded", "ultrawiki.progress.stage_embedded"],
  ["distilled", "ultrawiki.progress.stage_distilled"],
  ["failed", "ultrawiki.progress.stage_failed"],
] as const;

export function ImportProgress({
  counts,
  pipeline,
  jobs,
  onChanged,
  onOpenSources,
}: {
  counts: Partial<UltraWikiCounts>;
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

  // What is still WAITING to be processed. 'distilled' is finished work and
  // 'failed' gave up, so neither belongs in a "still to do" number.
  const pendingItems =
    (counts.captured ?? 0) +
    (counts.keyword_indexed ?? 0) +
    (counts.embedded ?? 0);

  return (
    <section
      aria-label={t("ultrawiki.progress.title")}
      className="border-b border-border bg-card/20 px-4 py-2"
      data-testid="ultrawiki-import-progress"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
        <PipelineState
          pipeline={pipeline}
          pending={pendingItems}
          onOpenSources={onOpenSources}
        />
        <dl className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
          {STAGES.map(([key, labelKey]) => {
            const value = counts[key] ?? 0;
            const isFailed = key === "failed";
            return (
              <div
                className={cn(
                  "flex items-baseline gap-1",
                  isFailed && value > 0 && "text-destructive",
                )}
                data-testid={`ultrawiki-stage-${key}`}
                key={key}
              >
                <dt>{t(labelKey)}</dt>
                <dd
                  className={cn(
                    "font-medium",
                    isFailed && value > 0
                      ? "text-destructive"
                      : "text-foreground",
                  )}
                >
                  {value}
                </dd>
              </div>
            );
          })}
        </dl>
        {(counts.failed ?? 0) > 0 && (
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
            {t("ultrawiki.progress.retry_failed")}
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
              <span>
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
  pending,
  onOpenSources,
}: {
  pipeline: UltraWikiPipeline;
  pending: number;
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
          label: t("ultrawiki.progress.state_processing").replace(
            "{0}",
            String(pending),
          ),
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
