import { useMemo } from "react";
import type { Components } from "react-markdown";
import {
  FileWarning,
  ChevronLeft,
  ChevronRight,
  Clock,
  Link2,
  RefreshCw,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";
import rehypeAutolinkHeadings from "rehype-autolink-headings";

import {
  useDocDetail,
  useDocsGrouped,
  buildDocSections,
  DIATAXIS_ORDER,
} from "@/hooks/useDocs";
import type { DocNavSummary } from "@/hooks/useDocs";
import { DocsOverview } from "./DocsOverview";
import { CodeBlock } from "./CodeBlock";
import { Callout, parseCalloutTag, type CalloutType } from "./Callout";
import { useT, useUiLanguage } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";

interface Props {
  slug: string | null;
  onSelect: (slug: string) => void;
  onShowOverview: () => void;
}

export function DocsContent({ slug, onSelect, onShowOverview }: Props) {
  if (!slug) {
    return <DocsOverview onSelect={onSelect} />;
  }
  return (
    <DocsContentInner
      slug={slug}
      onSelect={onSelect}
      onShowOverview={onShowOverview}
    />
  );
}

function DocsContentInner({
  slug,
  onSelect,
  onShowOverview,
}: {
  slug: string;
  onSelect: (slug: string) => void;
  onShowOverview: () => void;
}) {
  const t = useT();
  const uiLanguage = useUiLanguage();
  const { data, isLoading, isFetching, error, refetch } = useDocDetail(slug);
  const grouped = useDocsGrouped();
  const neighbors = computeNeighbors(grouped.data, slug);
  // Slug index for cross-link resolution: a Set for O(1) lookup in the
  // ``a`` renderer. If a Markdown link points to a known slug, the click
  // navigates internally instead of externally.
  const knownSlugs = useMemo<Set<string>>(() => {
    const slugs: string[] = [];
    for (const kind of DIATAXIS_ORDER) {
      for (const doc of grouped.data?.[kind] ?? []) slugs.push(doc.slug);
    }
    return new Set(slugs);
  }, [grouped.data]);
  const relatedDocs = useMemo(() => {
    const bySlug = new Map(
      buildDocSections(grouped.data)
        .flatMap((section) => section.docs)
        .map((doc) => [doc.slug, doc]),
    );
    return data?.related
      .map((relatedSlug) => bySlug.get(relatedSlug))
      .filter((doc): doc is DocNavSummary => doc !== undefined) ?? [];
  }, [data?.related, grouped.data]);
  const reviewedDate = useMemo(
    () => formatReviewDate(data?.last_reviewed, uiLanguage),
    [data?.last_reviewed, uiLanguage],
  );

  if (isLoading) {
    return <DocPageSkeleton />;
  }
  if (error || !data) {
    return (
      <div className="flex min-h-full items-center justify-center px-8 text-center">
        <div className="max-w-sm rounded-xl border border-destructive/30 bg-destructive/5 p-6">
          <FileWarning className="mx-auto h-6 w-6 text-destructive" aria-hidden="true" />
          <p className="mt-3 text-sm font-medium text-foreground">
            {t("docs.could_not_load")}
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
    );
  }

  return (
    <article className="prose prose-neutral dark:prose-invert prose-base mx-auto max-w-3xl px-8 py-10 prose-headings:scroll-mt-20 prose-headings:text-pretty prose-p:text-pretty prose-code:before:hidden prose-code:after:hidden prose-a:text-primary prose-a:no-underline hover:prose-a:underline lg:px-10">
      <header className="not-prose mb-9 border-b border-border pb-6">
        <nav
          aria-label="Breadcrumb"
          className="mb-4 flex items-center gap-1.5 text-xs text-muted-foreground"
        >
          <button
            type="button"
            onClick={onShowOverview}
            className="rounded-sm transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {t("docs_content.breadcrumb")}
          </button>
          <ChevronRight className="h-3 w-3" aria-hidden="true" />
          <span className="text-foreground/80" aria-current="page">
            {data.section}
          </span>
        </nav>
        <h1 className="m-0 text-pretty text-3xl font-semibold tracking-tight">
          {data.title}
        </h1>
        <p className="mt-3 max-w-2xl text-pretty text-base leading-7 text-muted-foreground">
          {data.summary}
        </p>
      </header>

      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[
          rehypeSlug,
          [rehypeAutolinkHeadings, { behavior: "wrap" }],
        ]}
        components={makeMarkdownComponents({
          knownSlugs,
          onInternalNavigate: onSelect,
        })}
      >
        {data.body}
      </ReactMarkdown>

      {relatedDocs.length > 0 && (
        <RelatedGuides docs={relatedDocs} onSelect={onSelect} />
      )}

      <footer className="not-prose mt-12 border-t border-border pt-6">
        <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <Clock className="h-3 w-3" aria-hidden="true" />
            {data.last_reviewed ? (
              <>{t("docs_content.last_reviewed")} {reviewedDate}</>
            ) : (
              <>{t("docs_content.review_unavailable")}</>
            )}
          </span>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <NavCard
            doc={neighbors.prev}
            direction="prev"
            onSelect={onSelect}
          />
          <NavCard
            doc={neighbors.next}
            direction="next"
            onSelect={onSelect}
          />
        </div>
      </footer>
    </article>
  );
}

// ----------------------------------------------------------------------
// Prev/next computation + NavCard
// ----------------------------------------------------------------------

function computeNeighbors(
  grouped: Partial<Record<string, DocNavSummary[]>> | undefined,
  currentSlug: string,
): { prev: DocNavSummary | null; next: DocNavSummary | null } {
  if (!grouped) return { prev: null, next: null };
  const flat = buildDocSections(grouped).flatMap((section) => section.docs);
  const idx = flat.findIndex((d) => d.slug === currentSlug);
  if (idx === -1) return { prev: null, next: null };
  return {
    prev: idx > 0 ? flat[idx - 1] : null,
    next: idx < flat.length - 1 ? flat[idx + 1] : null,
  };
}

function NavCard({
  doc,
  direction,
  onSelect,
}: {
  doc: DocNavSummary | null;
  direction: "prev" | "next";
  onSelect: (slug: string) => void;
}) {
  const t = useT();
  if (!doc) return <div />;
  const Icon = direction === "prev" ? ChevronLeft : ChevronRight;
  const align = direction === "next" ? "text-right items-end" : "items-start";
  return (
    <button
      type="button"
      onClick={() => onSelect(doc.slug)}
      className={`flex flex-col gap-1 rounded-md border border-border bg-card/40 p-3 text-left transition hover:bg-muted/40 ${align}`}
    >
      <span className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
        {direction === "prev" ? <Icon className="h-3 w-3" aria-hidden="true" /> : null}
        {direction === "prev" ? t("docs_content.prev") : t("docs_content.next")}
        {direction === "next" ? <Icon className="h-3 w-3" aria-hidden="true" /> : null}
      </span>
      <span className="text-sm font-medium">{doc.title}</span>
      <span className="text-xs leading-5 text-muted-foreground">{doc.summary}</span>
    </button>
  );
}

function formatReviewDate(value: string | null | undefined, locale: string): string {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  const localeName = locale === "de" ? "de-DE" : locale === "es" ? "es-ES" : "en-US";
  return new Intl.DateTimeFormat(localeName, {
    dateStyle: "medium",
    timeZone: "UTC",
  }).format(date);
}

function RelatedGuides({
  docs,
  onSelect,
}: {
  docs: DocNavSummary[];
  onSelect: (slug: string) => void;
}) {
  const t = useT();
  return (
    <section
      className="not-prose mt-12 border-t border-border pt-8"
      aria-labelledby="related-guides-title"
    >
      <div className="mb-4 flex items-center gap-2">
        <Link2 className="h-4 w-4 text-primary" aria-hidden="true" />
        <h2 id="related-guides-title" className="text-lg font-semibold">
          {t("docs_content.related_guides")}
        </h2>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {docs.map((doc) => (
          <button
            key={doc.slug}
            type="button"
            onClick={() => onSelect(doc.slug)}
            className="rounded-lg border border-border bg-card/30 p-4 text-left transition-colors hover:border-primary/30 hover:bg-card/60"
          >
            <span className="text-sm font-semibold text-foreground">{doc.title}</span>
            <span className="mt-1.5 block text-xs leading-5 text-muted-foreground">
              {doc.summary}
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

function DocPageSkeleton() {
  const t = useT();
  return (
    <div className="mx-auto min-h-full w-full max-w-3xl px-8 py-10" role="status">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <RefreshCw
          className="h-3.5 w-3.5 animate-spin text-primary motion-reduce:animate-none"
          aria-hidden="true"
        />
        {t("docs_overview.loading_page")}
      </div>
      <div className="mt-6 animate-pulse motion-reduce:animate-none">
        <div className="h-3 w-24 rounded-full bg-muted" />
        <div className="mt-4 h-8 w-3/5 rounded-md bg-muted" />
        <div className="mt-4 h-px bg-border" />
        <div className="mt-8 space-y-3">
          <div className="h-3 w-full rounded-full bg-muted/80" />
          <div className="h-3 w-11/12 rounded-full bg-muted/80" />
          <div className="h-3 w-4/5 rounded-full bg-muted/80" />
        </div>
        <div className="mt-10 h-5 w-2/5 rounded-full bg-muted" />
        <div className="mt-5 space-y-3">
          <div className="h-3 w-full rounded-full bg-muted/80" />
          <div className="h-3 w-5/6 rounded-full bg-muted/80" />
          <div className="h-28 rounded-lg border border-border bg-card/40" />
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Markdown custom components
// ----------------------------------------------------------------------

interface MarkdownContext {
  knownSlugs: Set<string>;
  onInternalNavigate?: (slug: string) => void;
}

/**
 * Resolves a Markdown link against the doc index.
 *
 * Match order:
 * 1. ``[text](slug-in-index)``                -> internal
 * 2. ``[text](docs/path/to/file.md)``         -> internal if a slug can be derived from the path
 * 3. ``[text](docs/adr/0011-...md)``          -> internal via ``adr-...`` slug
 * 4. http(s)://...                            -> external, new tab
 * 5. #anchor                                  -> anchor jump within the page
 * 6. anything else                            -> default anchor
 */
function resolveLink(
  href: string,
  knownSlugs: Set<string>,
): { kind: "internal"; slug: string } | { kind: "external" } | { kind: "anchor" } | { kind: "default" } {
  if (!href) return { kind: "default" };
  if (href.startsWith("#")) return { kind: "anchor" };
  if (href.startsWith("http://") || href.startsWith("https://")) {
    return { kind: "external" };
  }
  // Direct slug match
  if (knownSlugs.has(href)) return { kind: "internal", slug: href };
  // Path ending in .md — try the stem without the extension, plus a few
  // prefix variants ("docs/adr/0011-router.md" -> "adr-0011-router").
  const cleaned = href.replace(/^\.?\//, "").replace(/\.md$/, "");
  // 1. whole path as slug
  if (knownSlugs.has(cleaned)) return { kind: "internal", slug: cleaned };
  // 2. filename only
  const parts = cleaned.split("/");
  const lastSegment = parts[parts.length - 1];
  if (knownSlugs.has(lastSegment)) {
    return { kind: "internal", slug: lastSegment };
  }
  // 3. ADR convention: ``docs/adr/NNNN-...`` -> ``adr-NNNN-...``
  if (parts.length >= 2 && parts[parts.length - 2] === "adr") {
    const adrSlug = `adr-${lastSegment}`;
    if (knownSlugs.has(adrSlug)) return { kind: "internal", slug: adrSlug };
  }
  return { kind: "default" };
}

function makeMarkdownComponents(ctx: MarkdownContext): Components {
  return {
    // Fenced blocks render their own complete container. Unwrapping the
    // Markdown ``pre`` avoids invalid ``pre > div`` nesting around CodeBlock.
    pre({ children }) {
      return <>{children}</>;
    },

    // Code block (with language tag) becomes the Shiki component.
    code({ className, children, ...rest }) {
      const match = /language-(\w+)/.exec(className || "");
      if (!match) {
        return (
          <code className={className} {...rest}>
            {children}
          </code>
        );
      }
      return (
        <CodeBlock
          language={match[1]}
          code={String(children).replace(/\n$/, "")}
        />
      );
    },

    // Detect a blockquote with a GitHub-style callout tag.
    blockquote({ children }) {
      const tagged = parseTaggedBlockquote(children);
      if (tagged) {
        return <Callout type={tagged.type}>{tagged.children}</Callout>;
      }
      return (
        <blockquote className="border-l-2 border-border pl-4 italic text-muted-foreground">
          {children}
        </blockquote>
      );
    },

    // Cross-link resolution
    a({ href, children, ...rest }) {
      const resolved = href ? resolveLink(href, ctx.knownSlugs) : { kind: "default" as const };
      if (resolved.kind === "internal" && ctx.onInternalNavigate) {
        const slug = resolved.slug;
        return (
          <a
            href={`#${slug}`}
            onClick={(e) => {
              e.preventDefault();
              ctx.onInternalNavigate?.(slug);
            }}
            {...rest}
          >
            {children}
          </a>
        );
      }
      if (resolved.kind === "external") {
        return (
          <a
            href={href}
            onClick={(event) => {
              event.preventDefault();
              void openExternalUrl(href ?? "");
            }}
            rel="noopener noreferrer"
            {...rest}
          >
            {children}
          </a>
        );
      }
      // anchor + default
      return (
        <a href={href} {...rest}>
          {children}
        </a>
      );
    },

    // Tables with a bit more padding for better readability.
    table({ children }) {
      return (
        <div className="not-prose my-4 overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">{children}</table>
        </div>
      );
    },
  };
}

/**
 * Looks at the children of a blockquote: if the first node is a <p> with a
 * leading tag (``[!info]`` etc.), returns the type + the stripped children.
 * Otherwise null.
 */
function parseTaggedBlockquote(
  children: React.ReactNode,
): { type: CalloutType; children: React.ReactNode } | null {
  // children can be an array (with whitespace strings in between).
  const arr = Array.isArray(children) ? children : [children];
  for (const node of arr) {
    if (typeof node === "string") {
      if (!node.trim()) continue;
      const tag = parseCalloutTag(node);
      if (tag) {
        // The tag itself has no block wrap — we insert the rest directly
        // as text. An edge case, rare in practice.
        return { type: tag.type, children: tag.rest };
      }
      return null;
    }
    // Grab the first <p> element
    if (
      typeof node === "object" &&
      node !== null &&
      "type" in (node as object) &&
      (node as { type: unknown }).type === "p"
    ) {
      const pChildren = (node as { props: { children: React.ReactNode } })
        .props.children;
      const firstText =
        typeof pChildren === "string"
          ? pChildren
          : Array.isArray(pChildren) && typeof pChildren[0] === "string"
          ? (pChildren[0] as string)
          : null;
      if (firstText) {
        const tag = parseCalloutTag(firstText);
        if (tag) {
          // Re-mounted children: replace the first text with ``rest``, pass
          // other nodes through unchanged.
          if (typeof pChildren === "string") {
            return { type: tag.type, children: <p>{tag.rest}</p> };
          }
          const tail = (pChildren as React.ReactNode[]).slice(1);
          const newChildren = [
            tag.rest,
            ...tail,
          ] as React.ReactNode[];
          return {
            type: tag.type,
            children: (
              <>
                <p>{newChildren}</p>
                {arr.slice(arr.indexOf(node) + 1)}
              </>
            ),
          };
        }
      }
      return null;
    }
  }
  return null;
}
