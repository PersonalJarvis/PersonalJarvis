import { useCallback, useEffect, useRef, useState } from "react";

import { DocsSidebar } from "@/components/docs/DocsSidebar";
import { DocsContent } from "@/components/docs/DocsContent";
import { DocsToc } from "@/components/docs/DocsToc";
import { DocsSearchModal } from "@/components/docs/DocsSearchModal";
import { useDocDetail } from "@/hooks/useDocs";
import { useRecentDocs } from "@/hooks/useRecentDocs";
import { useT } from "@/i18n";

/**
 * Top-level view for the docs section. 3-column layout (Anthropic/Mintlify style):
 * left sidebar = Diataxis tree, middle = Markdown body, right = TOC with
 * active-heading spy.
 *
 * ``selectedSlug`` is view-local — not a state-store entry, because the doc
 * only matters within this view. When switching sections we deliberately
 * forget the selection.
 *
 * ``contentRef`` points at the scrollable main container; the TOC uses it
 * as the query root for ``IntersectionObserver``.
 *
 * Ctrl+K opens full-text search as long as this view is active. The global
 * hotkey is deliberately section-local — it should not also open the
 * search modal in ChatsView/MissionsView etc.
 */
export function DocsView() {
  const t = useT();
  const [selectedSlug, setSelectedSlug] = useState<string | null>(() => {
    return new URLSearchParams(window.location.search).get("doc");
  });
  const [searchOpen, setSearchOpen] = useState(false);
  const contentRef = useRef<HTMLElement>(null);
  const { push: pushRecent } = useRecentDocs();

  // Headings from the currently selected doc — for the TOC.
  const detail = useDocDetail(selectedSlug);
  const headings = detail.data?.headings ?? [];

  // Push into the recent list once the doc loads successfully.
  useEffect(() => {
    if (detail.data) {
      pushRecent({
        slug: detail.data.slug,
        title: detail.data.title,
        diataxis: detail.data.diataxis,
      });
    }
  }, [detail.data, pushRecent]);

  // A copied ``?doc=slug#heading`` link resolves before the async Markdown
  // exists. Scroll once the requested guide has rendered.
  useEffect(() => {
    if (!detail.data || !window.location.hash) return;
    const headingId = decodeURIComponent(window.location.hash.slice(1));
    window.requestAnimationFrame(() => {
      document.getElementById(headingId)?.scrollIntoView({ block: "start" });
    });
  }, [detail.data]);

  const selectDoc = useCallback((slug: string) => {
    setSelectedSlug(slug);
    const url = new URL(window.location.href);
    url.searchParams.set("doc", slug);
    url.hash = "";
    window.history.replaceState(null, "", url);
    contentRef.current?.scrollTo({ top: 0 });
  }, []);

  const showOverview = useCallback(() => {
    setSelectedSlug(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("doc");
    url.hash = "";
    window.history.replaceState(null, "", url);
    contentRef.current?.scrollTo({ top: 0 });
  }, []);

  // Ctrl+K / Cmd+K opens the search modal.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  return (
    <div className="flex h-full min-h-0 bg-background">
      <a
        href="#docs-content"
        className="sr-only z-50 rounded-md bg-background px-3 py-2 text-sm text-foreground focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus-visible:ring-2 focus-visible:ring-ring"
      >
        {t("docs_content.skip_to_content")}
      </a>
      <DocsSidebar
        selectedSlug={selectedSlug}
        onSelect={selectDoc}
        onShowOverview={showOverview}
        onOpenSearch={() => setSearchOpen(true)}
      />
      <main
        id="docs-content"
        ref={contentRef as React.RefObject<HTMLElement>}
        tabIndex={-1}
        className="min-w-0 flex-1 overflow-y-auto"
      >
        <DocsContent
          slug={selectedSlug}
          onSelect={selectDoc}
          onShowOverview={showOverview}
        />
      </main>
      <DocsToc headings={headings} contentRef={contentRef} />

      <DocsSearchModal
        open={searchOpen}
        onOpenChange={setSearchOpen}
        onSelect={selectDoc}
      />
    </div>
  );
}
