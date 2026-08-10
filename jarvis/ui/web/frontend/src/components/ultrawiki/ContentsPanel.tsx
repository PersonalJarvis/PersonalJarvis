/**
 * Contents tab — the inventory of what is actually IN the UltraWiki database.
 *
 * Every other surface answers "how many": counts per stage, a funnel, a job
 * summary. None of them answered the question the maintainer actually asked
 * while looking at the live app — "what is even in there?". This lists the
 * items themselves, newest-ingested first, filterable by source and pipeline
 * stage, each row linking out to where it came from.
 *
 * Paging is explicit ("Load more") rather than infinite scroll: the total is
 * shown honestly next to what is loaded, so a store with 40 000 items reads as
 * 40 000 items and not as an endless list.
 */
import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Database, ExternalLink, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { formatRelativeTime } from "@/components/ultrawiki/relativeTime";
import { ItemDetailDialog } from "@/components/ultrawiki/ItemDetailDialog";
import {
  fetchUltraWikiItems,
  type UltraWikiItem,
  type UltraWikiStatus,
} from "@/lib/ultrawikiApi";

const PAGE_SIZE = 50;

/** Mirrors jarvis/ultrawiki/types.py::ItemState (AP-4: never retyped). */
const STATE_FILTERS = [
  ["captured", "ultrawiki.progress.stage_captured"],
  ["keyword_indexed", "ultrawiki.progress.stage_keyword_indexed"],
  ["embedded", "ultrawiki.progress.stage_embedded"],
  ["distilled", "ultrawiki.progress.stage_distilled"],
  ["failed", "ultrawiki.progress.stage_failed"],
] as const;

export function ContentsPanel({
  status,
  onOpenSources,
}: {
  status: UltraWikiStatus;
  onOpenSources?: () => void;
}): JSX.Element {
  const t = useT();
  const [sourceId, setSourceId] = useState("");
  const [state, setState] = useState("");
  const [rows, setRows] = useState<UltraWikiItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const sourceLabels = new Map(
    status.sources.map((source) => [source.id, source.label]),
  );

  const load = useCallback(
    async (offset: number) => {
      setLoading(true);
      setError("");
      try {
        const page = await fetchUltraWikiItems({
          sourceId: sourceId || undefined,
          state: state || undefined,
          limit: PAGE_SIZE,
          offset,
        });
        // offset 0 is a fresh filter run; anything else appends a page.
        setRows((current) =>
          offset === 0 ? page.items : [...current, ...page.items],
        );
        setTotal(page.total);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [sourceId, state],
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  // Which row is opened for inspection. A list of titles cannot answer "is
  // the real content in there?" — only opening one can.
  const [openItemId, setOpenItemId] = useState<number | null>(null);

  const stageChips = STATE_FILTERS.map(([key, labelKey]) => ({
    key,
    label: t(labelKey),
    value: status.counts[key] ?? 0,
  }));

  return (
    <div className="space-y-3 p-4" data-testid="ultrawiki-contents-panel">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium text-foreground">
          <Database className="h-3.5 w-3.5" aria-hidden />
          {t("ultrawiki.contents.title")}
        </h3>
        <span
          className="rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-foreground"
          data-testid="ultrawiki-contents-total"
        >
          {t("ultrawiki.contents.total").replace(
            "{0}",
            String(status.counts.total ?? 0),
          )}
        </span>
        {stageChips.map((chip) => (
          <span
            key={chip.key}
            className={cn(
              "rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground",
              chip.key === "failed" &&
                chip.value > 0 &&
                "border-destructive/40 text-destructive",
            )}
            data-testid={`ultrawiki-contents-chip-${chip.key}`}
          >
            {chip.label} {chip.value}
          </span>
        ))}
      </div>

      <div className="flex flex-wrap gap-2">
        <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          {t("ultrawiki.contents.filter_source")}
          <BrandedSelect
            value={sourceId}
            onValueChange={setSourceId}
            ariaLabel={t("ultrawiki.contents.filter_source")}
            className="w-auto min-w-40 px-2 py-1 text-xs"
            testId="ultrawiki-contents-source-filter"
            options={[
              { value: "", label: t("ultrawiki.contents.all_sources") },
              ...status.sources.map((source) => ({
                value: source.id,
                label: source.label,
              })),
            ]}
          />
        </label>
        <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          {t("ultrawiki.contents.filter_state")}
          <BrandedSelect
            value={state}
            onValueChange={setState}
            ariaLabel={t("ultrawiki.contents.filter_state")}
            className="w-auto min-w-40 px-2 py-1 text-xs"
            testId="ultrawiki-contents-state-filter"
            options={[
              { value: "", label: t("ultrawiki.contents.all_states") },
              ...STATE_FILTERS.map(([key, labelKey]) => ({
                value: key,
                label: t(labelKey),
              })),
            ]}
          />
        </label>
      </div>

      {error && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-lg border border-destructive/50 bg-destructive/10 p-2.5 text-xs text-destructive"
          data-testid="ultrawiki-contents-error"
        >
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          {t("ultrawiki.contents.load_failed").replace("{0}", error)}
        </div>
      )}

      {rows.length === 0 && !loading && !error ? (
        <div
          className="rounded-lg border border-dashed border-border/70 bg-card/30 p-4 text-xs text-muted-foreground"
          data-testid="ultrawiki-contents-empty"
        >
          <p>{t("ultrawiki.contents.empty")}</p>
          {onOpenSources && (
            <button
              type="button"
              onClick={onOpenSources}
              className="mt-1 underline underline-offset-2 hover:text-foreground"
              data-testid="ultrawiki-contents-open-sources"
            >
              {t("ultrawiki.contents.open_sources")}
            </button>
          )}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full text-left text-xs">
            <thead className="bg-card/60 text-[10px] uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-normal">
                  {t("ultrawiki.contents.col_title")}
                </th>
                <th className="px-3 py-2 font-normal">
                  {t("ultrawiki.contents.col_source")}
                </th>
                <th className="px-3 py-2 font-normal">
                  {t("ultrawiki.contents.col_state")}
                </th>
                <th className="px-3 py-2 font-normal">
                  {t("ultrawiki.contents.col_loaded")}
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <tr
                  key={item.id}
                  className="cursor-pointer border-t border-border/60 hover:bg-primary/[0.04]"
                  onClick={() => setOpenItemId(item.id)}
                  data-testid={`ultrawiki-item-${item.id}`}
                >
                  <td className="max-w-[22rem] px-3 py-2">
                    {/* The title opens what is STORED; the arrow goes to the
                        source. Two different questions, two different targets —
                        so the link stops the row's click. */}
                    <span className="inline-flex items-center gap-1">
                      <button
                        type="button"
                        className="truncate text-left text-foreground underline-offset-2 hover:underline"
                        data-testid={`ultrawiki-item-open-${item.id}`}
                      >
                        {item.title}
                      </button>
                      {item.permalink && (
                        <a
                          href={item.permalink}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          aria-label={t("ultrawiki.item.open_source")}
                          title={t("ultrawiki.item.open_source")}
                        >
                          <ExternalLink
                            className="h-3 w-3 shrink-0 text-muted-foreground hover:text-primary"
                            aria-hidden
                          />
                        </a>
                      )}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {sourceLabels.get(item.source_id) ?? item.source_id}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={cn(
                        "rounded-full border px-1.5 py-0.5 text-[10px]",
                        item.state === "failed"
                          ? "border-destructive/40 bg-destructive/10 text-destructive"
                          : "border-border text-muted-foreground",
                      )}
                      data-state={item.state}
                    >
                      {/* Through the shared label map, never the raw state
                          name: "embedded" is a pipeline word, and the row is
                          read by someone who did not write the pipeline. */}
                      {t(`ultrawiki.progress.stage_${item.state}`)}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {t("ultrawiki.contents.loaded_ago").replace(
                      "{0}",
                      formatRelativeTime(item.ingested_at, t),
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        {rows.length > 0 && (
          <span
            className="text-[11px] text-muted-foreground"
            data-testid="ultrawiki-contents-showing"
          >
            {t("ultrawiki.contents.showing")
              .replace("{0}", String(rows.length))
              .replace("{1}", String(total))}
          </span>
        )}
        {rows.length < total && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => void load(rows.length)}
            disabled={loading}
            data-testid="ultrawiki-contents-load-more"
          >
            {loading && (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
            )}
            {t("ultrawiki.contents.load_more")}
          </Button>
        )}
        {loading && rows.length === 0 && (
          <span
            className="flex items-center gap-1.5 text-[11px] text-muted-foreground"
            data-testid="ultrawiki-contents-loading"
          >
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            {t("ultrawiki.contents.loading")}
          </span>
        )}
      </div>
      {openItemId !== null && (
        <ItemDetailDialog
          itemId={openItemId}
          onClose={() => setOpenItemId(null)}
        />
      )}

    </div>
  );
}
