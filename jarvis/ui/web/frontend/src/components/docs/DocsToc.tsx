import { useEffect, useMemo, useState } from "react";
import { AlignLeft } from "lucide-react";

import { cn } from "@/lib/utils";
import type { DocHeading } from "@/hooks/useDocs";
import { useT } from "@/i18n";

interface Props {
  headings: DocHeading[];
  /** Container that holds the <h2>/<h3> anchors. We query inside it. */
  contentRef: React.RefObject<HTMLElement>;
}

/**
 * Right-sidebar table of contents with active-heading tracking via
 * IntersectionObserver.
 *
 * Reacts to H2 + H3. H4-H6 are rare in our docs but could be added here if
 * needed. The active-heading trigger is shifted by the heading height via
 * ``rootMargin``, so the heading line itself (not the body below it) sets
 * the active state.
 */
export function DocsToc({ headings, contentRef }: Props) {
  const t = useT();
  const [activeSlug, setActiveSlug] = useState<string | null>(null);

  // TOC only for H2 + H3 — tutorial mid-point checks and ADR subsections.
  const tocHeadings = useMemo(
    () => headings.filter((heading) => heading.level >= 2 && heading.level <= 3),
    [headings],
  );

  useEffect(() => {
    if (!tocHeadings.length || !contentRef.current) return;

    const container = contentRef.current;
    const observed: HTMLElement[] = [];
    for (const h of tocHeadings) {
      const el = container.querySelector<HTMLElement>(`#${cssEscape(h.slug)}`);
      if (el) observed.push(el);
    }
    if (!observed.length) return;
    setActiveSlug(observed[0].id);

    const observer = new IntersectionObserver(
      (entries) => {
        // Take the first visible entry — top-most rule.
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort(
            (a, b) =>
              (a.target as HTMLElement).offsetTop -
              (b.target as HTMLElement).offsetTop,
          );
        if (visible.length > 0) {
          setActiveSlug(visible[0].target.id);
        }
      },
      {
        root: container,
        // A heading becomes active when it's in the top 20% of the viewport.
        rootMargin: "0px 0px -80% 0px",
        threshold: 0,
      },
    );

    for (const el of observed) observer.observe(el);

    // The last sections of a page can be too short to ever reach the top
    // band; once the reader hits the bottom, the final heading is current.
    const onScroll = () => {
      const atBottom =
        container.scrollTop + container.clientHeight >= container.scrollHeight - 4;
      if (atBottom) setActiveSlug(observed[observed.length - 1].id);
    };
    container.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      observer.disconnect();
      container.removeEventListener("scroll", onScroll);
    };
  }, [tocHeadings, contentRef]);

  if (!tocHeadings.length) {
    return null;
  }

  // Sticky inside the docs scroller, so it travels beside the article
  // instead of hugging the window edge.
  return (
    <nav
      className="sticky top-0 hidden max-h-screen w-56 shrink-0 self-start overflow-y-auto pb-10 pt-12 xl:block"
      aria-label={t("docs_content.on_this_page")}
    >
      <p className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground-strong">
        <AlignLeft className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
        {t("docs_content.on_this_page")}
      </p>
      <ul className="border-l border-border text-sm">
        {tocHeadings.map((h) => {
          const active = activeSlug === h.slug;
          return (
            <li key={h.slug}>
              <a
                href={`#${h.slug}`}
                onClick={(e) => handleClick(e, h.slug)}
                aria-current={active ? "location" : undefined}
                className={cn(
                  "-ml-px block border-l py-1 pr-2 transition-colors",
                  h.level === 3 ? "pl-7" : "pl-4",
                  active
                    ? "border-accent font-medium text-accent"
                    : "border-transparent text-muted-foreground hover:border-border-strong hover:text-foreground",
                )}
              >
                {h.text}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

function handleClick(e: React.MouseEvent<HTMLAnchorElement>, slug: string) {
  e.preventDefault();
  const el = document.getElementById(slug);
  if (el) {
    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    el.scrollIntoView({
      behavior: reduceMotion ? "auto" : "smooth",
      block: "start",
    });
    // Update the URL hash without a full reload
    window.history.replaceState(null, "", `#${slug}`);
  }
}

/**
 * Minimal CSS.escape polyfill for older browsers. WebView2 is Edge-based
 * and supports CSS.escape, but better safe than sorry.
 */
function cssEscape(value: string): string {
  if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(value);
  return value.replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}
