/**
 * A Markdown deliverable rendered as a document — the one renderer both the
 * Files reader (`ArtifactViewer`) and the output page (`OutputPreview`) draw
 * with, so a report reads the same wherever it is opened.
 *
 * What it adds over plain `react-markdown`: fenced code goes through the
 * Shiki `CodeBlock`; a link to a sibling file in the same run selects that
 * file instead of navigating; an external link opens in the user's real
 * browser through the open-external bridge (the desktop WebView drops a bare
 * `target="_blank"`); an image next to the document is served from the run
 * archive. The typography is the caller's — the reader passes its own
 * classes, the output page the artifact standard's.
 */
import { useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";
import { openExternalUrl } from "@/lib/openExternal";
import { artifactInlineUrl, type ArtifactSummary } from "@/hooks/useOutputs";
import { CodeBlock } from "@/components/docs/CodeBlock";

/**
 * Split a leading YAML front matter block (`---` … `---`) off a document.
 * A SKILL.md or a note with metadata would otherwise render the block as one
 * run-on paragraph ("schema_version: "1" name: … description: …"), which
 * reads as a broken page. The metadata is kept and drawn as a small YAML
 * block above the body — it is part of the file, just not prose.
 */
export function splitFrontMatter(text: string): { meta: string | null; body: string } {
  const match = /^﻿?---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(text);
  if (!match) return { meta: null, body: text };
  const meta = match[1].trim();
  return { meta: meta.length > 0 ? meta : null, body: text.slice(match[0].length) };
}

/** Resolve `href` relative to the directory of `fromPath` inside the archive. */
export function resolveSiblingPath(fromPath: string, href: string): string | null {
  if (!href || /^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith("#") || href.startsWith("/")) {
    return null;
  }
  const clean = href.split(/[?#]/)[0];
  const base = fromPath.split("/").slice(0, -1);
  for (const seg of clean.split("/")) {
    if (seg === "" || seg === ".") continue;
    if (seg === "..") {
      if (base.length === 0) return null;
      base.pop();
    } else {
      base.push(seg);
    }
  }
  return base.join("/");
}

/**
 * The typographic base every Markdown document shares: Tailwind's prose in
 * both themes, links in the accent, inline code on a quiet chip, tables and
 * images with the app's radius. Callers add the face and the measure.
 */
export const PROSE_BASE =
  "prose prose-neutral dark:prose-invert [overflow-wrap:anywhere] " +
  "prose-headings:font-semibold prose-headings:tracking-tight prose-headings:text-pretty " +
  "prose-p:text-pretty prose-li:my-1 " +
  "prose-a:text-primary prose-a:no-underline hover:prose-a:underline " +
  "prose-code:rounded prose-code:bg-muted/60 prose-code:px-1 prose-code:py-0.5 prose-code:font-mono prose-code:text-[0.85em] prose-code:font-normal prose-code:before:hidden prose-code:after:hidden " +
  "prose-hr:border-border prose-img:rounded-md";

export function MarkdownProse({
  slug,
  path,
  files,
  text,
  onSelectSibling,
  className,
  testId = "artifact-markdown",
}: {
  slug: string;
  /** The document's own archive path — what relative links resolve against. */
  path: string;
  /** Every file of the run, so a relative link can be recognised as a sibling. */
  files: ArtifactSummary[];
  text: string;
  /** Called with a sibling's path when the reader clicks a link to it. */
  onSelectSibling?: (path: string) => void;
  className?: string;
  testId?: string;
}) {
  const known = useMemo(() => new Set(files.map((f) => f.path)), [files]);
  const { meta, body } = useMemo(() => splitFrontMatter(text), [text]);
  const components = useMemo<Components>(
    () => ({
      // Fenced blocks render their own complete container; unwrapping the
      // Markdown ``pre`` avoids invalid ``pre > div`` nesting around CodeBlock.
      pre({ children }) {
        return <>{children}</>;
      },
      code({ className: codeClass, children, ...rest }) {
        const match = /language-(\w+)/.exec(codeClass || "");
        if (!match) {
          return (
            <code className={codeClass} {...rest}>
              {children}
            </code>
          );
        }
        return (
          <CodeBlock language={match[1]} code={String(children).replace(/\n$/, "")} />
        );
      },
      a({ href, children, ...rest }) {
        const sibling = href ? resolveSiblingPath(path, href) : null;
        if (sibling && known.has(sibling) && onSelectSibling) {
          return (
            <a
              href={`#${sibling}`}
              onClick={(e) => {
                e.preventDefault();
                onSelectSibling(sibling);
              }}
              {...rest}
            >
              {children}
            </a>
          );
        }
        if (href && /^https?:\/\//i.test(href)) {
          return (
            <a
              href={href}
              rel="noopener noreferrer"
              onClick={(e) => {
                e.preventDefault();
                void openExternalUrl(href);
              }}
              {...rest}
            >
              {children}
            </a>
          );
        }
        return (
          <a href={href} {...rest}>
            {children}
          </a>
        );
      },
      img({ src, alt, ...rest }) {
        const sibling = typeof src === "string" ? resolveSiblingPath(path, src) : null;
        const resolved =
          sibling && known.has(sibling) ? artifactInlineUrl(slug, sibling) : src;
        return <img src={resolved} alt={alt ?? ""} loading="lazy" {...rest} />;
      },
      table({ children }) {
        return (
          <div className="not-prose my-5 overflow-x-auto rounded-md border border-border">
            <table className="w-full text-sm [&_td]:px-3 [&_td]:py-1.5 [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold [&_thead]:bg-muted/40 [&_tr]:border-t [&_tr]:border-border/60 [&_thead_tr]:border-t-0">
              {children}
            </table>
          </div>
        );
      },
      blockquote({ children }) {
        return (
          <blockquote className="border-l-2 border-primary/50 pl-4 not-italic text-muted-foreground">
            {children}
          </blockquote>
        );
      },
    }),
    [known, onSelectSibling, path, slug],
  );

  return (
    <article data-testid={testId} className={cn(PROSE_BASE, className)}>
      {meta !== null && (
        <div data-testid="markdown-front-matter">
          <CodeBlock language="yaml" code={meta} />
        </div>
      )}
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {body}
      </ReactMarkdown>
    </article>
  );
}
