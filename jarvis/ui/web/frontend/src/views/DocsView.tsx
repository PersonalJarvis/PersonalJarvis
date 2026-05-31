import { useCallback, useEffect, useRef, useState } from "react";

import { DocsSidebar } from "@/components/docs/DocsSidebar";
import { DocsContent } from "@/components/docs/DocsContent";
import { DocsToc } from "@/components/docs/DocsToc";
import { DocsSearchModal } from "@/components/docs/DocsSearchModal";
import { useDocDetail } from "@/hooks/useDocs";
import { useRecentDocs } from "@/hooks/useRecentDocs";

/**
 * Top-Level-View fuer die Doc-Sektion. 3-Spalten-Layout (Anthropic/Mintlify-Stil):
 * Left Sidebar = Diataxis-Tree, Mitte = Markdown-Body, Right = TOC mit
 * Active-Heading-Spy.
 *
 * ``selectedSlug`` ist View-lokal — kein Zustand-Store-Eintrag, weil das Doc
 * nur in dieser View relevant ist. Beim Wechsel der Sektion vergessen wir die
 * Auswahl bewusst.
 *
 * ``contentRef`` zeigt auf den scrollbaren Main-Container; der TOC nutzt ihn
 * als Query-Root fuer ``IntersectionObserver``.
 *
 * Strg+K oeffnet die Volltextsuche, solange diese View aktiv ist. Globaler
 * Hotkey ist bewusst Section-lokal — er soll nicht in ChatsView/MissionsView
 * o.ae. das Such-Modal aufmachen.
 */
export function DocsView() {
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const contentRef = useRef<HTMLElement>(null);
  const { push: pushRecent } = useRecentDocs();

  // Headings aus dem aktuell selektierten Doc — fuer den TOC.
  const detail = useDocDetail(selectedSlug);
  const headings = detail.data?.headings ?? [];

  // Beim erfolgreichen Doc-Load in die Recent-Liste schieben.
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
