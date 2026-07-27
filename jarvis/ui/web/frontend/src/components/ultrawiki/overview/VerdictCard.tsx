/**
 * "Can I use it?" — the sentence the whole section exists to say.
 *
 * The screen it replaces opened with seven checklist rows of equal weight, six
 * of which said "fine". The one that mattered was wrong, and nothing about the
 * layout helped anyone notice. So there is now exactly one headline, and the
 * detail line underneath states the only thing a headline cannot: how much of
 * the corpus is still unfinished, and whether that stops you using the rest.
 *
 * Every word here is composed from the shared progress numbers through i18n
 * keys — this component never adds up buckets.
 *
 * It also never INVENTS an estimate, which is not the same as never showing
 * one. It used to close with "you do not have to wait" over a backlog of
 * 235 915 items that measured four days (2026-07-27), because the payload
 * carried no notion of speed and the sentence was written for a queue of
 * minutes. The backend now measures its own completed transitions
 * (jarvis/ultrawiki/throughput.py), so the duration shown here is observed,
 * never modelled — and when it has not been observed long enough, the card
 * says it is still measuring rather than guessing.
 */
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { Eyebrow, Num, formatCount } from "@/components/ultrawiki/overview/primitives";
import { IntakeBar } from "@/components/ultrawiki/overview/IntakeBar";
import { paceOf } from "@/lib/ultrawikiEta";
import type {
  UltraWikiPipeline,
  UltraWikiProgress,
  UltraWikiThroughput,
} from "@/lib/ultrawikiApi";

type Tone = "done" | "working" | "stalled" | "notready" | "empty" | "starting";

const TONE_STYLE: Record<Tone, { headline: string; rule: string }> = {
  done: { headline: "text-[#5bd4a4]", rule: "bg-[#5bd4a4]" },
  working: { headline: "text-primary", rule: "bg-primary" },
  stalled: { headline: "text-[#ffb84d]", rule: "bg-[#ffb84d]" },
  notready: { headline: "text-[#ffb84d]", rule: "bg-[#ffb84d]" },
  empty: { headline: "text-foreground", rule: "bg-muted-foreground/40" },
  starting: { headline: "text-muted-foreground", rule: "bg-muted-foreground/40" },
};

/**
 * Which of the six situations the store is in.
 *
 * Ordered by what a reader needs to hear first: an empty store is not a
 * fault, a stalled queue outranks a busy one, and a backlog never demotes a
 * usable knowledge base to "not ready" — the processed part answers questions
 * while the rest catches up.
 *
 * ``started`` comes first for a reason found in live testing: while the app
 * boots, the status route answers with zeroed counts, and the screen read
 * "Nothing stored yet." over a store holding 4 712 items. A closed store
 * cannot report what is in it, so the honest answer is that it is not open —
 * not a claim about its contents.
 */
export function verdictToneOf(
  progress: UltraWikiProgress,
  pipelineState: string,
  usable: boolean,
  started = true,
): Tone {
  if (!started) return "starting";
  if (progress.total === 0) return "empty";
  if (!usable) return "notready";
  if (progress.waiting > 0) {
    return pipelineState === "paused" ? "stalled" : "working";
  }
  return "done";
}

export function VerdictCard({
  progress,
  pipeline,
  usable,
  started = true,
  throughput,
}: {
  progress: UltraWikiProgress;
  pipeline: UltraWikiPipeline;
  usable: boolean;
  /** Is the knowledge store actually open? A closed store reports zeros. */
  started?: boolean;
  /** Measured lane rates. Absent on an older backend — the card degrades to
   *  its un-timed wording rather than showing a zero it did not measure. */
  throughput?: UltraWikiThroughput;
}): JSX.Element {
  const t = useT();
  const pipelineState = String(pipeline.state ?? "");
  const tone = verdictToneOf(progress, pipelineState, usable, started);
  const style = TONE_STYLE[tone];

  const step = t(
    `ultrawiki.overview.step_${progress.next_step ?? "processing"}`,
  );
  // The embedding lane paces the whole corpus: nothing gets summarised before
  // it is embedded, so its duration is the one the headline detail quotes.
  const pace = paceOf(throughput?.embed, t);
  // A parked summary lane is worth its own line — it is the reason the
  // "summarised" band stops growing while every other number climbs.
  const summaryPause = throughput?.distill?.paused_reason ?? "";
  // One line per situation, spelled out rather than nested into a ternary
  // chain — these sentences are the point of the screen and have to stay easy
  // to read in the source too.
  let detail: string;
  switch (tone) {
    case "starting":
    case "empty":
    case "notready":
      detail = t(`ultrawiki.overview.detail_${tone}`);
      break;
    case "done":
      detail = t("ultrawiki.overview.detail_done").replace(
        "{0}",
        formatCount(progress.total),
      );
      break;
    default: {
      // Four wordings for four genuinely different situations. Picking the
      // measured one only when there IS a measurement is what keeps this
      // honest: an un-timed backlog says so, rather than borrowing the
      // confidence of a number nobody took.
      const key =
        tone === "stalled"
          ? "ultrawiki.overview.detail_stalled"
          : pace.kind === "moving"
            ? "ultrawiki.overview.detail_working_eta"
            : pace.kind === "stalled"
              ? "ultrawiki.overview.detail_working_stalled"
              : "ultrawiki.overview.detail_working";
      detail = t(key)
        .replace("{0}", formatCount(progress.waiting))
        .replace("{1}", formatCount(progress.total))
        .replace("{2}", step)
        .replace("{3}", pace.eta)
        .replace("{4}", pace.perHour === null ? "" : formatCount(pace.perHour));
    }
  }

  return (
    <section data-testid="ultrawiki-verdict" data-tone={tone}>
      <Eyebrow>{t("ultrawiki.overview.eyebrow_verdict")}</Eyebrow>
      <div className="rounded-xl border border-border bg-card/60 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            {/* A short colour rule instead of yet another status icon: the
                section already carries icons in its problem rows, and a
                headline does not need to compete with them. */}
            <span
              className={cn("h-5 w-[3px] shrink-0 rounded-full", style.rule)}
              aria-hidden
            />
            <p
              className={cn(
                "font-display text-lg font-semibold leading-tight tracking-tight",
                style.headline,
              )}
              data-testid="ultrawiki-verdict-headline"
            >
              {t(`ultrawiki.overview.headline_${tone}`)}
            </p>
          </div>
          <span className="flex items-baseline gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground">
            <Num
              value={progress.total}
              className="text-sm font-medium text-foreground"
            />
            {t("ultrawiki.overview.items_label")}
          </span>
        </div>

        <p
          className="mt-2 max-w-2xl text-[13px] leading-relaxed text-muted-foreground"
          data-testid="ultrawiki-verdict-detail"
        >
          {detail}
        </p>

        {/* The reason a paused queue is paused is a backend sentence — shown
            verbatim rather than paraphrased, because guessing at it is how a
            surface starts disagreeing with the one below it. */}
        {tone === "stalled" && pipeline.reason && (
          <p
            className="mt-1.5 text-xs leading-relaxed text-[#ffb84d]"
            data-testid="ultrawiki-verdict-reason"
          >
            {pipeline.reason}
          </p>
        )}

        {/* The summarising lane, when it is deliberately parked. Without this
            the "summarised" band simply stops growing while every other number
            climbs, and a correctly paused stage is indistinguishable from a
            broken one. The backend's own sentence, verbatim. */}
        {summaryPause && (
          <p
            className="mt-1.5 text-xs leading-relaxed text-muted-foreground"
            data-testid="ultrawiki-verdict-summary-pause"
          >
            {t("ultrawiki.overview.pace_paused").replace("{0}", summaryPause)}
          </p>
        )}

        {/* No bar while the store is closed: its numbers would be zeros the
            backend never measured. */}
        {started && progress.total > 0 && (
          <div className="mt-4">
            <IntakeBar
              progress={progress}
              running={pipelineState === "processing"}
            />
          </div>
        )}
      </div>
    </section>
  );
}
