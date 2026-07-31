/**
 * "What happened" — the surface the section never had.
 *
 * Everything else here answers *how many* or *what is inside*. The question
 * that went unanswered, in the maintainer's own words while looking at the
 * live app, was "I have no idea what happened." Imports ran, items appeared,
 * numbers moved, and no screen recorded any of it.
 *
 * Two sources feed this list, and they are not equally complete — which the
 * footnote says out loud rather than letting the list imply a full log:
 *
 * - the job registry (`status.jobs`) holds this session's runs, including the
 *   one currently in flight, and is lost on restart;
 * - each source's `last_outcome` is persisted, so a source whose last import
 *   predates the restart still has a line here — but only its most recent one.
 *
 * A source that appears in both is shown once, from the live job, which is
 * the fresher of the two.
 */
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  MinusCircle,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { formatRelativeTime } from "@/components/ultrawiki/relativeTime";
import { Eyebrow, formatCount } from "@/components/ultrawiki/overview/primitives";
import {
  ULTRAWIKI_ACTIVE_JOB_STATUSES,
  type UltraWikiSource,
  type UltraWikiStatus,
} from "@/lib/ultrawikiApi";

const MAX_ROWS = 8;

export interface ActivityEntry {
  key: string;
  sourceId: string;
  label: string;
  status: string;
  /** ISO string or epoch seconds — whatever the origin recorded. */
  at: string | number | null;
  New: number;
  changed: number;
  unchanged: number;
  error: string;
  /** True for the persisted per-source outcome, which is not a full log. */
  fromMemory: boolean;
}

/**
 * Merge the live job registry with the persisted per-source outcomes.
 *
 * Exported for its own test: the de-duplication is the part that decides
 * whether one import shows up as one event or two.
 */
export function activityEntriesOf(
  jobs: UltraWikiStatus["jobs"],
  sources: UltraWikiSource[],
): ActivityEntry[] {
  const labels = new Map(sources.map((s) => [s.id, s.label]));
  const seen = new Set<string>();
  const entries: ActivityEntry[] = [];

  for (const job of jobs) {
    seen.add(job.source_id);
    entries.push({
      key: job.job_id,
      sourceId: job.source_id,
      label: labels.get(job.source_id) ?? job.source_id,
      status: job.status,
      at: job.ended_at ?? job.started_at,
      New: job.new,
      changed: job.changed,
      unchanged: job.unchanged,
      error: job.error,
      fromMemory: false,
    });
  }

  for (const source of sources) {
    const outcome = source.last_outcome;
    if (!outcome || seen.has(source.id)) continue;
    entries.push({
      key: `outcome:${source.id}`,
      sourceId: source.id,
      label: source.label,
      status: outcome.status || "done",
      at: outcome.finished_at,
      New: outcome.new,
      changed: outcome.changed,
      unchanged: outcome.unchanged,
      error: source.last_error ?? "",
      fromMemory: true,
    });
  }

  return entries.sort((a, b) => timeOf(b.at) - timeOf(a.at));
}

function timeOf(at: string | number | null): number {
  if (at === null || at === undefined || at === "") return 0;
  if (typeof at === "number") return at * 1000;
  const parsed = Date.parse(at);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function ActivityFeed({
  jobs,
  sources,
}: {
  jobs: UltraWikiStatus["jobs"];
  sources: UltraWikiSource[];
}): JSX.Element {
  const t = useT();
  const all = activityEntriesOf(jobs, sources);
  const entries = all.slice(0, MAX_ROWS);
  const anyFromMemory = entries.some((e) => e.fromMemory);

  return (
    <section data-testid="ultrawiki-activity">
      <Eyebrow>{t("ultrawiki.overview.eyebrow_activity")}</Eyebrow>
      {entries.length === 0 ? (
        <p
          className="rounded-xl border border-dashed border-border bg-card/30 p-4 text-[13px] text-muted-foreground"
          data-testid="ultrawiki-activity-empty"
        >
          {t("ultrawiki.overview.activity_empty")}
        </p>
      ) : (
        <ol className="divide-y divide-border/60 overflow-hidden rounded-xl border border-border bg-card/40">
          {entries.map((entry) => (
            <ActivityRow key={entry.key} entry={entry} />
          ))}
        </ol>
      )}
      {anyFromMemory && (
        <p
          className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground"
          data-testid="ultrawiki-activity-note"
        >
          {t("ultrawiki.overview.activity_note")}
        </p>
      )}
    </section>
  );
}

function ActivityRow({ entry }: { entry: ActivityEntry }): JSX.Element {
  const t = useT();
  const running = ULTRAWIKI_ACTIVE_JOB_STATUSES.includes(entry.status);
  const Icon = running
    ? Loader2
    : entry.status === "failed"
      ? AlertTriangle
      : entry.status === "cancelled"
        ? MinusCircle
        : CheckCircle2;
  const tone = running
    ? "text-primary"
    : entry.status === "failed"
      ? "text-destructive"
      : entry.status === "cancelled"
        ? "text-muted-foreground"
        : "text-[#5bd4a4]";

  // "+12 new · 3 changed · 4 511 unchanged" — the three numbers that say what
  // an import actually did. A run that changed nothing is worth seeing too,
  // so the counts are printed even when they are all zero.
  const summary = [
    t("ultrawiki.overview.act_new").replace("{0}", formatCount(entry.New)),
    t("ultrawiki.overview.act_changed").replace("{0}", formatCount(entry.changed)),
    t("ultrawiki.overview.act_unchanged").replace(
      "{0}",
      formatCount(entry.unchanged),
    ),
  ].join(" · ");

  return (
    <li
      className="flex items-center gap-3 px-3 py-2"
      data-testid={`ultrawiki-activity-row-${entry.sourceId}`}
      data-status={entry.status}
    >
      <Icon
        className={cn("h-3.5 w-3.5 shrink-0", tone, running && "animate-spin")}
        aria-hidden
      />
      <span className="w-40 shrink-0 truncate text-[13px] text-foreground">
        {entry.label}
      </span>
      <span className="min-w-0 flex-1 truncate font-mono text-[11px] tabular-nums text-muted-foreground">
        {entry.status === "failed" && entry.error ? entry.error : summary}
      </span>
      <span className="shrink-0 text-[11px] text-muted-foreground">
        {running
          ? t("ultrawiki.overview.act_running")
          : formatRelativeTime(entry.at, t)}
      </span>
    </li>
  );
}
