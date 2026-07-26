/**
 * "Where it comes from" — every source as one line, with the number that
 * proves it actually delivered something.
 *
 * The Sources tab already lets you add, approve and revoke; what it never
 * answered at a glance was the question people actually have about a source
 * they connected weeks ago: *did anything come out of it?* A source can be
 * approved, enabled, error-free and still have contributed zero items — the
 * exact state that once left a knowledge base empty for days while every
 * screen reported success. So the item count is the loudest thing in the row,
 * and a source that has delivered nothing says so in words.
 */
import { useState } from "react";
import { Loader2, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { ConnectorBrandMark } from "@/components/ultrawiki/ConnectorBrand";
import { formatRelativeTime } from "@/components/ultrawiki/relativeTime";
import { Eyebrow, Num } from "@/components/ultrawiki/overview/primitives";
import { startUltraWikiSync, type UltraWikiSource } from "@/lib/ultrawikiApi";

type RowState = "importing" | "failed" | "pending" | "never" | "empty" | "ok";

const ROW_TONE: Record<RowState, string> = {
  importing: "text-primary",
  failed: "text-destructive",
  pending: "text-[#ffb84d]",
  never: "text-[#ffb84d]",
  empty: "text-[#ffb84d]",
  ok: "text-muted-foreground",
};

/**
 * What this source's line should say about itself.
 *
 * "empty" is the one worth spelling out: it ran, it worked, and it produced
 * nothing. Collapsing that into "ok" is how a dead integration hides behind a
 * green tick.
 */
export function rowStateOf(source: UltraWikiSource): RowState {
  if (source.active_job) return "importing";
  if (source.last_error) return "failed";
  if (source.consent !== "approved") return "pending";
  if (!source.last_sync_at && !source.last_outcome) return "never";
  if (!(source.counts?.total ?? 0)) return "empty";
  return "ok";
}

export function SourceRoster({
  sources,
  onChanged,
  onOpenSources,
}: {
  sources: UltraWikiSource[];
  onChanged: () => void;
  onOpenSources: () => void;
}): JSX.Element {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function importNow(source: UltraWikiSource) {
    setBusyId(source.id);
    try {
      await startUltraWikiSync(source.id);
      pushToast(
        "success",
        t("ultrawiki.overview.import_started").replace("{0}", source.label),
      );
      onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section data-testid="ultrawiki-source-roster">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <Eyebrow className="mb-0">{t("ultrawiki.overview.eyebrow_sources")}</Eyebrow>
        <button
          type="button"
          onClick={onOpenSources}
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          data-testid="ultrawiki-roster-manage"
        >
          <Plus className="h-3 w-3" aria-hidden />
          {t("ultrawiki.overview.manage_sources")}
        </button>
      </div>

      {sources.length === 0 ? (
        <div
          className="rounded-xl border border-dashed border-border bg-card/30 p-4"
          data-testid="ultrawiki-roster-empty"
        >
          <p className="text-[13px] text-muted-foreground">
            {t("ultrawiki.overview.no_sources")}
          </p>
          <Button
            size="sm"
            className="mt-2.5"
            onClick={onOpenSources}
            data-testid="ultrawiki-roster-empty-action"
          >
            {t("ultrawiki.overview.add_first_source")}
          </Button>
        </div>
      ) : (
        <ul className="divide-y divide-border/60 overflow-hidden rounded-xl border border-border bg-card/40">
          {sources.map((source) => {
            const state = rowStateOf(source);
            const items = source.counts?.total ?? 0;
            const when = formatRelativeTime(source.last_sync_at, t);
            return (
              <li
                key={source.id}
                className="flex items-center gap-3 px-3 py-2.5"
                data-testid={`ultrawiki-roster-row-${source.id}`}
                data-row-state={state}
              >
                <ConnectorBrandMark
                  brand={source.brand ?? ""}
                  label={source.label}
                  size="sm"
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] font-medium text-foreground">
                    {source.label}
                  </p>
                  <p className={cn("truncate text-[11px]", ROW_TONE[state])}>
                    {state === "failed"
                      ? source.last_error
                      : state === "ok"
                        ? t("ultrawiki.overview.row_read").replace("{0}", when)
                        : t(`ultrawiki.overview.row_${state}`)}
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <Num
                    value={items}
                    className="text-sm font-medium text-foreground"
                  />
                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    {t("ultrawiki.overview.items_label")}
                  </p>
                </div>
                {source.consent === "approved" && !source.active_job && (
                  <Button
                    size="sm"
                    variant={state === "never" || state === "empty" ? "default" : "outline"}
                    onClick={() => void importNow(source)}
                    disabled={busyId === source.id}
                    data-testid={`ultrawiki-roster-import-${source.id}`}
                  >
                    {busyId === source.id && (
                      <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
                    )}
                    {t("ultrawiki.overview.import_now")}
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
