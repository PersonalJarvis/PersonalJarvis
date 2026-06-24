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
  // Slug-Index fuer Cross-Link-Resolution: Set fuer O(1)-Lookup im
  // ``a``-Renderer. Wenn ein Markdown-Link auf einen bekannten Slug zeigt,
  // navigiert der Klick intern statt extern.
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
      {/* Header — Frontmatter-Snippet */}
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

      {/* Footer — Last-Updated + Prev/Next-Pager */}
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
// Prev/Next-Berechnung + NavCard
// ----------------------------------------------------------------------

function computeNeighbors(
  grouped: Record<string, DocSummary[]> | undefined,
  currentSlug: string,
): { prev: DocSummary | null; next: DocSummary | null } {
  if (!grouped) return { prev: null, next: null };
  // Wir flatten die Sidebar-Reihenfolge: Diataxis-Order, innerhalb der
  // Gruppe alphabetisch (so wie der Backend-``grouped_by_diataxis`` sortiert).
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
// Markdown-Custom-Components
// ----------------------------------------------------------------------

interface MarkdownContext {
  knownSlugs: Set<string>;
  onInternalNavigate?: (slug: string) => void;
}

/**
 * Resolved einen Markdown-Link gegen den Doc-Index.
 *
 * Match-Reihenfolge:
 * 1. ``[text](slug-im-Index)``                -> intern
 * 2. ``[text](docs/path/to/file.md)``         -> intern wenn Slug aus Pfad ableitbar
 * 3. ``[text](docs/adr/0011-...md)``          -> intern via ``adr-...``-Slug
 * 4. http(s)://...                            -> extern, neuer Tab
 * 5. #anchor                                  -> Anchor-Sprung innerhalb der Seite
 * 6. alles andere                             -> default-Anchor
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
  // Direkter Slug-Treffer
  if (knownSlugs.has(href)) return { kind: "internal", slug: href };
  // Pfad mit .md am Ende — Stem ohne Extension probieren, plus diverse
  // Praefix-Varianten ("docs/adr/0011-router.md" -> "adr-0011-router").
  const cleaned = href.replace(/^\.?\//, "").replace(/\.md$/, "");
  // 1. ganzer Pfad als Slug
  if (knownSlugs.has(cleaned)) return { kind: "internal", slug: cleaned };
  // 2. nur Dateiname
  const parts = cleaned.split("/");
  const lastSegment = parts[parts.length - 1];
  if (knownSlugs.has(lastSegment)) {
    return { kind: "internal", slug: lastSegment };
  }
  // 3. ADR-Konvention: ``docs/adr/NNNN-...`` -> ``adr-NNNN-...``
  if (parts.length >= 2 && parts[parts.length - 2] === "adr") {
    const adrSlug = `adr-${lastSegment}`;
    if (knownSlugs.has(adrSlug)) return { kind: "internal", slug: adrSlug };
  }
  return { kind: "default" };
}

function makeMarkdownComponents(ctx: MarkdownContext): Components {
  return {
    // Code-Block (mit Sprach-Tag) wird zur Shiki-Component.
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

    // Blockquote mit GitHub-Style-Callout-Tag erkennen.
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

    // Cross-Link-Resolution
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

    // Tabellen mit etwas mehr Padding fuer bessere Lesbarkeit.
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
 * Schaut sich die Children eines blockquote an: wenn der erste Knoten ein
 * <p> mit Leading-Tag (``[!info]`` etc.) ist, liefert der typ + die
 * gestripten Children. Sonst null.
 */
function parseTaggedBlockquote(
  children: React.ReactNode,
): { type: CalloutType; children: React.ReactNode } | null {
  // children kann ein Array sein (mit Whitespace-Strings dazwischen).
  const arr = Array.isArray(children) ? children : [children];
  for (const node of arr) {
    if (typeof node === "string") {
      if (!node.trim()) continue;
      const tag = parseCalloutTag(node);
      if (tag) {
        // Der Tag selbst hat keinen Block-Wrap — wir packen den Rest direkt
        // als Text rein. Sonderfall, in der Praxis selten.
        return { type: tag.type, children: tag.rest };
      }
      return null;
    }
    // Erstes <p>-Element greifen
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
          // Re-mounted children: ersten Text durch ``rest`` ersetzen, andere
          // Knoten durchreichen.
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
