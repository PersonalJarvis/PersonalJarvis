import { useCallback, useMemo, useRef, useState } from "react";
import { useEventStore, type SectionId } from "@/store/events";
import { NAV_GROUPS, resolveNavLabel } from "@/components/layout/navGroups";
import { useSectionHealth } from "@/hooks/useProviders";
import { usePluginAttention } from "@/hooks/usePluginAttention";
import { layoutDock } from "@/lib/dockMagnify";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The deck's section dock — every section of the app as one icon, on the
 * left edge, with the magnification desktop docks made familiar.
 *
 * It replaces the sidebar ONLY while the deck is on screen (App.tsx): the
 * sections appear once, not twice, and the rigid list becomes the smooth
 * one. Clicking an icon jumps to that section, where the ordinary sidebar is
 * waiting; the deck's own dock is not a second navigation, it IS the
 * navigation for this one surface.
 *
 * The list is the sidebar's own `NAV_GROUPS` — one source, so a section added
 * there appears here without anyone remembering to add it twice (AP-4). The
 * attention signals are the sidebar's too: a provider error lights API Keys,
 * a plugin that needs a reconnect lights Plugins.
 *
 * Magnification is pure geometry from `lib/dockMagnify.ts`; this component
 * only measures the pointer and applies the resulting transforms. Motion is
 * suppressed for users who asked for less of it — the dock then simply
 * highlights the hovered icon.
 */
const BASE = 30; // px — icon box at rest
const GAP = 8; // px — between icon boxes at rest
const PAD = 12; // px — dock padding top/bottom

export function DeckDock({ className }: { className?: string }) {
  const t = useT();
  const activeSection = useEventStore((s) => s.activeSection);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const conversations = useEventStore((s) => s.conversations);
  const { health } = useSectionHealth();
  const pluginAttention = usePluginAttention();

  const items = useMemo(() => NAV_GROUPS.flat(), []);
  const groupBreaks = useMemo(() => {
    // Index of the first item of every group after the first — a hairline is
    // drawn above these so the dock keeps the sidebar's grouping.
    const breaks = new Set<number>();
    let n = 0;
    for (let g = 0; g < NAV_GROUPS.length; g++) {
      if (g > 0) breaks.add(n);
      n += NAV_GROUPS[g].length;
    }
    return breaks;
  }, []);

  const apikeysError = useMemo(
    () => Object.values(health).some((h) => h?.status === "error"),
    [health],
  );

  const reduced = useMemo(
    () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
    [],
  );

  const railRef = useRef<HTMLDivElement | null>(null);
  const [pointerY, setPointerY] = useState<number | null>(null);

  const onMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const rect = railRef.current?.getBoundingClientRect();
    if (!rect) return;
    setPointerY(e.clientY - rect.top - PAD);
  }, []);
  const onLeave = useCallback(() => setPointerY(null), []);

  const layout = useMemo(
    () => layoutDock(items.length, BASE, GAP, reduced ? null : pointerY),
    [items.length, pointerY, reduced],
  );

  return (
    <nav
      aria-label={t("deck.sections")}
      className={cn("relative flex h-full w-16 shrink-0 flex-col items-center", className)}
    >
      <div
        ref={railRef}
        onPointerMove={onMove}
        onPointerLeave={onLeave}
        className="relative w-full flex-1 overflow-visible"
        style={{ paddingTop: PAD, paddingBottom: PAD }}
      >
        {/* Height follows the magnified extent so the dock never clips. */}
        <div className="relative w-full" style={{ height: layout.extent }}>
          {items.map((item, i) => {
            const li = layout.items[i];
            const isActive =
              activeSection === item.id || item.matchIds?.includes(activeSection);
            const count = item.id === "chats" ? conversations.length : 0;
            const attention =
              (item.id === "apikeys" && apikeysError) ||
              (item.id === "skills" && pluginAttention.count > 0);
            const label = resolveNavLabel(t, item);
            const hot = li.scale > 1.35;

            return (
              <div key={item.id} className="contents">
                {groupBreaks.has(i) && (
                  <span
                    aria-hidden
                    className="absolute left-4 right-4 h-px bg-border"
                    style={{ top: li.center - li.size / 2 - GAP / 2 - 0.5 }}
                  />
                )}
                <button
                  type="button"
                  onClick={() => setActiveSection(item.id)}
                  aria-label={label}
                  aria-current={isActive ? "page" : undefined}
                  title={label}
                  className={cn(
                    "absolute left-1/2 flex items-center justify-center rounded-xl border transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
                    isActive
                      ? "border-primary/50 bg-primary/15 text-primary"
                      : "border-transparent text-muted-foreground hover:text-foreground",
                    hot && !isActive && "border-border/60 bg-card/30 text-foreground",
                  )}
                  style={{
                    top: li.center - li.size / 2,
                    width: li.size,
                    height: li.size,
                    transform: "translateX(-50%)",
                    // The icon scales with its box; the box is what pushes
                    // neighbours, so nothing overlaps.
                    transition: reduced ? undefined : "top 90ms linear, width 90ms linear, height 90ms linear",
                  }}
                >
                  <item.icon
                    className="shrink-0"
                    style={{ width: Math.round(16 * li.scale), height: Math.round(16 * li.scale) }}
                  />

                  {/* Live pip: something is going on in this section. */}
                  {(count > 0 || attention) && (
                    <span
                      aria-hidden
                      className={cn(
                        "absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full ring-2 ring-background",
                        attention ? "bg-destructive" : "bg-primary",
                      )}
                    />
                  )}

                  {/* Label flies out while magnified — the dock has no room
                      for text otherwise, and a hovered icon should name
                      itself without a tooltip delay. */}
                  {hot && (
                    <span
                      className="pointer-events-none absolute left-full ml-3 whitespace-nowrap rounded-md border border-border bg-background/90 px-2 py-0.5 text-xs text-foreground shadow-sm"
                    >
                      {label}
                      {count > 0 && (
                        <span className="ml-1.5 font-mono text-[10px] text-primary">{count}</span>
                      )}
                    </span>
                  )}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </nav>
  );
}

export type { SectionId };
