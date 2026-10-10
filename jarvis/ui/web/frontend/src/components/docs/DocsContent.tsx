import { useEffect, useMemo, useRef, useState } from "react";
import type { Components } from "react-markdown";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Copy,
  ExternalLink,
  FileWarning,
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
import { docSourceUrl, readingMinutes } from "./docsShared";
import { useT, useUiLanguage } from "@/i18n";
import { localeForUiLanguage } from "@/components/runs/format";
import { openExternalUrl } from "@/lib/openExternal";
import { robustCopy } from "@/lib/clipboard";

interface Props {
  slug: string | null;
  onSelect: (slug: string) => void;
  onShowOverview: () => void;
  onOpenSearch?: () => void;
}

export function DocsContent({ slug, onSelect, onShowOverview, onOpenSearch }: Props) {
  if (!slug) {
    return <DocsOverview onSelect={onSelect} onOpenSearch={onOpenSearch} />;
  }
  return (
    <DocsContentInner
      slug={slug}
      onSelect={onSelect}
      onShowOverview={onShowOverview}
    />
  );
}

/*
 * Reading typography for a guide. The typography plugin's colour variables
 * are pointed at theme tokens (identical for its normal and inverted
 * palettes), so one class list reads correctly in light and dark, and the
 * heading sizes are pinned to the app's own type scale.
 */
const ARTICLE_PROSE = [
  "docs-prose prose max-w-none",
  "prose-p:text-pretty prose-li:my-1 prose-li:marker:text-foreground-faint",
  "prose-headings:scroll-mt-8 prose-headings:text-pretty prose-headings:font-semibold prose-headings:text-foreground-strong",
  "prose-h2:mb-4 prose-h2:mt-12 prose-h2:text-xl",
  "prose-h3:mb-3 prose-h3:mt-8 prose-h3:text-lg",
  "prose-h4:text-base",
  "prose-a:font-normal prose-a:text-accent prose-a:underline prose-a:decoration-accent/30 prose-a:underline-offset-4 hover:prose-a:decoration-accent",
  "prose-strong:font-semibold prose-strong:text-foreground-strong",
  "prose-code:rounded-sm prose-code:bg-secondary prose-code:px-1.5 prose-code:py-0.5 prose-code:font-mono prose-code:text-sm prose-code:font-normal prose-code:text-foreground-strong prose-code:before:hidden prose-code:after:hidden",
  "prose-hr:my-10 prose-hr:border-border",
  "prose-img:rounded-lg prose-img:border prose-img:border-border",
].join(" ");

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
  const minutes = useMemo(() => readingMinutes(data?.body ?? ""), [data?.body]);
  const markdownComponents = useMemo(
    () => makeMarkdownComponents({ knownSlugs, onInternalNavigate: onSelect }),
    [knownSlugs, onSelect],
  );

  if (isLoading) {
    return <DocPageSkeleton />;
  }
  if (error || !data) {
    return (
      <div className="flex min-h-full items-center justify-center px-8 py-20 text-center">
        <div className="max-w-sm">
          <FileWarning className="mx-auto h-6 w-6 text-destructive" aria-hidden="true" />
          <p className="mt-3 text-lg font-semibold text-foreground-strong">
            {t("docs.could_not_load")}
          </p>
          <div className="mt-5 flex justify-center gap-2">
            <button
              type="button"
              onClick={() => void refetch()}
              className="inline-flex h-8 items-center gap-2 rounded-md border border-border-strong px-3 text-sm font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
            <button
              type="button"
              onClick={onShowOverview}
              className="inline-flex h-8 items-center rounded-md px-3 text-sm font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {t("docs_sidebar.overview")}
            </button>
          </div>
        </div>
      </div>
    );
  }

  const markdownPage = `# ${data.title}\n\n${data.body}`;

  return (
    <article className="mx-auto w-full max-w-reading pb-20 pt-12">
      <header className="mb-10 border-b border-border pb-8">
        <nav aria-label={t("docs.breadcrumb")} className="flex items-center gap-1.5 text-sm">
          <button
            type="button"
            onClick={onShowOverview}
            className="rounded-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {t("docs_content.breadcrumb")}
          </button>
          <span className="text-foreground-faint" aria-hidden="true">/</span>
          <span className="font-medium text-accent" aria-current="page">
            {data.section}
          </span>
        </nav>
        <h1 className="mt-3 text-pretty font-display text-2xl text-foreground-strong">
          {data.title}
        </h1>
        {data.summary && (
          <p className="mt-3 text-pretty text-lg text-muted-foreground">
            {data.summary}
          </p>
        )}
        <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
          <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
            <span>{t("docs_content.reading_time").replace("{0}", String(minutes))}</span>
            {data.last_reviewed && (
              <>
                <span className="text-foreground-faint" aria-hidden="true">·</span>
                <span>{t("docs_content.reviewed").replace("{0}", reviewedDate)}</span>
              </>
            )}
          </p>
          <div className="flex items-center gap-1.5">
            <CopyPageButton markdown={markdownPage} />
            {data.path && (
              <a
                href={docSourceUrl(data.path)}
                onClick={(event) => {
                  event.preventDefault();
                  void openExternalUrl(docSourceUrl(data.path));
                }}
                rel="noopener noreferrer"
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-2.5 text-sm font-medium text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {t("docs_content.view_source")}
                <ExternalLink className="h-3 w-3" aria-hidden="true" />
              </a>
            )}
          </div>
        </div>
      </header>

      <div className={ARTICLE_PROSE}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[
            rehypeSlug,
            [rehypeAutolinkHeadings, { behavior: "wrap" }],
          ]}
          components={markdownComponents}
        >
          {data.body}
        </ReactMarkdown>
      </div>

      {relatedDocs.length > 0 && (
        <RelatedGuides docs={relatedDocs} onSelect={onSelect} />
      )}

      {(neighbors.prev || neighbors.next) && (
        <nav
          aria-label={t("docs_content.pagination")}
          className="mt-14 grid grid-cols-1 gap-3 border-t border-border pt-8 sm:grid-cols-2"
        >
          <NavCard doc={neighbors.prev} direction="prev" onSelect={onSelect} />
          <NavCard doc={neighbors.next} direction="next" onSelect={onSelect} />
        </nav>
      )}
    </article>
  );
}

function CopyPageButton({ markdown }: { markdown: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );
  return (
    <button
      type="button"
      onClick={() => {
        void robustCopy(markdown).then((ok) => {
          if (!ok) return;
          setCopied(true);
          if (timer.current !== null) window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-2.5 text-sm font-medium text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-success" aria-hidden="true" />
      ) : (
        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
      )}
      <span aria-live="polite">
        {copied ? t("docs_content.page_copied") : t("docs_content.copy_page")}
      </span>
    </button>
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
  if (!doc) return <div className="hidden sm:block" />;
  const next = direction === "next";
  return (
    <button
      type="button"
      onClick={() => onSelect(doc.slug)}
      className={`group flex flex-col gap-1 rounded-lg border border-border px-5 py-4 transition-colors hover:border-border-strong hover:bg-secondary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
        next ? "items-end text-right sm:col-start-2" : "items-start text-left"
      }`}
    >
      <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
        {!next && (
          <ArrowLeft
            className="h-3.5 w-3.5 transition-transform motion-safe:group-hover:-translate-x-0.5"
            aria-hidden="true"
          />
        )}
        {next ? t("docs_content.next") : t("docs_content.prev")}
        {next && (
          <ArrowRight
            className="h-3.5 w-3.5 transition-transform motion-safe:group-hover:translate-x-0.5"
            aria-hidden="true"
          />
        )}
      </span>
      <span className="text-lg font-medium text-foreground-strong">{doc.title}</span>
    </button>
  );
}

function formatReviewDate(value: string | null | undefined, locale: string): string {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(localeForUiLanguage(locale), {
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
    <section className="mt-14" aria-labelledby="related-guides-title">
      <h2
        id="related-guides-title"
        className="text-sm font-semibold text-foreground-strong"
      >
        {t("docs_content.related_guides")}
      </h2>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {docs.map((doc) => (
          <button
            key={doc.slug}
            type="button"
            onClick={() => onSelect(doc.slug)}
            className="group rounded-lg border border-border px-4 py-3.5 text-left transition-colors hover:border-border-strong hover:bg-secondary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span className="flex items-center justify-between gap-3 text-base font-medium text-foreground">
              {doc.title}
              <ArrowRight
                className="h-3.5 w-3.5 shrink-0 text-foreground-faint transition-colors group-hover:text-foreground"
                aria-hidden="true"
              />
            </span>
            <span className="mt-1 line-clamp-2 text-sm text-muted-foreground">
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
    <div className="mx-auto w-full max-w-reading pb-20 pt-12" role="status">
      <span className="sr-only">{t("docs_overview.loading_page")}</span>
      <div className="animate-pulse motion-reduce:animate-none" aria-hidden="true">
        <div className="h-3 w-40 rounded-full bg-muted" />
        <div className="mt-5 h-7 w-3/5 rounded-md bg-muted" />
        <div className="mt-4 h-3.5 w-4/5 rounded-full bg-muted/70" />
        <div className="mt-8 h-px bg-border" />
        <div className="mt-10 space-y-3.5">
          <div className="h-3 w-full rounded-full bg-muted/70" />
          <div className="h-3 w-11/12 rounded-full bg-muted/70" />
          <div className="h-3 w-4/5 rounded-full bg-muted/70" />
        </div>
        <div className="mt-12 h-4 w-2/5 rounded-full bg-muted" />
        <div className="mt-5 space-y-3.5">
          <div className="h-3 w-full rounded-full bg-muted/70" />
          <div className="h-3 w-5/6 rounded-full bg-muted/70" />
        </div>
        <div className="mt-6 h-28 rounded-lg border border-border bg-card" />
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
        <blockquote className="border-l-2 border-border-strong pl-4 not-italic text-foreground-secondary [&_p:before]:content-none [&_p:after]:content-none">
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

    // Tables sit in their own framed, horizontally scrollable box; cells are
    // styled here because the frame opts out of the prose rules.
    table({ children }) {
      return (
        <div className="not-prose my-6 overflow-x-auto rounded-lg border border-border">
          <table className="w-full border-collapse text-left text-base">{children}</table>
        </div>
      );
    },
    thead({ children }) {
      return <thead className="bg-secondary/50">{children}</thead>;
    },
    th({ children, style }) {
      return (
        <th
          style={style}
          className="border-b border-border px-4 py-2.5 text-sm font-semibold text-foreground-strong"
        >
          {children}
        </th>
      );
    },
    td({ children, style }) {
      return (
        <td
          style={style}
          className="border-t border-border px-4 py-2.5 align-top text-foreground-secondary first:font-medium first:text-foreground [&_code]:rounded-sm [&_code]:bg-secondary [&_code]:px-1 [&_code]:font-mono [&_code]:text-sm"
        >
          {children}
        </td>
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
