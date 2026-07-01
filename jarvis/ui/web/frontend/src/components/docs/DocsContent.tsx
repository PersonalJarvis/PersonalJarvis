import { useMemo } from "react";
import type { Components } from "react-markdown";
import {
  Loader2,
  FileWarning,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Clock,
  ExternalLink,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";
import rehypeAutolinkHeadings from "rehype-autolink-headings";

import {
  useDocDetail,
  useDocsGrouped,
  useDocsList,
  useOpenDocInEditor,
  DIATAXIS_ORDER,
} from "@/hooks/useDocs";
import type { DocSummary } from "@/hooks/useDocs";
import { DocTypeBadge } from "./DocTypeBadge";
import { CodeBlock } from "./CodeBlock";
import { Callout, parseCalloutTag, type CalloutType } from "./Callout";
import { useT } from "@/i18n";

interface Props {
  slug: string | null;
  onSelect?: (slug: string) => void;
}

export function DocsContent({ slug, onSelect }: Props) {
  const t = useT();
  if (!slug) {
    return (
      <div className="flex h-full items-center justify-center text-center">
        <div className="max-w-sm text-muted-foreground">
          <BookOpen className="mx-auto mb-3 h-10 w-10 opacity-40" />
          <p className="text-sm">
            Pick a doc from the sidebar or press{" "}
            <kbd className="rounded border border-border bg-muted px-1 text-xs">
              Ctrl+K
            </kbd>{" "}
            {t("docs.for_fulltext")}
          </p>
        </div>
      </div>
    );
  }
  return <DocsContentInner slug={slug} onSelect={onSelect} />;
}

function DocsContentInner({
  slug,
  onSelect,
}: {
  slug: string;
  onSelect?: (slug: string) => void;
}) {
  const t = useT();
  const { data, isLoading, error } = useDocDetail(slug);
  const grouped = useDocsGrouped();
  const list = useDocsList();
  const neighbors = computeNeighbors(grouped.data, slug);
  const openInEditor = useOpenDocInEditor();
  // Slug index for cross-link resolution: a Set for O(1) lookup in the
  // ``a`` renderer. If a Markdown link points to a known slug, the click
  // navigates internally instead of externally.
  const knownSlugs = useMemo<Set<string>>(() => {
    return new Set((list.data ?? []).map((d) => d.slug));
  }, [list.data]);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="flex h-full items-center justify-center text-destructive">
        <FileWarning className="mr-2 h-5 w-5" />
        {t("docs.could_not_load")}
      </div>
    );
  }

  return (
    <article className="prose prose-neutral dark:prose-invert prose-sm mx-auto max-w-2xl px-8 py-8 prose-headings:scroll-mt-20 prose-code:before:hidden prose-code:after:hidden prose-a:text-primary prose-a:no-underline hover:prose-a:underline">
      {/* Header — frontmatter snippet */}
      <header className="not-prose mb-8 border-b border-border pb-4">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <DocTypeBadge diataxis={data.diataxis} />
          {data.phase !== "-" && (
            <span className="rounded-md border border-border bg-muted/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
              Phase {data.phase}
            </span>
          )}
          {data.tags.map((tag) => (
            <span
              key={tag}
              className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
            >
              #{tag}
            </span>
          ))}
        </div>
        <h1 className="m-0 text-2xl font-semibold tracking-tight">
          {data.title}
        </h1>
        <p className="mt-1 text-xs text-muted-foreground">
          <span>Slug: {data.slug}</span>
          {data.last_reviewed && (
            <> · last reviewed: {data.last_reviewed}</>
          )}
          {data.error && (
            <span className="ml-2 text-destructive">· {data.error}</span>
          )}
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

      {/* Footer — last-updated + prev/next pager */}
      <footer className="not-prose mt-12 border-t border-border pt-6">
        <div className="mb-4 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <Clock className="h-3 w-3" />
            {data.last_reviewed ? (
              <>Last reviewed on {data.last_reviewed}</>
            ) : (
              <>No <code>last_reviewed</code> set</>
            )}
          </span>
          <span>·</span>
          <span>
            Owner:{" "}
            <span className="font-medium text-foreground/80">{data.owner}</span>
          </span>
          <button
            type="button"
            onClick={() => openInEditor.mutate(data.slug)}
            disabled={openInEditor.isPending}
            className="ml-auto inline-flex items-center gap-1 rounded-md border border-border bg-card/40 px-2 py-1 text-[11px] transition hover:bg-muted/40 disabled:opacity-50"
            title={t("common.open_in_editor")}
          >
            <ExternalLink className="h-3 w-3" />
            {openInEditor.isPending ? "opening…" : "Open in editor"}
          </button>
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
  grouped: Record<string, DocSummary[]> | undefined,
  currentSlug: string,
): { prev: DocSummary | null; next: DocSummary | null } {
  if (!grouped) return { prev: null, next: null };
  // We flatten the sidebar order: Diataxis order, alphabetical within each
  // group (matching how the backend's ``grouped_by_diataxis`` sorts).
  const flat: DocSummary[] = [];
  for (const kind of DIATAXIS_ORDER) {
    const items = grouped[kind];
    if (items) flat.push(...items);
  }
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
  doc: DocSummary | null;
  direction: "prev" | "next";
  onSelect?: (slug: string) => void;
}) {
  const t = useT();
  if (!doc) return <div />;
  const Icon = direction === "prev" ? ChevronLeft : ChevronRight;
  const align = direction === "next" ? "text-right items-end" : "items-start";
  return (
    <button
      type="button"
      onClick={() => onSelect?.(doc.slug)}
      className={`flex flex-col gap-1 rounded-md border border-border bg-card/40 p-3 text-left transition hover:bg-muted/40 ${align}`}
    >
      <span className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
        {direction === "prev" ? <Icon className="h-3 w-3" /> : null}
        {direction === "prev" ? t("docs_content.prev") : t("docs_content.next")}
        {direction === "next" ? <Icon className="h-3 w-3" /> : null}
      </span>
      <span className="text-sm font-medium">{doc.title}</span>
      <DocTypeBadge diataxis={doc.diataxis} />
    </button>
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
          <a href={href} target="_blank" rel="noopener noreferrer" {...rest}>
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
