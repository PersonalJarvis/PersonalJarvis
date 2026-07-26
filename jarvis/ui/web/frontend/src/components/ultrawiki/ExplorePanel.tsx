/**
 * Explore — the readable face of the knowledge base.
 *
 * Everything else in the Ultra panel is administrative: what is connected,
 * what is importing, what is configured. This is the one view that answers
 * the question the user actually has — "what does it know about my life?"
 *
 * Visual encoding (lib/entityGraph.ts, never inline):
 * - how OFTEN a topic comes up → its count and its node size in the graph
 * - WHEN it lived in your history → the time bar under each topic and the
 *   brightness of its node
 * Both are derived from the data. There is no decorative colour in here.
 *
 * The time bar is the signature of this view: at a glance you see that
 * "Berlin" runs the whole way through while "Bora Bora" was one bright week
 * in July — a shape no list of counts can show.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink, Search } from "lucide-react";

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

  return (
    // h-full, not flex-1: the tab body this renders into is itself a
    // scrolling container, and inside one of those `flex-1` imposes no
    // ceiling — the panel grew to 46 000 px on a real corpus, pushing the
    // vault strip somewhere no one would ever scroll to. Taking the parent's
    // height instead is what makes the inner lists scroll on their own.
    <div className="flex h-full min-h-0 flex-col" data-testid="explore-panel">
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

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <aside className="flex min-h-0 shrink-0 flex-col border-b border-border lg:w-72 lg:border-b-0 lg:border-r">
          <div className="relative border-b border-border px-3 py-2">
            <Search
              className="pointer-events-none absolute left-5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <input
              data-testid="explore-search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("ultrawiki.explore.search_placeholder")}
              aria-label={t("ultrawiki.explore.search_placeholder")}
              className="w-full rounded-md border border-border bg-background py-1.5 pl-7 pr-2 text-xs outline-none placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-primary"
            />
          </div>

          <p className="px-4 py-1.5 text-[11px] uppercase tracking-wide text-muted-foreground">
            {t("ultrawiki.explore.entities_count").replace(
              "{0}",
              String(filtered.length),
            )}
          </p>

          <div className="min-h-0 flex-1 overflow-y-auto pb-2 max-lg:max-h-64">
            {filtered.length === 0 && search.trim() !== "" && (
              <p
                data-testid="explore-no-search-hits"
                className="px-4 py-3 text-xs text-muted-foreground"
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

        <div className="flex min-h-0 flex-1 flex-col">
          <div className="h-56 shrink-0 border-b border-border sm:h-64">
            <EntityGraph
              minMentions={minMentions}
              onMinMentionsChange={setMinMentions}
              selectedKey={selected}
              onSelect={setSelected}
            />
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {selected && detailQuery.data ? (
              <EntityDetail
                entity={detailQuery.data.entity}
                moments={detailQuery.data.moments}
                onBack={() => setSelected(null)}
                onSelect={setSelected}
              />
            ) : (
              <MomentList
                title={t("ultrawiki.explore.moments_title")}
                moments={momentsQuery.data?.moments ?? []}
                count={momentsQuery.data?.total ?? 0}
              />
            )}
          </div>
        </div>
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
        "group block w-full px-4 py-1.5 text-left transition-colors",
        active ? "bg-primary/10" : "hover:bg-muted/50",
      )}
    >
      <span className="flex items-baseline justify-between gap-2">
        <span className="truncate text-xs text-foreground">{entity.label}</span>
        <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
          {entity.mentions}
        </span>
      </span>
      {/* The signature of this view: where in your history this topic lived.
          Width is its lifetime, position is when, brightness is how recent. */}
      <span
        className="mt-1 block h-[3px] w-full rounded-full bg-muted"
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
  onBack,
  onSelect,
}: {
  entity: UltraWikiEntity;
  moments: UltraWikiMoment[];
  onBack: () => void;
  onSelect: (key: string) => void;
}): JSX.Element {
  const t = useT();
  return (
    <div className="p-4" data-testid="explore-detail">
      <button
        type="button"
        onClick={onBack}
        className="mb-3 flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3 w-3" aria-hidden />
        {t("ultrawiki.explore.back")}
      </button>

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

      <div className="mt-3">
        <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
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
                className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground hover:border-primary/50 hover:text-foreground"
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

      <MomentList
        title={t("ultrawiki.explore.moments_of").replace("{0}", entity.label)}
        moments={moments}
        count={moments.length}
        className="mt-4"
      />
    </div>
  );
}

function MomentList({
  title,
  moments,
  count,
  className,
}: {
  title: string;
  moments: UltraWikiMoment[];
  count: number;
  className?: string;
}): JSX.Element {
  const t = useT();
  return (
    <div className={cn("px-4 py-3", className)}>
      <p className="flex items-baseline gap-2 text-[11px] uppercase tracking-wide text-muted-foreground">
        {title}
        <span className="tabular-nums normal-case opacity-70">
          {t("ultrawiki.explore.moments_count").replace("{0}", String(count))}
        </span>
      </p>
      <ol className="mt-2 space-y-2.5">
        {moments.map((moment) => (
          <li
            key={moment.document_id}
            data-testid={`explore-moment-${moment.document_id}`}
            className="border-l-2 border-border pl-3"
          >
            <p className="text-xs leading-snug text-foreground">{moment.title}</p>
            {moment.summary && (
              <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-muted-foreground">
                {moment.summary}
              </p>
            )}
            <p className="mt-1 flex items-center gap-1.5 text-[10px] tabular-nums text-muted-foreground">
              <span>{moment.timestamp_utc.slice(0, 10)}</span>
              <span aria-hidden>·</span>
              <span className="truncate">{moment.source_label}</span>
              {moment.permalink && (
                <a
                  href={moment.permalink}
                  data-testid={`explore-moment-link-${moment.document_id}`}
                  className="inline-flex items-center gap-0.5 hover:text-foreground"
                >
                  <ExternalLink className="h-2.5 w-2.5" aria-hidden />
                  {t("ultrawiki.explore.open_source")}
                </a>
              )}
            </p>
          </li>
        ))}
      </ol>
    </div>
  );
}

export default ExplorePanel;
