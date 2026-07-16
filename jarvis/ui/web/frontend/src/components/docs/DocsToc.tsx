import { useEffect, useMemo, useState } from "react";

import { ScrollArea } from "@/components/ui/scroll-area";
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
 * IntersectionObserver — Anthropic/Mintlify style.
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
    return () => observer.disconnect();
  }, [tocHeadings, contentRef]);

  if (!tocHeadings.length) {
    return null;
  }

  return (
    <aside
      className="hidden h-full w-64 shrink-0 border-l border-border xl:block"
      aria-label={t("docs_content.on_this_page")}
    >
      <ScrollArea className="h-full">
        <div className="px-4 py-6">
          <h3 className="mb-3 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t("docs_content.on_this_page")}
          </h3>
          <ul className="space-y-1 text-xs">
            {tocHeadings.map((h) => (
              <li key={h.slug}>
                <a
                  href={`#${h.slug}`}
                  onClick={(e) => handleClick(e, h.slug)}
                  aria-current={activeSlug === h.slug ? "location" : undefined}
                  className={cn(
                    "block rounded py-0.5 transition",
                    "text-muted-foreground hover:text-foreground",
                    h.level === 3 && "ml-3",
                    activeSlug === h.slug &&
                      "border-l-2 border-primary pl-2 -ml-px font-medium text-foreground",
                  )}
                >
                  {h.text}
                </a>
              </li>
            ))}
          </ul>
        </div>
      </ScrollArea>
    </aside>
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
