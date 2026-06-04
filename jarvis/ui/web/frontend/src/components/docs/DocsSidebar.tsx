import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Clock, FileText, Search } from "lucide-react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import {
  DIATAXIS_LABELS,
  DIATAXIS_ORDER,
  useDocsGrouped,
  type DocDiataxis,
  type DocSummary,
} from "@/hooks/useDocs";
import { useRecentDocs } from "@/hooks/useRecentDocs";
import { DocTypeBadge } from "./DocTypeBadge";
import { useT } from "@/i18n";

interface Props {
  selectedSlug: string | null;
  onSelect: (slug: string) => void;
  onOpenSearch: () => void;
}

export function DocsSidebar({ selectedSlug, onSelect, onOpenSearch }: Props) {
  const t = useT();
  const { data, isLoading, error } = useDocsGrouped();
  const { recent } = useRecentDocs();
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Set<DocDiataxis>>(new Set());

  const filteredGroups = useMemo(() => {
    if (!data) return null;
    const q = query.trim().toLowerCase();
    if (!q) return data;
    const out = {} as Record<DocDiataxis, DocSummary[]>;
    for (const key of DIATAXIS_ORDER) {
      const items = data[key];
      if (!items) continue;
      const filtered = items.filter(
        (d) =>
          d.title.toLowerCase().includes(q) ||
          d.slug.toLowerCase().includes(q) ||
          d.tags.some((t) => t.toLowerCase().includes(q)),
      );
      if (filtered.length) out[key] = filtered;
    }
    return out;
  }, [data, query]);

  const totalCount = useMemo(() => {
    if (!filteredGroups) return 0;
    return Object.values(filteredGroups).reduce(
      (acc, items) => acc + (items?.length ?? 0),
      0,
    );
  }, [filteredGroups]);

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-border bg-card/40">
      {/* Header */}
      <div className="border-b border-border px-3 py-3">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold tracking-tight">
            Dokumentation
          </h2>
          <button
            type="button"
            onClick={onOpenSearch}
            title={t("docs.fulltext_search")}
            className="rounded-md p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
          >
            <Search className="h-4 w-4" />
          </button>
        </div>
        <input
          type="text"
          placeholder={t("docs.search_placeholder")}
          value={query}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
            setQuery(e.target.value)
          }
          className="flex h-8 w-full rounded-md border border-input bg-background px-2 text-xs ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        />
        <p className="mt-1 text-[11px] text-muted-foreground">
          {isLoading
            ? "lade…"
            : error
            ? "Fehler"
            : `${totalCount} Doc${totalCount === 1 ? "" : "s"}`}
        </p>
      </div>

      {/* Tree */}
      <ScrollArea className="flex-1">
        <div className="px-2 py-2">
          {/* Recent-Docs — nur wenn nicht gefiltert + mind. 1 Eintrag */}
          {!query && recent.length > 0 && (
            <div className="mb-2">
              <div className="flex w-full items-center gap-1 rounded-md px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                <Clock className="h-3 w-3" />
                <span>Zuletzt geoeffnet</span>
                <span className="ml-auto text-[10px] font-normal text-muted-foreground/70">
                  {recent.length}
                </span>
              </div>
              {recent.map((doc) => (
                <button
                  key={doc.slug}
                  type="button"
                  onClick={() => onSelect(doc.slug)}
                  data-active={doc.slug === selectedSlug || undefined}
                  className={cn(
                    "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs transition",
                    "hover:bg-muted/60",
                    doc.slug === selectedSlug &&
                      "bg-muted font-medium border-l-2 border-primary -ml-px pl-[7px]",
                  )}
                  title={doc.title}
                >
                  <FileText className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" />
                  <span className="flex-1 break-words leading-snug line-clamp-2">
                    {doc.title}
                  </span>
                  <DocTypeBadge diataxis={doc.diataxis} className="mt-0.5 shrink-0" />
                </button>
              ))}
              <div className="my-2 border-b border-border/40" />
            </div>
          )}

          {filteredGroups &&
            DIATAXIS_ORDER.map((kind) => {
              const items = filteredGroups[kind];
              if (!items?.length) return null;
              const isCollapsed = collapsed.has(kind);
              return (
                <div key={kind} className="mb-2">
                  <button
                    type="button"
                    onClick={() => {
                      const next = new Set(collapsed);
                      if (isCollapsed) next.delete(kind);
                      else next.add(kind);
                      setCollapsed(next);
                    }}
                    className="flex w-full items-center gap-1 rounded-md px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground hover:bg-muted/50"
                  >
                    {isCollapsed ? (
                      <ChevronRight className="h-3 w-3" />
                    ) : (
                      <ChevronDown className="h-3 w-3" />
                    )}
                    <span>{DIATAXIS_LABELS[kind]}</span>
                    <span className="ml-auto text-[10px] font-normal text-muted-foreground/70">
                      {items.length}
                    </span>
                  </button>
                  {!isCollapsed &&
                    items.map((doc) => (
                      <SidebarItem
                        key={doc.slug}
                        doc={doc}
                        active={doc.slug === selectedSlug}
                        onClick={() => onSelect(doc.slug)}
                      />
                    ))}
                </div>
              );
            })}
        </div>
      </ScrollArea>
    </aside>
  );
}

interface ItemProps {
  doc: DocSummary;
  active: boolean;
  onClick: () => void;
}

function SidebarItem({ doc, active, onClick }: ItemProps) {
  // Doc-Titel sollen vollstaendig lesbar sein (User-Mandat 2026-04-29) —
  // ``break-words`` erlaubt Umbruch in deutschen Komposita, ``line-clamp-2``
  // begrenzt auf max 2 Zeilen damit die Liste nicht zerfliesst, und der
  // ``title``-Attribut bleibt als Hover-Tooltip fuer den vollen Titel.
  // KEIN Diataxis-Pill — der Section-Header oben tragt die Info schon
  // (User-Mandat: keine doppelte Information, kein abgeschnittenes
  // ``LEGAC...``-Pill).
  return (
    <button
      type="button"
      onClick={onClick}
      data-active={active || undefined}
      className={cn(
        "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs transition",
        "hover:bg-muted/60",
        active &&
          "bg-muted font-medium border-l-2 border-primary -ml-px pl-[7px]",
      )}
      title={doc.title}
    >
      <FileText className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" />
      <span className="flex-1 break-words leading-snug line-clamp-2">
        {doc.title}
      </span>
    </button>
  );
}
