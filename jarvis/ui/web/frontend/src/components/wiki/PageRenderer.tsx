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
import { useT } from "@/i18n";

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
  const t = useT();
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
        <p role="alert" className="text-body text-destructive">
          {t("page_renderer.load_error")}
        </p>
      </div>
    );
  }

  const page = pageQuery.data;
  if (!page || !page.ok) {
    return (
      <div className="px-7 py-6" data-testid="wiki-page-error">
        <p role="alert" className="text-body text-destructive">
          {page?.error ?? t("page_renderer.not_found")}
        </p>
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
        vaultRoot={treeQuery.data?.vault_root ?? ""}
        vaultRelPath={vaultRelPath}
      />

      {/* A wiki page is a document, so it is set at the reading step in full
          ink and bounded to the reading measure. It used to run every
          paragraph, list and heading at muted ink under `prose-invert` — a
          dark-only class — so a page read as text somebody had disabled, and
          light mode got dark-mode typography. */}
      <div
        className="max-w-reading px-9 py-7 text-reading text-foreground"
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
              <h2 className="mb-3 mt-group text-title font-semibold text-foreground-strong">
                {children}
              </h2>
            ),
            h2: ({ children }) => (
              <h2 className="mb-3 mt-group text-title font-semibold text-foreground-strong">
                {children}
              </h2>
            ),
            h3: ({ children }) => (
              <h3 className="mb-2 mt-block text-title font-semibold text-foreground-strong">
                {children}
              </h3>
            ),
            p: ({ children }) => <p className="my-stack text-foreground">{children}</p>,
            ul: ({ children }) => (
              <ul className="my-stack list-disc pl-5 text-foreground">{children}</ul>
            ),
            li: ({ children }) => <li className="my-0.5">{children}</li>,
            code: ({ children, ...rest }) => (
              <code
                {...rest}
                className="rounded-sm bg-secondary px-1 py-0.5 font-mono text-meta text-foreground"
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
 * the last path segment (so `entities/harald` → `harald`).
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
    <div
      className="max-w-reading space-y-stack px-9 py-7"
      data-testid="wiki-page-skeleton"
      role="status"
      aria-busy="true"
    >
      <div className="h-3 w-32 animate-pulse rounded-full bg-sheen/[0.06]" />
      <div className="h-6 w-64 animate-pulse rounded-md bg-sheen/[0.06]" />
      <div className="flex gap-2">
        <div className="h-4 w-20 animate-pulse rounded-full bg-sheen/[0.06]" />
        <div className="h-4 w-24 animate-pulse rounded-full bg-sheen/[0.06]" />
      </div>
      <div className="mt-group space-y-stack">
        <div className="h-3 w-full animate-pulse rounded-full bg-sheen/[0.06]" />
        <div className="h-3 w-5/6 animate-pulse rounded-full bg-sheen/[0.06]" />
        <div className="h-3 w-4/6 animate-pulse rounded-full bg-sheen/[0.06]" />
      </div>
    </div>
  );
}

export { WIKILINK_PREFIX };
