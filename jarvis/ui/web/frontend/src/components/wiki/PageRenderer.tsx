/**
 * Render a single wiki page: header (breadcrumb + pills + Obsidian button)
 * plus the markdown body with clickable `[[wikilinks]]`.
 *
 * Wikilink handling: the body markdown is pre-processed before being passed
 * to `react-markdown`. Each `[[X]]`, `[[entities/X]]`, or `[[X|label]]` is
 * rewritten to a regular markdown link with href `#wiki:<slug>`. The
 * `components.a` override of `react-markdown` then intercepts these,
 * rendering a custom `<a>` element that calls `onWikilinkClick(slug)`
 * instead of navigating.
 *
 * Broken wikilinks (target slug not in the cached tree) get the `.broken`
 * class and trigger a toast when clicked.
 */
import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";

import {
  fetchWikiPage,
  fetchWikiTree,
  type WikiKind,
  type WikiTreeResponse,
} from "@/lib/wikiApi";
import { cn } from "@/lib/utils";

import { PageHeader } from "./PageHeader";

interface PageRendererProps {
  slug: string;
  onWikilinkClick: (targetSlug: string) => void;
}

const WIKILINK_PREFIX = "#wiki:";

// Regex covers `[[slug]]`, `[[entities/slug]]`, `[[slug|label]]`,
// `[[entities/slug|label]]`. Slugs are kebab-case, optionally folder-prefixed.
const WIKILINK_RE = /\[\[([^\]|\n]+)(?:\|([^\]\n]+))?\]\]/g;

export function PageRenderer({ slug, onWikilinkClick }: PageRendererProps) {
  const qc = useQueryClient();
  const pageQuery = useQuery({
    queryKey: ["wiki", "page", slug],
    queryFn: () => fetchWikiPage(slug),
    staleTime: 5_000,
  });

  // Cached tree response — used to determine whether a wikilink resolves
  // to an existing page or is broken.
  const treeQuery = useQuery({
    queryKey: ["wiki", "tree"],
    queryFn: fetchWikiTree,
    staleTime: 5_000,
  });

  const knownSlugs = useMemo(
    () => buildKnownSlugSet(treeQuery.data),
    [treeQuery.data],
  );

  const preprocessedBody = useMemo(() => {
    const raw = pageQuery.data?.body_md ?? "";
    return preprocessWikilinks(raw);
  }, [pageQuery.data?.body_md]);

  if (pageQuery.isLoading) {
    return <PageSkeleton />;
  }

  if (pageQuery.isError) {
    return (
      <div className="px-7 py-6" data-testid="wiki-page-error">
        <div
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          Seite konnte nicht geladen werden.
        </div>
      </div>
    );
  }

  const page = pageQuery.data;
  if (!page || !page.ok) {
    return (
      <div className="px-7 py-6" data-testid="wiki-page-error">
        <div
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          {page?.error ?? "Seite nicht gefunden."}
        </div>
      </div>
    );
  }

  const kind = (page.kind ?? "entity") as WikiKind;
  const vaultRelPath = page.path ?? `${kind}/${slug}.md`;
  const title = page.title ?? slug;
  const frontmatter = page.frontmatter ?? {};

  const handleClick = (target: string) => {
    if (knownSlugs.size > 0 && !knownSlugs.has(target)) {
      // Force a re-fetch of the tree in case the cache is stale, then surface
      // the missing page so the caller can show a toast.
      qc.invalidateQueries({ queryKey: ["wiki", "tree"] });
    }
    onWikilinkClick(target);
  };

  return (
    <article className="flex flex-col" data-testid="wiki-page-renderer">
      <PageHeader
        slug={slug}
        kind={kind}
        title={title}
        frontmatter={frontmatter}
        vaultRelPath={vaultRelPath}
      />

      <div
        className="prose prose-invert max-w-[820px] px-9 py-7 text-sm"
        data-testid="wiki-page-body"
      >
        <ReactMarkdown
          components={{
            a: ({ href, children, ...rest }) => {
              if (typeof href === "string" && href.startsWith(WIKILINK_PREFIX)) {
                const target = href.slice(WIKILINK_PREFIX.length);
                const isBroken =
                  knownSlugs.size > 0 && !knownSlugs.has(target);
                return (
                  <a
                    {...rest}
                    href={href}
                    data-target-slug={target}
                    className={cn("wikilink", isBroken && "broken")}
                    onClick={(e) => {
                      e.preventDefault();
                      handleClick(target);
                    }}
                  >
                    {children}
                  </a>
                );
              }
              return (
                <a {...rest} href={href} target="_blank" rel="noopener noreferrer">
                  {children}
                </a>
              );
            },
            h1: ({ children }) => (
              <h2 className="mb-3 mt-7 border-b border-border pb-2 text-[13px] font-semibold uppercase tracking-wider text-muted-foreground">
                {children}
              </h2>
            ),
            h2: ({ children }) => (
              <h2 className="mb-3 mt-7 border-b border-border pb-2 text-[13px] font-semibold uppercase tracking-wider text-muted-foreground">
                {children}
              </h2>
            ),
            h3: ({ children }) => (
              <h3 className="mb-2 mt-5 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                {children}
              </h3>
            ),
            p: ({ children }) => (
              <p className="my-2 text-muted-foreground">{children}</p>
            ),
            ul: ({ children }) => (
              <ul className="my-2 list-disc pl-5 text-muted-foreground">{children}</ul>
            ),
            li: ({ children }) => <li className="my-0.5">{children}</li>,
            code: ({ children, ...rest }) => (
              <code
                {...rest}
                className="rounded bg-background px-1 py-0.5 font-mono text-[11px] text-muted-foreground"
              >
                {children}
              </code>
            ),
          }}
        >
          {preprocessedBody}
        </ReactMarkdown>
      </div>
    </article>
  );
}

/**
 * Convert `[[slug]]`, `[[entities/slug]]`, `[[slug|label]]` markers into
 * regular markdown links pointing at `#wiki:<slug>`. The slug component is
 * the last path segment (so `entities/sam` → `sam`).
 */
export function preprocessWikilinks(body: string): string {
  return body.replace(WIKILINK_RE, (_match, target: string, label?: string) => {
    const slug = lastSegment(target.trim());
    const text = label ? label.trim() : slug;
    // Escape only `]` to avoid breaking the surrounding markdown.
    const safeText = text.replace(/]/g, "\\]");
    return `[${safeText}](${WIKILINK_PREFIX}${slug})`;
  });
}

function lastSegment(target: string): string {
  const idx = Math.max(target.lastIndexOf("/"), target.lastIndexOf("\\"));
  return idx >= 0 ? target.slice(idx + 1) : target;
}

function buildKnownSlugSet(tree: WikiTreeResponse | undefined): Set<string> {
  const out = new Set<string>();
  if (!tree?.folders) return out;
  for (const folder of tree.folders) {
    for (const file of folder.files) {
      out.add(file.slug);
    }
  }
  return out;
}

function PageSkeleton() {
  return (
    <div className="space-y-3 px-7 py-6" data-testid="wiki-page-skeleton">
      <div className="h-3 w-32 animate-pulse rounded bg-muted" />
      <div className="h-6 w-64 animate-pulse rounded bg-muted" />
      <div className="flex gap-2">
        <div className="h-4 w-20 animate-pulse rounded bg-muted" />
        <div className="h-4 w-24 animate-pulse rounded bg-muted" />
      </div>
      <div className="mt-6 space-y-2">
        <div className="h-3 w-full animate-pulse rounded bg-muted" />
        <div className="h-3 w-5/6 animate-pulse rounded bg-muted" />
        <div className="h-3 w-4/6 animate-pulse rounded bg-muted" />
      </div>
    </div>
  );
}

export { WIKILINK_PREFIX };
