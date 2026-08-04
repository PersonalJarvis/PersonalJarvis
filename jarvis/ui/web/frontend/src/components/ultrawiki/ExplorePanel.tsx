/**
 * Explore — the readable face of the knowledge base.
 *
 * Everything else in the Ultra panel is administrative: what is connected,
 * what is importing, what is configured. This is the one view that answers
 * the question the user actually has — "what does it know about my life?"
 *
 * Layout: three columns, and the split is the whole design.
 *
 *   ┌───────────┬──────────────────────────┬───────────────┐
 *   │ topics    │        THE MAP           │    reader     │
 *   │ (17 rem)  │       (all the rest)     │   (27 rem)    │
 *   └───────────┴──────────────────────────┴───────────────┘
 *
 * It replaced a stack — a 224 px map wedged above a full-width list — which
 * failed at both ends: a force layout squeezed into a letterbox is a smear,
 * and a paragraph set 1 500 px wide is a wall no one reads a second line of.
 * Splitting them fixes both with the same move. The map gets area, the prose
 * gets a column about 65 characters across, which is the width text has been
 * set at for four hundred years.
 *
 * Visual encoding (lib/entityGraph.ts, never inline):
 * - how OFTEN a topic comes up → its count and its node size in the graph
 * - WHEN it lived in your history → the time bar under each topic and the
 *   warmth of its node, ash → signal yellow
 * Both are derived from the data. There is no decorative colour in here.
 *
 * The time bar is the signature of this view: at a glance you see that
 * "Berlin" runs the whole way through while "Bora Bora" was one bright week
 * in July — a shape no list of counts can show.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ChevronDown, ExternalLink, Search } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import {
  corpusSpan,
  recencyTint,
  spanBar,
  type CorpusSpan,
} from "@/lib/entityGraph";
import {
  exploreReasonKey,
  fetchExploreEntities,
  fetchExploreEntity,
  fetchExploreMoments,
  type UltraWikiEntity,
  type UltraWikiExploreReason,
  type UltraWikiMoment,
} from "@/lib/ultrawikiExploreApi";
import { EntityGraph } from "@/components/ultrawiki/EntityGraph";
import { VaultBar } from "@/components/ultrawiki/VaultBar";

export interface ExplorePanelProps {
  onOpenSources: () => void;
  onOpenSettings: () => void;
}

/** Which tab an empty state should send the user to, if any. */
const EMPTY_ACTION: Record<UltraWikiExploreReason, "sources" | "settings" | null> =
  {
    ok: null,
    no_sources: "sources",
    nothing_imported: "sources",
    nothing_distilled: "settings",
    no_entities: null,
  };

export function ExplorePanel({
  onOpenSources,
  onOpenSettings,
}: ExplorePanelProps): JSX.Element {
  const t = useT();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [minMentions, setMinMentions] = useState(2);

  const entitiesQuery = useQuery({
    queryKey: ["ultrawiki", "explore", "entities"],
    // The whole topic list is small (a real corpus measured under a thousand)
    // and filtering happens locally, so typing stays instant instead of
    // firing a request per keystroke.
    queryFn: () => fetchExploreEntities({ limit: 2000 }),
    staleTime: 30_000,
  });

  const momentsQuery = useQuery({
    queryKey: ["ultrawiki", "explore", "moments"],
    queryFn: () => fetchExploreMoments({ limit: 200 }),
    staleTime: 30_000,
  });

  const detailQuery = useQuery({
    queryKey: ["ultrawiki", "explore", "entity", selected],
    queryFn: () => fetchExploreEntity(selected as string),
    enabled: selected !== null,
    staleTime: 30_000,
  });

  const entities = entitiesQuery.data?.entities ?? [];
  const span = useMemo(() => corpusSpan(entities), [entities]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return entities;
    return entities.filter(
      (entity) =>
        entity.key.includes(needle) ||
        entity.label.toLowerCase().includes(needle),
    );
  }, [entities, search]);

  if (entitiesQuery.isError && momentsQuery.isError) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <p
          role="alert"
          data-testid="explore-error"
          className="max-w-md rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          {t("ultrawiki.explore.error")}
        </p>
      </div>
    );
  }

  if (entitiesQuery.isLoading) {
    return (
      <div
        data-testid="explore-loading"
        className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground"
      >
        {t("ultrawiki.explore.loading")}
      </div>
    );
  }

  const reason = entitiesQuery.data?.reason ?? "ok";
  const corpus = entitiesQuery.data?.corpus ?? {
    sources: 0,
    items: 0,
    distilled: 0,
  };
  const action = EMPTY_ACTION[reason];
  const detail = detailQuery.data?.entity.key === selected ? detailQuery.data : null;

  return (
    // h-full, not flex-1: the tab body this renders into is itself a
    // scrolling container, and inside one of those `flex-1` imposes no
    // ceiling — the panel grew to 46 000 px on a real corpus, pushing the
    // vault strip somewhere no one would ever scroll to. Taking the parent's
    // height instead is what makes the inner lists scroll on their own.
    <div
      className="flex h-full min-h-0 min-w-0 flex-col"
      data-testid="explore-panel"
    >
      {reason !== "ok" && (
        <div
          data-testid="explore-empty"
          data-reason={reason}
          className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-border bg-muted/30 px-4 py-2.5 text-xs text-muted-foreground"
        >
          <span>{t(exploreReasonKey(reason))}</span>
          {action && (
            <button
              type="button"
              data-testid="explore-empty-action"
              onClick={action === "sources" ? onOpenSources : onOpenSettings}
              className="underline underline-offset-2 hover:text-foreground"
            >
              {action === "sources"
                ? t("ultrawiki.panel.tab_sources")
                : t("ultrawiki.panel.tab_settings")}
            </button>
          )}
        </div>
      )}

      {/* min-w-0 on every column of this row, without exception: the map
          paints a canvas at a fixed pixel width, and one flex child that
          cannot shrink below its content is all it takes to give the whole
          section a horizontal scrollbar it can never shrink back out of. */}
      <div
        data-testid="explore-columns"
        className="flex min-h-0 min-w-0 flex-1 flex-col xl:flex-row"
      >
        <aside className="flex min-h-0 min-w-0 shrink-0 flex-col border-b border-border xl:w-[17rem] xl:border-b-0 xl:border-r">
          <div className="relative shrink-0 px-3 pb-2 pt-3">
            <Search
              className="pointer-events-none absolute left-6 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <input
              data-testid="explore-search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("ultrawiki.explore.search_placeholder")}
              aria-label={t("ultrawiki.explore.search_placeholder")}
              className="h-8 w-full rounded-lg border border-border bg-background/60 pl-8 pr-2.5 text-xs outline-none transition-colors placeholder:text-muted-foreground hover:border-border focus-visible:border-primary/50 focus-visible:ring-1 focus-visible:ring-primary/40"
            />
          </div>

          <p className="shrink-0 px-4 pb-1.5 pt-1 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            {t("ultrawiki.explore.entities_count").replace(
              "{0}",
              String(filtered.length),
            )}
          </p>

          <div className="scrollbar-jarvis min-h-0 flex-1 overflow-y-auto px-1.5 pb-2 max-xl:max-h-56">
            {filtered.length === 0 && search.trim() !== "" && (
              <p
                data-testid="explore-no-search-hits"
                className="px-2.5 py-3 text-xs text-muted-foreground"
              >
                {t("ultrawiki.explore.no_search_hits").replace("{0}", search.trim())}
              </p>
            )}
            {filtered.map((entity) => (
              <EntityRow
                key={entity.key}
                entity={entity}
                span={span}
                active={entity.key === selected}
                onSelect={() => setSelected(entity.key)}
              />
            ))}
          </div>
        </aside>

        {/* The map. No fixed height anywhere on this branch — it takes the
            column, which on a desktop window is most of the screen. */}
        <div className="min-h-[20rem] min-w-0 shrink-0 border-b border-border xl:min-h-0 xl:flex-1 xl:shrink xl:border-b-0">
          <EntityGraph
            minMentions={minMentions}
            onMinMentionsChange={setMinMentions}
            selectedKey={selected}
            onSelect={setSelected}
          />
        </div>

        <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-card/20 xl:w-[27rem] xl:flex-none xl:border-l xl:border-border">
          <header className="flex shrink-0 items-start gap-2 border-b border-border px-4 py-2.5">
            {selected && (
              <button
                type="button"
                data-testid="explore-reader-back"
                onClick={() => setSelected(null)}
                aria-label={t("ultrawiki.explore.back")}
                title={t("ultrawiki.explore.back")}
                className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:text-primary"
              >
                <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
              </button>
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                {selected && detail
                  ? t("ultrawiki.explore.moments_of").replace(
                      "{0}",
                      detail.entity.label,
                    )
                  : t("ultrawiki.explore.moments_title")}
              </p>
              <p className="text-[11px] tabular-nums text-muted-foreground">
                {t("ultrawiki.explore.moments_count").replace(
                  "{0}",
                  String(
                    selected && detail
                      ? detail.moments.length
                      : (momentsQuery.data?.total ?? 0),
                  ),
                )}
                {!selected && (
                  <>
                    {" · "}
                    <span className="normal-case">
                      {t("ultrawiki.explore.reader_hint")}
                    </span>
                  </>
                )}
              </p>
            </div>
          </header>

          <div className="scrollbar-jarvis min-h-0 flex-1 overflow-y-auto">
            {selected && detail ? (
              <EntityDetail
                entity={detail.entity}
                moments={detail.moments}
                onSelect={setSelected}
              />
            ) : (
              <MomentList moments={momentsQuery.data?.moments ?? []} />
            )}
          </div>
        </section>
      </div>

      <VaultBar />

      <p className="sr-only" data-testid="explore-corpus">
        {corpus.sources}/{corpus.items}/{corpus.distilled}
      </p>
    </div>
  );
}

function EntityRow({
  entity,
  span,
  active,
  onSelect,
}: {
  entity: UltraWikiEntity;
  span: CorpusSpan;
  active: boolean;
  onSelect: () => void;
}): JSX.Element {
  const bar = spanBar(entity, span);
  const tint = recencyTint(entity.last_seen, span);
  return (
    <button
      type="button"
      onClick={onSelect}
      data-testid={`explore-entity-${entity.key}`}
      data-entity-key={entity.key}
      data-active={active ? "true" : "false"}
      className={cn(
        "group block w-full rounded-lg px-2.5 py-1.5 text-left transition-colors",
        active
          ? "bg-primary/10 ring-1 ring-inset ring-primary/25"
          : "hover:bg-muted/60",
      )}
    >
      <span className="flex items-baseline justify-between gap-2">
        <span
          className={cn(
            "truncate text-xs",
            active ? "text-primary" : "text-foreground",
          )}
        >
          {entity.label}
        </span>
        <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
          {entity.mentions}
        </span>
      </span>
      {/* The signature of this view: where in your history this topic lived.
          Width is its lifetime, position is when, warmth is how recent. */}
      <span
        className="mt-1 block h-[3px] w-full rounded-full bg-sheen/[0.06]"
        aria-hidden
      >
        <span
          data-testid={`explore-span-${entity.key}`}
          data-width={bar.width.toFixed(4)}
          data-offset={bar.offset.toFixed(4)}
          className="block h-full rounded-full"
          style={{
            marginLeft: `${bar.offset * 100}%`,
            width: `${bar.width * 100}%`,
            backgroundColor: tint,
          }}
        />
      </span>
    </button>
  );
}

function EntityDetail({
  entity,
  moments,
  onSelect,
}: {
  entity: UltraWikiEntity;
  moments: UltraWikiMoment[];
  onSelect: (key: string) => void;
}): JSX.Element {
  const t = useT();
  return (
    <div data-testid="explore-detail">
      <div className="border-b border-border px-4 py-3.5">
        <h3 className="text-sm text-foreground">{entity.label}</h3>
        <p className="mt-0.5 text-[11px] tabular-nums text-muted-foreground">
          {t("ultrawiki.explore.mentions_long").replace(
            "{0}",
            String(entity.mentions),
          )}
          {entity.first_seen && (
            <>
              {" · "}
              {t("ultrawiki.explore.period")
                .replace("{0}", entity.first_seen.slice(0, 10))
                .replace("{1}", entity.last_seen.slice(0, 10))}
            </>
          )}
        </p>

        <p className="mt-3 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          {t("ultrawiki.explore.neighbors")}
        </p>
        {entity.neighbors.length === 0 ? (
          <p className="mt-1 text-xs text-muted-foreground">
            {t("ultrawiki.explore.no_neighbors")}
          </p>
        ) : (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {entity.neighbors.slice(0, 12).map((neighbor) => (
              <button
                key={neighbor.key}
                type="button"
                data-testid={`explore-neighbor-${neighbor.key}`}
                onClick={() => onSelect(neighbor.key)}
                title={t("ultrawiki.explore.shared").replace(
                  "{0}",
                  String(neighbor.shared),
                )}
                className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
              >
                {neighbor.label}
                <span className="ml-1 tabular-nums opacity-60">
                  {neighbor.shared}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      <MomentList moments={moments} />
    </div>
  );
}

function MomentList({ moments }: { moments: UltraWikiMoment[] }): JSX.Element {
  return (
    <ol className="space-y-2 p-3">
      {moments.map((moment) => (
        <MomentCard key={moment.document_id} moment={moment} />
      ))}
    </ol>
  );
}

/**
 * One distilled moment, as something you can actually read.
 *
 * Closed it is a headline and three lines — enough to scan a couple of
 * hundred of them. Open it is the whole summary plus the answer the
 * distillation arrived at, which until now was fetched, stored, and never
 * shown to anyone.
 */
function MomentCard({ moment }: { moment: UltraWikiMoment }): JSX.Element {
  const t = useT();
  const [open, setOpen] = useState(false);
  const expandable = Boolean(moment.summary || moment.resolution);

  return (
    <li
      data-testid={`explore-moment-${moment.document_id}`}
      data-open={open ? "true" : "false"}
      className="rounded-xl border border-border/70 bg-card/40 px-3.5 py-3 transition-colors hover:border-primary/25 hover:bg-card/70"
    >
      <button
        type="button"
        disabled={!expandable}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={expandable ? open : undefined}
        aria-label={
          expandable
            ? t(open ? "ultrawiki.explore.read_less" : "ultrawiki.explore.read_more")
            : undefined
        }
        className="flex w-full items-start gap-2 text-left disabled:cursor-default"
      >
        <span className="min-w-0 flex-1 text-[13px] font-medium leading-relaxed text-foreground">
          {moment.title}
        </span>
        {expandable && (
          <ChevronDown
            className={cn(
              "mt-1 h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-180",
            )}
            aria-hidden
          />
        )}
      </button>

      {moment.summary && (
        <p
          className={cn(
            "mt-1.5 text-xs leading-relaxed text-muted-foreground",
            !open && "line-clamp-3",
          )}
        >
          {moment.summary}
        </p>
      )}

      {open && moment.resolution && (
        <p className="mt-2.5 border-l-2 border-primary/40 pl-2.5 text-xs leading-relaxed text-foreground/90">
          {moment.resolution}
        </p>
      )}

      <p className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] tabular-nums text-muted-foreground">
        <span>{moment.timestamp_utc.slice(0, 10)}</span>
        <span aria-hidden>·</span>
        <span className="truncate">{moment.source_label}</span>
        {moment.permalink && (
          <a
            href={moment.permalink}
            data-testid={`explore-moment-link-${moment.document_id}`}
            className="inline-flex items-center gap-0.5 transition-colors hover:text-primary"
          >
            <ExternalLink className="h-2.5 w-2.5" aria-hidden />
            {t("ultrawiki.explore.open_source")}
          </a>
        )}
      </p>
    </li>
  );
}

export default ExplorePanel;
