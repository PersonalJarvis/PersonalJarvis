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
import { Loader2, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  ULTRAWIKI_ACTIVE_JOB_STATUSES,
  cancelUltraWikiJob,
  type UltraWikiCounts,
  type UltraWikiJob,
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
  pipelineRunning,
  jobs,
  onChanged,
}: {
  counts: Partial<UltraWikiCounts>;
  pipelineRunning: boolean;
  jobs: UltraWikiJob[];
  onChanged: () => void;
}): JSX.Element {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [cancelling, setCancelling] = useState<string | null>(null);

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

  return (
    <section
      aria-label={t("ultrawiki.progress.title")}
      className="border-b border-border bg-card/20 px-4 py-2"
      data-testid="ultrawiki-import-progress"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
        <span className="flex items-center gap-1.5 font-medium text-foreground">
          {pipelineRunning && (
            <Loader2
              className="h-3 w-3 animate-spin text-primary"
              aria-hidden
              data-testid="ultrawiki-pipeline-running"
            />
          )}
          {t(
            pipelineRunning
              ? "ultrawiki.progress.pipeline_running"
              : "ultrawiki.progress.pipeline_idle",
          )}
        </span>
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
      </div>

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
