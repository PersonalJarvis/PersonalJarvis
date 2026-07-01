import { useCallback, useEffect, useRef, useState } from "react";

import { DocsSidebar } from "@/components/docs/DocsSidebar";
import { DocsContent } from "@/components/docs/DocsContent";
import { DocsToc } from "@/components/docs/DocsToc";
import { DocsSearchModal } from "@/components/docs/DocsSearchModal";
import { useDocDetail } from "@/hooks/useDocs";
import { useRecentDocs } from "@/hooks/useRecentDocs";

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
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
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

  const selectDoc = useCallback((slug: string) => {
    setSelectedSlug(slug);
    contentRef.current?.scrollTo({ top: 0 });
  }, []);

  // Strg+K / Cmd+K oeffnet das Search-Modal.
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
    <div className="flex h-full">
      <DocsSidebar
        selectedSlug={selectedSlug}
        onSelect={selectDoc}
        onOpenSearch={() => setSearchOpen(true)}
      />
      <main
        ref={contentRef as React.RefObject<HTMLElement>}
        className="flex-1 overflow-y-auto"
      >
        <DocsContent slug={selectedSlug} onSelect={selectDoc} />
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
