import { useEffect, useState } from "react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { DocHeading } from "@/hooks/useDocs";

interface Props {
  headings: DocHeading[];
  /** Container in dem die <h2>/<h3>-Anchors leben. Wir queryen darin. */
  contentRef: React.RefObject<HTMLElement>;
}

/**
 * Right-Sidebar Table-of-Contents mit Active-Heading-Tracking via
 * IntersectionObserver — Anthropic/Mintlify-Stil.
 *
 * Reagiert auf H2 + H3. H4-H6 sind in unseren Docs selten, wuerden aber bei
 * Bedarf hier ergaenzt. Active-Heading wird via ``rootMargin`` zur Heading-
 * Hoehe verschoben, damit die Heading-Zeile selbst (nicht der Body darunter)
 * den Active-State setzt.
 */
export function DocsToc({ headings, contentRef }: Props) {
  const [activeSlug, setActiveSlug] = useState<string | null>(null);

  // TOC nur fuer H2 + H3 — Tutorial-Mid-Point-Checks und ADR-Subsections.
  const tocHeadings = headings.filter((h) => h.level >= 2 && h.level <= 3);

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
        // Nimm den ersten sichtbaren Eintrag — Top-most-rule.
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
        // Heading wird Active wenn es im oberen 20% des Viewports steht.
        rootMargin: "0px 0px -80% 0px",
        threshold: 0,
      },
    );

    for (const el of observed) observer.observe(el);
    return () => observer.disconnect();
  }, [tocHeadings, contentRef]);

  if (!tocHeadings.length) {
    return (
      <aside className="hidden h-full w-64 shrink-0 border-l border-border xl:block" />
    );
  }

  return (
    <aside className="hidden h-full w-64 shrink-0 border-l border-border xl:block">
      <ScrollArea className="h-full">
        <div className="px-4 py-6">
          <h3 className="mb-3 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Auf dieser Seite
          </h3>
          <ul className="space-y-1 text-xs">
            {tocHeadings.map((h) => (
              <li key={h.slug}>
                <a
                  href={`#${h.slug}`}
                  onClick={(e) => handleClick(e, h.slug)}
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
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    // URL-Hash aktualisieren ohne Full-Reload
    window.history.replaceState(null, "", `#${slug}`);
  }
}

/**
 * Minimaler CSS.escape-Polyfill fuer aeltere Browser. WebView2 ist Edge-
 * basiert und unterstuetzt CSS.escape, aber besser sicher als sorry.
 */
function cssEscape(value: string): string {
  if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(value);
  return value.replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}
