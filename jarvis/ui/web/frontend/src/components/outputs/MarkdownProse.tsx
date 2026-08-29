/**
 * A Markdown deliverable rendered as a document — the one renderer both the
 * Files reader (`ArtifactViewer`) and the output page (`OutputPreview`) draw
 * with, so a report reads the same wherever it is opened.
 *
 * What it adds over plain `react-markdown`: fenced code goes through the
 * Shiki `CodeBlock`, except an `html` or `svg` fence, which is DRAWN
 * (`RenderedFence`: a chart the worker wrote as inline SVG is a chart, a
 * snippet of HTML runs in a sandbox, the markup one click away behind its
 * Source switch), and a plain-text fence holding an ASCII grid table, which
 * becomes a real table (`AsciiTableFence`); a link to a sibling file in the
 * same run selects that
 * file instead of navigating; an external link opens in the user's real
 * browser through the open-external bridge (the desktop WebView drops a bare
 * `target="_blank"`); an image next to the document is served from the run
 * archive. The typography is the caller's — the reader passes its own
 * classes, the output page the artifact standard's.
 */
import { Children, isValidElement, useMemo, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";
import { openExternalUrl } from "@/lib/openExternal";
import { artifactInlineUrl, type ArtifactSummary } from "@/hooks/useOutputs";
import { CodeBlock } from "@/components/docs/CodeBlock";
import { RenderedFence } from "@/components/outputs/RenderedFence";
import { AsciiTableFence } from "@/components/outputs/AsciiTableFence";
import { fenceLooseAsciiTables, parseAsciiTable } from "@/lib/asciiTable";

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

/** Languages whose fences may hold an ASCII grid table rather than code. */
const PLAIN_FENCE_LANGUAGES = new Set(["", "text", "txt", "plain", "plaintext", "ascii"]);

/**
 * Read the fence back out of a Markdown `<pre>`.
 *
 * The fence has to be handled HERE and not in the `code` component, because
 * react-markdown 9 dropped the `inline` prop: the same `code` component serves
 * inline `` `code` `` and block fences and cannot tell them apart. The parent
 * element can — a fence is the only `code` inside a `pre`. What arrives as
 * `children` is our own `code` component still unrendered, so its props are the
 * ORIGINAL ones: the `language-…` class and the raw fence text.
 *
 * A fence with no language tag lands here with `language: ""`. That case is
 * exactly what used to escape as a bare `<code>` with no `<pre>` around it,
 * which collapses every line break and renders a table as one run-on line.
 */
export function readFence(children: ReactNode): { language: string; code: string } | null {
  const element = Children.toArray(children).find((child) => isValidElement(child));
  if (!isValidElement<{ className?: string; children?: ReactNode }>(element)) return null;
  const match = /language-([\w+#.-]+)/.exec(element.props.className ?? "");
  return {
    language: match ? match[1].toLowerCase() : "",
    code: String(element.props.children ?? "").replace(/\n$/, ""),
  };
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
  // An ASCII grid table the author never fenced is folded into a paragraph by
  // Markdown, losing every line break; fencing it first hands it to the `pre`
  // handler, which draws it as a table. GFM tables are left untouched.
  const { meta, body } = useMemo(() => {
    const split = splitFrontMatter(text);
    return { meta: split.meta, body: fenceLooseAsciiTables(split.body) };
  }, [text]);
  const components = useMemo<Components>(
    () => ({
      // EVERY fence is replaced here, whether or not it carries a language
      // tag: each replacement brings its own complete container, so the
      // Markdown ``pre`` is dropped rather than left to wrap a ``div``.
      pre({ children }) {
        const fence = readFence(children);
        if (fence === null) return <pre>{children}</pre>;
        const { language, code } = fence;
        if (language === "html" || language === "svg") {
          return <RenderedFence language={language} code={code} />;
        }
        if (PLAIN_FENCE_LANGUAGES.has(language)) {
          const grid = parseAsciiTable(code);
          if (grid !== null) return <AsciiTableFence grid={grid} code={code} />;
        }
        return <CodeBlock language={language} code={code} />;
      },
      // Inline code only — a fence never reaches this, `pre` consumed it.
      // ``node`` is dropped from the spread on every element below: it is
      // react-markdown's syntax tree, and React would write it into the DOM
      // as ``node="[object Object]"``.
      code({ node: _node, className: codeClass, children, ...rest }) {
        return (
          <code className={codeClass} {...rest}>
            {children}
          </code>
        );
      },
      a({ node: _node, href, children, ...rest }) {
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
      img({ node: _node, src, alt, ...rest }) {
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
