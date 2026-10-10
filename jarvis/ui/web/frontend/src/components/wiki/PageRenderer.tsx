/**
 * Render a single wiki page as a document: header (where it lives, title,
 * facts, Obsidian hand-off) over the markdown body, with clickable
 * `[[wikilinks]]`.
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
 *
 * The curator stamps facts with `(as of 2026-08-12)`; those stamps are set as
 * quiet dates beside the fact instead of a parenthesis in the sentence.
 */
import { Children, cloneElement, isValidElement, useMemo, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  fetchWikiPage,
  fetchWikiTree,
  type WikiKind,
  type WikiTreeResponse,
} from "@/lib/wikiApi";
import { cleanTitle } from "@/lib/wikiModel";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useRunLocale } from "@/components/runs/format";

import { PageHeader } from "./PageHeader";

interface PageRendererProps {
  slug: string;
  onWikilinkClick: (targetSlug: string) => void;
}

const WIKILINK_PREFIX = "#wiki:";

// Regex covers `[[slug]]`, `[[entities/slug]]`, `[[slug|label]]`,
// `[[entities/slug|label]]`. Slugs are kebab-case, optionally folder-prefixed.
const WIKILINK_RE = /\[\[([^\]|\n]+)(?:\|([^\]\n]+))?\]\]/g;

// `(as of 2026-08-12)` — the curator's freshness stamp on a fact.
const AS_OF_RE = /\s*\(as of (\d{4}-\d{2}-\d{2})\)/g;

export function PageRenderer({ slug, onWikilinkClick }: PageRendererProps) {
  const t = useT();
  const language = useRunLocale();
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

  const page = pageQuery.data;
  const title = cleanTitle(page?.title, slug);

  const preprocessedBody = useMemo(() => {
    const raw = pageQuery.data?.body_md ?? "";
    return preprocessWikilinks(stripLeadingTitle(raw, title));
  }, [pageQuery.data?.body_md, title]);

  const dateFormat = useMemo(() => {
    try {
      return new Intl.DateTimeFormat(language, { dateStyle: "medium" });
    } catch {
      return null;
    }
  }, [language]);

  const components = useMemo<Components>(() => {
    const decorate = (children: ReactNode) => decorateAsOf(children, dateFormat, t("wiki_ui.as_of"));
    return {
      a: ({ href, children, node: _node, ...rest }) => {
        if (typeof href === "string" && href.startsWith(WIKILINK_PREFIX)) {
          const target = href.slice(WIKILINK_PREFIX.length);
          const isBroken = knownSlugs.size > 0 && !knownSlugs.has(target);
          return (
            <a
              {...rest}
              href={href}
              data-target-slug={target}
              title={isBroken ? t("wiki_ui.link_missing") : undefined}
              className={cn(
                "wikilink rounded-sm underline underline-offset-4 transition-colors",
                isBroken
                  ? "broken text-muted-foreground decoration-destructive/60 decoration-dashed hover:text-foreground"
                  : "text-accent decoration-accent/30 hover:decoration-accent",
              )}
              onClick={(e) => {
                e.preventDefault();
                if (knownSlugs.size > 0 && !knownSlugs.has(target)) {
                  // Force a re-fetch of the tree in case the cache is stale,
                  // then surface the missing page so the caller can toast.
                  qc.invalidateQueries({ queryKey: ["wiki", "tree"] });
                }
                onWikilinkClick(target);
              }}
            >
              {children}
            </a>
          );
        }
        return (
          <a
            {...rest}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent underline decoration-accent/30 underline-offset-4 transition-colors hover:decoration-accent"
          >
            {children}
          </a>
        );
      },
      h1: ({ children }) => (
        <h2 className="mb-3 mt-10 text-lg font-semibold text-foreground-strong first:mt-0">{children}</h2>
      ),
      h2: ({ children }) => (
        <h2 className="mb-3 mt-10 flex items-center gap-3 text-lg font-semibold text-foreground-strong first:mt-0">
          {children}
          <span aria-hidden className="h-px flex-1 bg-border" />
        </h2>
      ),
      h3: ({ children }) => (
        <h3 className="mb-2 mt-7 text-base font-semibold text-foreground-strong">{children}</h3>
      ),
      h4: ({ children }) => (
        <h4 className="mb-2 mt-6 text-base font-medium text-foreground">{children}</h4>
      ),
      p: ({ children }) => <p className="my-4 text-foreground">{decorate(children)}</p>,
      ul: ({ children }) => (
        <ul className="my-4 space-y-2 pl-5 text-foreground marker:text-foreground-faint [list-style-type:'–__']">
          {children}
        </ul>
      ),
      ol: ({ children }) => (
        <ol className="my-4 list-decimal space-y-2 pl-6 text-foreground marker:text-sm marker:tabular-nums marker:text-foreground-faint">
          {children}
        </ol>
      ),
      li: ({ children }) => <li className="pl-1">{decorate(children)}</li>,
      blockquote: ({ children }) => (
        <blockquote className="my-5 border-l-2 border-border-strong pl-4 text-foreground-secondary">
          {children}
        </blockquote>
      ),
      hr: () => <hr className="my-8 border-border" />,
      strong: ({ children }) => <strong className="font-semibold text-foreground-strong">{children}</strong>,
      table: ({ children }) => (
        <div className="my-5 overflow-x-auto rounded-md border border-border">
          <table className="w-full border-collapse text-sm">{children}</table>
        </div>
      ),
      th: ({ children }) => (
        <th className="border-b border-border bg-secondary px-3 py-2 text-left font-medium text-foreground">
          {children}
        </th>
      ),
      td: ({ children }) => (
        <td className="border-b border-border px-3 py-2 align-top text-foreground-secondary">{children}</td>
      ),
      pre: ({ children }) => (
        <pre className="my-5 overflow-x-auto rounded-md border border-border bg-secondary p-4 font-mono text-sm leading-6 text-foreground [&>code]:bg-transparent [&>code]:p-0">
          {children}
        </pre>
      ),
      code: ({ children, node: _node, ...rest }) => (
        <code
          {...rest}
          className="rounded-sm bg-secondary px-1 py-0.5 font-mono text-sm text-foreground"
        >
          {children}
        </code>
      ),
    };
  }, [dateFormat, knownSlugs, onWikilinkClick, qc, t]);

  if (pageQuery.isLoading) {
    return <PageSkeleton />;
  }

  if (pageQuery.isError) {
    return (
      <div className="mx-auto w-full max-w-reading px-10 py-10" data-testid="wiki-page-error">
        <p role="alert" className="text-base text-destructive">
          {t("page_renderer.load_error")}
        </p>
      </div>
    );
  }

  if (!page || !page.ok) {
    return (
      <div className="mx-auto w-full max-w-reading px-10 py-10" data-testid="wiki-page-error">
        <p role="alert" className="text-base text-destructive">
          {page?.error ?? t("page_renderer.not_found")}
        </p>
      </div>
    );
  }

  const kind = (page.kind ?? "entity") as WikiKind;
  const vaultRelPath = page.path ?? `${kind}/${slug}.md`;
  const frontmatter = page.frontmatter ?? {};

  return (
    <article
      className="profile-rise mx-auto flex w-full max-w-reading flex-col px-10 pb-24 pt-10"
      data-testid="wiki-page-renderer"
    >
      <PageHeader
        slug={slug}
        kind={kind}
        title={title}
        frontmatter={frontmatter}
        vaultRoot={treeQuery.data?.vault_root ?? ""}
        vaultRelPath={vaultRelPath}
        mtime={page.stats?.mtime}
        words={page.stats?.words}
      />

      {/* A wiki page is a document, so it is set at the reading step in full
          ink and bounded to the reading measure, in both themes. */}
      <div
        className="mt-8 border-t border-border pt-8 text-reading text-foreground"
        data-testid="wiki-page-body"
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
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
    // Escape backslashes first, then `]`, so a label cannot break out of the
    // surrounding markdown link (a trailing `\` would otherwise escape the `]`).
    const safeText = text.replace(/\\/g, "\\\\").replace(/]/g, "\\]");
    return `[${safeText}](${WIKILINK_PREFIX}${slug})`;
  });
}

/**
 * Pages open with `# <Title>`, which the header already shows. Drop that one
 * heading — and only when it repeats the title — so the title is not printed
 * twice, one above the other.
 */
export function stripLeadingTitle(body: string, title: string): string {
  const match = /^\s*#\s+(.+?)\s*#*\s*(?:\r?\n|$)/.exec(body);
  if (!match) return body;
  const heading = cleanTitle(match[1]).toLowerCase();
  return heading === cleanTitle(title).toLowerCase() ? body.slice(match[0].length) : body;
}

/** Replace `(as of YYYY-MM-DD)` in plain-text children with a quiet date. */
function decorateAsOf(
  children: ReactNode,
  dateFormat: Intl.DateTimeFormat | null,
  label: string,
): ReactNode {
  return Children.map(children, (child, index) => {
    if (typeof child === "string") {
      if (!AS_OF_RE.test(child)) return child;
      AS_OF_RE.lastIndex = 0;
      const parts: ReactNode[] = [];
      let last = 0;
      for (const match of child.matchAll(AS_OF_RE)) {
        const at = match.index ?? 0;
        if (at > last) parts.push(child.slice(last, at));
        const iso = match[1];
        const date = new Date(`${iso}T12:00:00`);
        const text =
          dateFormat && !Number.isNaN(date.getTime()) ? dateFormat.format(date) : iso;
        parts.push(
          <time
            key={`${index}-${at}`}
            dateTime={iso}
            title={`${label} ${iso}`}
            className="ml-2 inline-flex whitespace-nowrap align-baseline text-sm text-foreground-faint"
          >
            {text}
          </time>,
        );
        last = at + match[0].length;
      }
      if (last < child.length) parts.push(child.slice(last));
      return parts;
    }
    // A paragraph inside a loose list item: decorate one level down too.
    if (isValidElement<{ children?: ReactNode }>(child) && child.type === "p") {
      return cloneElement(child, undefined, decorateAsOf(child.props.children, dateFormat, label));
    }
    return child;
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
      className="mx-auto w-full max-w-reading space-y-stack px-10 pt-10"
      data-testid="wiki-page-skeleton"
      role="status"
      aria-busy="true"
    >
      <div className="h-3 w-40 animate-pulse rounded-sm bg-foreground/10" />
      <div className="mt-5 h-8 w-72 animate-pulse rounded-md bg-foreground/10" />
      <div className="h-3 w-56 animate-pulse rounded-sm bg-foreground/10" />
      <div className="space-y-stack border-t border-border pt-8">
        <div className="h-3 w-full animate-pulse rounded-sm bg-foreground/10" />
        <div className="h-3 w-5/6 animate-pulse rounded-sm bg-foreground/10" />
        <div className="h-3 w-4/6 animate-pulse rounded-sm bg-foreground/10" />
      </div>
    </div>
  );
}

export { WIKILINK_PREFIX };
