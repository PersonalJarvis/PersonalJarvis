/**
 * The entries — one file, five sections, and the gaps folded away.
 *
 * The old ledger listed all eighteen fields in five separate cards on the
 * argument that "a blank field is as informative as a written one". On a
 * profile the assistant has barely started, that produced sixteen rows of the
 * same three words and five cards with nothing in the bottom two thirds. The
 * page reported its own emptiness in more detail than it reported the reader.
 *
 * So: known facts are entries, and unknown fields collapse into one line per
 * section that opens on click. The count stays visible on every section, so
 * nothing is hidden — it is folded, and folding is what a file does with
 * pages nobody has written yet.
 *
 * One card rather than five, because five boxes read as a dashboard and this
 * is a document.
 */
import { useState } from "react";
import { ChevronDown } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { clusterDataOf } from "@/views/profile/api";
import { EntryRow } from "@/views/profile/EntryRow";
import {
  CLUSTER_FIELD_KEYS,
  CLUSTER_ORDER,
  clusterFilledCount,
  isEmptyValue,
  type ClusterId,
} from "@/views/profile/ledger";
import { historyFor, latestFor, type Observation } from "@/views/profile/provenance";

export function EntryLedger({
  meta,
  observations,
}: {
  meta: Record<string, unknown>;
  observations: readonly Observation[];
}) {
  return (
    <Card className="profile-rise divide-y divide-border">
      {CLUSTER_ORDER.map((cid, i) => (
        <ClusterSection
          key={cid}
          cid={cid}
          meta={meta}
          observations={observations}
          delayMs={60 + i * 40}
        />
      ))}
    </Card>
  );
}

function ClusterSection({
  cid,
  meta,
  observations,
  delayMs,
}: {
  cid: ClusterId;
  meta: Record<string, unknown>;
  observations: readonly Observation[];
  delayMs: number;
}) {
  const t = useT();
  const [showGaps, setShowGaps] = useState(false);

  const data = clusterDataOf(meta, cid);
  const fields = CLUSTER_FIELD_KEYS[cid];
  const filled = clusterFilledCount(meta, cid);
  const complete = filled === fields.length;

  const known = fields.filter((key) => !isEmptyValue(data[key]));
  const gaps = fields.filter((key) => isEmptyValue(data[key]));

  const countLabel = t("profile_view.group_filled")
    .replace("{0}", String(filled))
    .replace("{1}", String(fields.length));

  const row = (key: string) => (
    <EntryRow
      key={key}
      cid={cid}
      fieldKey={key}
      value={data[key]}
      latest={latestFor(observations, cid, key)}
      history={historyFor(observations, cid, key)}
    />
  );

  return (
    <section
      className="profile-rise px-5 py-4"
      style={{ animationDelay: `${delayMs}ms` }}
      aria-label={t(`profile_view.clusters.${cid}.label`)}
    >
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-title font-semibold text-foreground-strong">
          {t(`profile_view.clusters.${cid}.label`)}
        </h3>
        <Badge
          variant={complete ? "success" : "secondary"}
          aria-label={countLabel}
          title={countLabel}
          className="tabular-nums"
        >
          {filled}/{fields.length}
        </Badge>
      </div>

      {/* The rows keep a measure while the section header spans the card: a
          fact set 1200 px wide has its two ends a hand-span apart, and the
          empty right side then reads as margin rather than as a hole. */}
      <div className="mt-2 max-w-reading">
        {known.map(row)}

        {gaps.length > 0 && (
          <>
            {showGaps && <div className="mt-1">{gaps.map(row)}</div>}
            <button
              type="button"
              onClick={() => setShowGaps((v) => !v)}
              aria-expanded={showGaps}
              data-testid={`gaps-${cid}`}
              className={cn(
                "-mx-2 mt-1 flex w-[calc(100%+1rem)] items-center gap-1.5 rounded-md px-2 py-1.5",
                "text-meta text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
            >
              <ChevronDown
                aria-hidden
                className={cn("h-3.5 w-3.5 transition-transform", showGaps && "rotate-180")}
              />
              {showGaps
                ? t("profile_view.gaps_hide")
                : t("profile_view.gaps_show").replace("{0}", String(gaps.length))}
            </button>
          </>
        )}
      </div>
    </section>
  );
}
