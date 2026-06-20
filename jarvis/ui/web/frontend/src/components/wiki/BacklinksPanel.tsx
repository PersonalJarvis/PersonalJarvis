/**
 * Right-column panel: pages that link back to the currently open page.
 *
 * Reads `/api/wiki/backlinks/{slug}` and renders each backlink as a clickable
 * card. Clicking a backlink invokes `onSelect(targetSlug)` so the caller
 * can switch the page view.
 */
import { useQuery } from "@tanstack/react-query";
import { FileText } from "lucide-react";

import { useT } from "@/i18n";
import { fetchWikiBacklinks } from "@/lib/wikiApi";

interface BacklinksPanelProps {
  slug: string;
  onSelect: (targetSlug: string) => void;
}

export function BacklinksPanel({ slug, onSelect }: BacklinksPanelProps) {
  const t = useT();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["wiki", "backlinks", slug],
    queryFn: () => fetchWikiBacklinks(slug),
    staleTime: 5_000,
    enabled: Boolean(slug),
  });

  const backlinks = data?.backlinks ?? [];

  return (
    <aside
      className="flex h-full w-[380px] shrink-0 flex-col border-l border-border bg-card/40 overflow-y-auto p-4"
      data-testid="wiki-backlinks-panel"
    >
      <BacklinksCard title="Backlinks">
        {isLoading && <BacklinksSkeleton />}
        {isError && (
          <div
            role="alert"
            className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
          >
            {t("backlinks_panel.load_error")}
          </div>
        )}
        {!isLoading && !isError && backlinks.length === 0 && (
          <p className="text-xs text-muted-foreground" data-testid="wiki-backlinks-empty">
            {t("backlinks_panel.empty")}
          </p>
        )}
        {!isLoading && !isError &&
          backlinks.map((bl) => (
            <button
              type="button"
              key={bl.slug}
              onClick={() => onSelect(bl.slug)}
              className="flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-secondary/60 hover:text-foreground"
              data-testid="wiki-backlink-item"
              data-target-slug={bl.slug}
            >
              <FileText className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-foreground">{bl.title || bl.slug}</div>
                {bl.snippet && (
                  <div className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
                    {bl.snippet}
                  </div>
                )}
              </div>
            </button>
          ))}
      </BacklinksCard>
    </aside>
  );
}

function BacklinksCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-3 rounded-lg border border-border bg-secondary/30 p-4">
      <h3 className="mb-3 text-[11px] font-bold uppercase tracking-widest text-muted-foreground">
        {title}
      </h3>
      <div className="space-y-1">{children}</div>
    </section>
  );
}

function BacklinksSkeleton() {
  return (
    <div className="space-y-2" data-testid="wiki-backlinks-skeleton">
      <div className="h-4 w-3/4 animate-pulse rounded bg-muted" />
      <div className="h-3 w-1/2 animate-pulse rounded bg-muted" />
    </div>
  );
}
