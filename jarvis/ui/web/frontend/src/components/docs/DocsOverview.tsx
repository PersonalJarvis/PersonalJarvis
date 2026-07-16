import { useMemo } from "react";
import {
  ArrowRight,
  BookOpen,
  ExternalLink,
  FileWarning,
  Loader2,
  RefreshCw,
} from "lucide-react";

import {
  buildDocSections,
  useDocsGrouped,
  type DocSection,
  type DocNavSummary,
} from "@/hooks/useDocs";
import { useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";

const ONLINE_DOCS_URL = "https://personaljarvis.ai/docs/";
interface Props {
  onSelect: (slug: string) => void;
}

export function DocsOverview({ onSelect }: Props) {
  const t = useT();
  const { data, isLoading, isFetching, error, refetch } = useDocsGrouped();

  const sections = useMemo(() => buildDocSections(data), [data]);
  const allDocs = useMemo(
    () => sections.flatMap((section) => section.docs),
    [sections],
  );
  const featured = allDocs.slice(0, 4);

  return (
    <section className="mx-auto min-h-full w-full max-w-5xl px-8 py-10 lg:px-12">
      <div className="border-b border-border pb-8">
        <div className="mb-4 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.24em] text-primary">
          <BookOpen className="h-3.5 w-3.5" aria-hidden="true" />
          {t("docs_overview.eyebrow")}
        </div>
        <h1 className="text-3xl font-semibold tracking-tight text-foreground lg:text-4xl">
          {t("docs_overview.title")}
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">
          {t("docs_overview.description")}
        </p>
        <a
          href={ONLINE_DOCS_URL}
          onClick={(event) => {
            event.preventDefault();
            void openExternalUrl(ONLINE_DOCS_URL);
          }}
          rel="noopener noreferrer"
          className="mt-5 inline-flex items-center gap-2 rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-xs font-medium text-primary transition hover:border-primary/60 hover:bg-primary/15"
        >
          {t("docs_overview.online_docs")}
          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
        </a>
      </div>

      {isLoading ? (
        <OverviewSkeleton />
      ) : error ? (
        <div className="mt-8 rounded-xl border border-destructive/30 bg-destructive/5 p-6">
          <div className="flex items-start gap-3">
            <FileWarning className="mt-0.5 h-5 w-5 text-destructive" aria-hidden="true" />
            <div>
              <h2 className="text-sm font-semibold">
                {t("docs_overview.load_failed_title")}
              </h2>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {t("docs_overview.load_failed_description")}
              </p>
              <button
                type="button"
                onClick={() => void refetch()}
                className="mt-4 inline-flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-xs font-medium transition hover:bg-muted"
              >
                <RefreshCw
                  className={
                    isFetching
                      ? "h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
                      : "h-3.5 w-3.5"
                  }
                  aria-hidden="true"
                />
                {t("docs_overview.retry")}
              </button>
            </div>
          </div>
        </div>
      ) : featured.length === 0 ? (
        <div className="mt-8 rounded-xl border border-border bg-card/40 p-8 text-center">
          <BookOpen className="mx-auto h-8 w-8 text-muted-foreground/50" aria-hidden="true" />
          <h2 className="mt-3 text-sm font-semibold">
            {t("docs_overview.empty_title")}
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("docs_overview.empty_description")}
          </p>
        </div>
      ) : (
        <div className="mt-10">
          <div className="mb-5 flex items-end justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold">
                {t("docs_overview.local_library")}
              </h2>
              <p className="mt-1 text-xs text-muted-foreground">
                {t("docs_overview.local_library_description")}
              </p>
            </div>
            <span className="shrink-0 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {allDocs.length} {t("docs_sidebar.documents")}
            </span>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            {featured.map((doc) => (
              <DocCard key={doc.slug} doc={doc} onSelect={onSelect} />
            ))}
          </div>

          <div className="mt-12 border-t border-border pt-8">
            <h2 className="text-lg font-semibold">
              {t("docs_overview.browse_by_topic")}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("docs_overview.browse_description")}
            </p>
            <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {sections.map((section) => (
                <SectionCard key={section.name} section={section} onSelect={onSelect} />
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function DocCard({
  doc,
  onSelect,
}: {
  doc: DocNavSummary;
  onSelect: (slug: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(doc.slug)}
      className="group flex min-h-36 flex-col rounded-xl border border-border bg-card/30 p-5 text-left transition hover:border-primary/30 hover:bg-card/70 motion-safe:hover:-translate-y-0.5"
    >
      <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        {doc.section}
      </span>
      <span className="mt-2 flex items-center gap-2 text-sm font-semibold text-foreground group-hover:text-primary">
        {doc.title}
        <ArrowRight className="h-3.5 w-3.5 transition motion-safe:group-hover:translate-x-0.5" aria-hidden="true" />
      </span>
      <span className="mt-3 text-xs leading-5 text-muted-foreground">
        {doc.summary}
      </span>
    </button>
  );
}

function SectionCard({
  section,
  onSelect,
}: {
  section: DocSection;
  onSelect: (slug: string) => void;
}) {
  const t = useT();
  const first = section.docs[0];
  return (
    <button
      type="button"
      onClick={() => onSelect(first.slug)}
      className="group rounded-lg border border-border bg-card/20 p-4 text-left transition-colors hover:border-primary/30 hover:bg-card/60"
    >
      <span className="flex items-center justify-between gap-3 text-sm font-semibold">
        {section.name}
        <ArrowRight className="h-3.5 w-3.5 text-muted-foreground transition-transform motion-safe:group-hover:translate-x-0.5" aria-hidden="true" />
      </span>
      <span className="mt-2 block text-xs leading-5 text-muted-foreground">
        {first.summary}
      </span>
      <span className="mt-3 block text-[10px] uppercase tracking-wider text-muted-foreground/70">
        {section.docs.length}{" "}
        {section.docs.length === 1
          ? t("docs_overview.guide")
          : t("docs_overview.guides")}
      </span>
    </button>
  );
}

function OverviewSkeleton() {
  const t = useT();
  return (
    <div className="mt-8" role="status" aria-live="polite">
      <div className="flex items-start gap-3 rounded-xl border border-primary/20 bg-primary/5 p-4">
        <Loader2
          className="mt-0.5 h-4 w-4 animate-spin text-primary motion-reduce:animate-none"
          aria-hidden="true"
        />
        <div>
          <p className="text-sm font-medium">{t("docs_overview.indexing_title")}</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {t("docs_overview.indexing_description")}
          </p>
        </div>
      </div>
      <div className="mt-8 animate-pulse motion-reduce:animate-none">
        <div className="h-4 w-36 rounded-full bg-muted" />
        <div className="mt-2 h-3 w-72 max-w-full rounded-full bg-muted/70" />
        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          {[0, 1, 2, 3].map((item) => (
            <div key={item} className="h-28 rounded-xl border border-border bg-card/30 p-5">
              <div className="h-2 w-20 rounded-full bg-muted" />
              <div className="mt-4 h-3 w-3/5 rounded-full bg-muted" />
              <div className="mt-5 h-2 w-2/5 rounded-full bg-muted/60" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
