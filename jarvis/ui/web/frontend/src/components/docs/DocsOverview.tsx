import { useMemo, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  ExternalLink,
  FileWarning,
  Loader2,
  RefreshCw,
  Search,
} from "lucide-react";

import {
  buildDocSections,
  docSectionLabel,
  useDocsGrouped,
  type DocSection,
  type DocNavSummary,
} from "@/hooks/useDocs";
import { useRecentDocs } from "@/hooks/useRecentDocs";
import { useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import { cn } from "@/lib/utils";
import { ONLINE_DOCS_URL, SearchTrigger, sectionIcon } from "./docsShared";

/** Links a topic card shows before it offers "Show all". */
const TOPIC_PREVIEW_COUNT = 4;

interface Props {
  onSelect: (slug: string) => void;
  onOpenSearch?: () => void;
}

/**
 * The docs home: a title with the search field, the first topic as a
 * numbered reading path, recently opened guides, and every other topic as a
 * card listing its guides.
 */
export function DocsOverview({ onSelect, onOpenSearch }: Props) {
  const t = useT();
  const { data, isLoading, isFetching, error, refetch } = useDocsGrouped();
  const { recent } = useRecentDocs();

  const sections = useMemo(() => buildDocSections(data), [data]);
  const sectionBySlug = useMemo(() => {
    const map = new Map<string, string>();
    for (const section of sections) {
      for (const doc of section.docs) map.set(doc.slug, section.name);
    }
    return map;
  }, [sections]);
  const [startSection, ...topics] = sections;
  const recentDocs = recent.filter((doc) => sectionBySlug.has(doc.slug)).slice(0, 3);

  return (
    <div className="mx-auto w-full max-w-page pb-20 pt-14">
      <header className="max-w-reading">
        <p className="text-sm font-medium text-accent">
          {t("docs_overview.eyebrow")}
        </p>
        <h1 className="mt-3 font-display text-2xl text-foreground-strong">
          {t("docs_overview.title")}
        </h1>
        <p className="mt-4 text-lg text-muted-foreground">
          {t("docs_overview.description")}
        </p>
        {onOpenSearch && (
          <SearchTrigger
            size="lg"
            onClick={onOpenSearch}
            className="mt-8 max-w-xl"
          />
        )}
      </header>

      {isLoading ? (
        <OverviewSkeleton />
      ) : error ? (
        <div className="mt-12 flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/[0.06] p-5">
          <FileWarning className="mt-0.5 h-5 w-5 shrink-0 text-destructive" aria-hidden="true" />
          <div>
            <h2 className="text-lg font-semibold text-foreground-strong">
              {t("docs_overview.load_failed_title")}
            </h2>
            <p className="mt-1 text-base text-muted-foreground">
              {t("docs_overview.load_failed_description")}
            </p>
            <button
              type="button"
              onClick={() => void refetch()}
              className="mt-4 inline-flex h-8 items-center gap-2 rounded-md border border-border-strong px-3 text-sm font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <RefreshCw
                className={cn(
                  "h-3.5 w-3.5",
                  isFetching && "animate-spin motion-reduce:animate-none",
                )}
                aria-hidden="true"
              />
              {t("docs_overview.retry")}
            </button>
          </div>
        </div>
      ) : !startSection ? (
        <div className="mt-12 rounded-lg border border-border p-10 text-center">
          <BookOpen className="mx-auto h-6 w-6 text-foreground-faint" aria-hidden="true" />
          <h2 className="mt-3 text-lg font-semibold text-foreground-strong">
            {t("docs_overview.empty_title")}
          </h2>
          <p className="mt-1 text-base text-muted-foreground">
            {t("docs_overview.empty_description")}
          </p>
        </div>
      ) : (
        <>
          <StartPath section={startSection} onSelect={onSelect} />

          {recentDocs.length > 0 && (
            <section className="mt-14" aria-labelledby="docs-recent-title">
              <h2
                id="docs-recent-title"
                className="text-sm font-semibold text-foreground-strong"
              >
                {t("docs_overview.recent_title")}
              </h2>
              <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {recentDocs.map((doc) => (
                  <button
                    key={doc.slug}
                    type="button"
                    onClick={() => onSelect(doc.slug)}
                    className="group flex items-center justify-between gap-3 rounded-md border border-border px-4 py-3 text-left transition-colors hover:border-border-strong hover:bg-secondary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-base font-medium text-foreground">
                        {doc.title}
                      </span>
                      <span className="mt-0.5 block truncate text-sm text-muted-foreground">
                        {docSectionLabel(sectionBySlug.get(doc.slug) ?? "")}
                      </span>
                    </span>
                    <ArrowRight
                      className="h-3.5 w-3.5 shrink-0 text-foreground-faint transition-colors group-hover:text-foreground"
                      aria-hidden="true"
                    />
                  </button>
                ))}
              </div>
            </section>
          )}

          {topics.length > 0 && (
            <section className="mt-16" aria-labelledby="docs-topics-title">
              <h2
                id="docs-topics-title"
                className="text-xl font-semibold text-foreground-strong"
              >
                {t("docs_overview.browse_by_topic")}
              </h2>
              <p className="mt-1.5 text-base text-muted-foreground">
                {t("docs_overview.browse_description")}
              </p>
              <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {topics.map((section) => (
                  <TopicCard key={section.name} section={section} onSelect={onSelect} />
                ))}
              </div>
            </section>
          )}

          <section className="mt-16 flex flex-col gap-5 border-t border-border pt-8 md:flex-row md:items-center md:justify-between">
            <div>
              <h2 className="text-lg font-semibold text-foreground-strong">
                {t("docs_overview.help_title")}
              </h2>
              <p className="mt-1 text-base text-muted-foreground">
                {t("docs_overview.help_description")}
              </p>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              {onOpenSearch && (
                <button
                  type="button"
                  onClick={onOpenSearch}
                  className="inline-flex h-9 items-center gap-2 rounded-md border border-border-strong px-4 text-base font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Search className="h-3.5 w-3.5" aria-hidden="true" />
                  {t("docs.search_button")}
                </button>
              )}
              <a
                href={ONLINE_DOCS_URL}
                onClick={(event) => {
                  event.preventDefault();
                  void openExternalUrl(ONLINE_DOCS_URL);
                }}
                rel="noopener noreferrer"
                className="inline-flex h-9 items-center gap-2 rounded-md border border-border-strong px-4 text-base font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {t("docs_overview.online_docs")}
                <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              </a>
            </div>
          </section>
        </>
      )}
    </div>
  );
}

/** The first topic, drawn as an ordered reading path. */
function StartPath({
  section,
  onSelect,
}: {
  section: DocSection;
  onSelect: (slug: string) => void;
}) {
  const t = useT();
  const Icon = sectionIcon(section.name);
  const first = section.docs[0];
  return (
    <section
      className="mt-14 grid overflow-hidden rounded-lg border border-border bg-card lg:grid-cols-5"
      aria-labelledby="docs-start-title"
    >
      <div className="flex flex-col border-b border-border p-7 lg:col-span-2 lg:border-b-0 lg:border-r">
        <span className="flex h-9 w-9 items-center justify-center rounded-md bg-accent-soft text-accent">
          <Icon className="h-4 w-4" aria-hidden="true" />
        </span>
        <h2
          id="docs-start-title"
          className="mt-5 text-xl font-semibold text-foreground-strong"
        >
          {t("docs_overview.start_title")}
        </h2>
        <p className="mt-2 text-base text-muted-foreground">
          {t("docs_overview.start_description")}
        </p>
        {first && (
          <button
            type="button"
            onClick={() => onSelect(first.slug)}
            className="mt-6 inline-flex h-9 w-fit items-center gap-2 rounded-md bg-primary px-4 text-base font-medium text-primary-foreground transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card"
          >
            {t("docs_overview.start_reading")}
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        )}
      </div>
      <ol className="p-3 lg:col-span-3">
        {section.docs.map((doc, index) => (
          <li key={doc.slug}>
            <button
              type="button"
              onClick={() => onSelect(doc.slug)}
              className="group flex w-full items-start gap-4 rounded-md px-4 py-3 text-left transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="mt-px flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border-strong text-xs font-medium tabular-nums text-muted-foreground transition-colors group-hover:border-accent group-hover:text-accent">
                {index + 1}
              </span>
              <span className="min-w-0">
                <span className="block text-base font-medium text-foreground">
                  {doc.title}
                </span>
                <span className="mt-0.5 line-clamp-1 text-sm text-muted-foreground">
                  {doc.summary}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}

function TopicCard({
  section,
  onSelect,
}: {
  section: DocSection;
  onSelect: (slug: string) => void;
}) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const Icon = sectionIcon(section.name);
  const visible: DocNavSummary[] = expanded
    ? section.docs
    : section.docs.slice(0, TOPIC_PREVIEW_COUNT);
  const hidden = section.docs.length - TOPIC_PREVIEW_COUNT;
  return (
    <section className="flex flex-col rounded-lg border border-border bg-card p-5">
      <div className="flex items-center gap-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-secondary text-foreground-secondary">
          <Icon className="h-4 w-4" aria-hidden="true" />
        </span>
        <h3 className="text-lg font-semibold text-foreground-strong">
          {docSectionLabel(section.name)}
        </h3>
      </div>
      <ul className="mt-4 flex-1 space-y-px">
        {visible.map((doc) => (
          <li key={doc.slug}>
            <button
              type="button"
              onClick={() => onSelect(doc.slug)}
              title={doc.summary}
              className="group flex w-full items-center justify-between gap-3 rounded-sm py-1.5 text-left text-base text-foreground-secondary transition-colors hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="min-w-0 truncate">{doc.title}</span>
              <ArrowRight
                className="h-3.5 w-3.5 shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
                aria-hidden="true"
              />
            </button>
          </li>
        ))}
      </ul>
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          className="mt-3 w-fit rounded-sm text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {expanded
            ? t("docs_overview.show_less")
            : t("docs_overview.show_all").replace("{0}", String(section.docs.length))}
        </button>
      )}
    </section>
  );
}

function OverviewSkeleton() {
  const t = useT();
  return (
    <div className="mt-14" role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2
          className="h-3.5 w-3.5 animate-spin text-accent motion-reduce:animate-none"
          aria-hidden="true"
        />
        <span className="font-medium text-foreground">
          {t("docs_overview.indexing_title")}
        </span>
        <span className="hidden sm:inline">— {t("docs_overview.indexing_description")}</span>
      </div>
      <div className="mt-6 animate-pulse motion-reduce:animate-none" aria-hidden="true">
        <div className="h-56 rounded-lg border border-border bg-card" />
        <div className="mt-14 h-4 w-40 rounded-full bg-muted" />
        <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((item) => (
            <div key={item} className="h-48 rounded-lg border border-border bg-card p-5">
              <div className="h-3 w-1/2 rounded-full bg-muted" />
              <div className="mt-6 space-y-3">
                <div className="h-2.5 w-4/5 rounded-full bg-muted/70" />
                <div className="h-2.5 w-3/5 rounded-full bg-muted/70" />
                <div className="h-2.5 w-2/3 rounded-full bg-muted/70" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
