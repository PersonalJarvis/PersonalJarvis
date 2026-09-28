import { useEffect, useRef, useState, type ReactNode } from "react";
import { MoveHorizontal, PanelRightClose, Plus, X } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useResizablePane } from "@/hooks/useResizablePane";
import { PaneResizer } from "@/components/layout/PaneResizer";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { SIDE_PANEL_TABS, sidePanelTab } from "./sidePanelTabs";

const WIDTH_KEY = "jarvis.agenticIde.sidePanelWidth.v1";
const DEFAULT_PX = 340;
const MIN_PX = 260;
const MAX_PX = 720;
/** Terminal canvas kept visible while the panel is open. */
const GRID_RESERVED_PX = 320;

export const SIDE_PANEL_ID = "ide-side-panel";

const HEADER_BTN =
  "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors " +
  "hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
  "disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent";

/**
 * The Agentic IDE's workspace with its right-hand side panel.
 *
 * `children` (the terminal grid) takes the free width; the panel host on the
 * right is ALWAYS mounted and only its width moves between 0 and the stored
 * size, so opening or closing it never changes the sibling identity of a live
 * terminal or its PTY socket — the same trick the legacy explorer used.
 */
export function IdeSidePanelFrame({ children }: { children: ReactNode }) {
  const t = useT();
  const open = useIdeSidePanelStore((state) => state.open);
  const frame = useRef<HTMLDivElement>(null);
  const [frameWidth, setFrameWidth] = useState(0);

  useEffect(() => {
    const node = frame.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setFrameWidth(Math.round(entry.contentRect.width)));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const max = Math.max(MIN_PX, Math.min(MAX_PX, frameWidth ? frameWidth - GRID_RESERVED_PX : MAX_PX));
  const pane = useResizablePane({
    storageKey: WIDTH_KEY,
    defaultSize: DEFAULT_PX,
    min: MIN_PX,
    max,
    axis: "x",
    // The panel sits on the right, so its grip is its LEFT edge.
    handle: "start",
  });
  // Keep a wider stored preference intact while the window is narrow.
  const width = Math.min(pane.size, max);

  return (
    <div ref={frame} className="flex h-full min-h-0 w-full">
      <div className="h-full min-h-0 min-w-0 flex-1">{children}</div>
      <div
        data-testid="ide-side-panel-host"
        className={cn(
          "relative h-full shrink-0",
          !pane.isResizing && "transition-[width] duration-200 motion-reduce:transition-none",
        )}
        style={{ width: open ? width : 0 }}
        aria-hidden={!open}
      >
        {open && (
          <>
            <div className="group absolute inset-y-0 left-0 z-20 flex -translate-x-1/2">
              <PaneResizer
                testId="ide-side-panel-resizer"
                orientation="vertical"
                active={pane.isResizing}
                title={t("ide_side_panel.resize")}
                onPointerDown={pane.startResize}
                onDoubleClick={pane.reset}
                // A start-edge grip: Left grows the panel, so the delta flips.
                onNudge={(delta) => pane.nudge(-delta)}
                valueNow={width}
                valueMin={MIN_PX}
                valueMax={max}
                controls={SIDE_PANEL_ID}
                className="h-full"
                showLine={false}
              />
              <MoveHorizontal
                aria-hidden
                className={cn(
                  "pointer-events-none absolute left-1/2 top-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-secondary p-0.5 text-foreground transition-opacity",
                  pane.isResizing ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100",
                )}
              />
            </div>
            <IdeSidePanel />
          </>
        )}
      </div>
    </div>
  );
}

/** The panel itself: tab header ("+" and collapse) over the active tab's content. */
export function IdeSidePanel() {
  const t = useT();
  const tabs = useIdeSidePanelStore((state) => state.tabs);
  const active = useIdeSidePanelStore((state) => state.active);
  const select = useIdeSidePanelStore((state) => state.select);
  const openTab = useIdeSidePanelStore((state) => state.openTab);
  const closeTab = useIdeSidePanelStore((state) => state.closeTab);
  const setOpen = useIdeSidePanelStore((state) => state.setOpen);
  const [menuOpen, setMenuOpen] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
  const addable = SIDE_PANEL_TABS.filter((tab) => !tabs.includes(tab.id));
  const current = sidePanelTab(active);

  useEffect(() => {
    if (!menuOpen) return;
    const onPointer = (event: MouseEvent) => {
      if (menu.current && !menu.current.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setMenuOpen(false); };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  return (
    <aside
      id={SIDE_PANEL_ID}
      data-testid="ide-side-panel"
      aria-label={t("ide_side_panel.aria")}
      className="flex h-full min-h-0 flex-col overflow-hidden border-l border-border bg-card/40"
    >
      <div className="flex h-11 shrink-0 items-center gap-1 border-b border-border/60 px-2">
        <div role="tablist" aria-label={t("ide_side_panel.tabs_aria")} className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
          {tabs.map((id) => {
            const tab = sidePanelTab(id);
            if (!tab) return null;
            const label = t(tab.labelKey);
            const selected = id === active;
            return (
              <div
                key={id}
                className={cn(
                  "group/tab flex h-8 min-w-0 shrink-0 items-center gap-1 rounded-lg pl-2.5 pr-1 text-sm transition-colors",
                  selected ? "bg-secondary text-foreground" : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                )}
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  aria-controls={`${SIDE_PANEL_ID}-content`}
                  data-testid={`ide-side-panel-tab-${id}`}
                  onClick={() => select(id)}
                  className="flex min-w-0 items-center gap-1.5 focus-visible:outline-none"
                >
                  <tab.icon className="h-4 w-4 shrink-0" aria-hidden />
                  <span className="truncate font-medium">{label}</span>
                </button>
                <button
                  type="button"
                  aria-label={`${t("ide_side_panel.close_tab")}: ${label}`}
                  title={t("ide_side_panel.close_tab")}
                  data-testid={`ide-side-panel-close-${id}`}
                  onClick={() => closeTab(id)}
                  className="inline-flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:bg-background/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <X className="h-3.5 w-3.5" aria-hidden />
                </button>
              </div>
            );
          })}
        </div>
        <div ref={menu} className="relative">
          <button
            type="button"
            data-testid="ide-side-panel-add"
            aria-label={t("ide_side_panel.add_tab")}
            title={addable.length ? t("ide_side_panel.add_tab") : t("ide_side_panel.all_open")}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            disabled={addable.length === 0}
            onClick={() => setMenuOpen((value) => !value)}
            className={HEADER_BTN}
          >
            <Plus className="h-4 w-4" aria-hidden />
          </button>
          {menuOpen && addable.length > 0 && (
            <div role="menu" className="absolute right-0 top-full z-30 mt-1 min-w-44 rounded-lg border border-border bg-popover p-1 shadow-float">
              {addable.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  role="menuitem"
                  data-testid={`ide-side-panel-add-${tab.id}`}
                  onClick={() => { openTab(tab.id); setMenuOpen(false); }}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground hover:bg-secondary"
                >
                  <tab.icon className="h-4 w-4 text-muted-foreground" aria-hidden />
                  {t(tab.labelKey)}
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          type="button"
          data-testid="ide-side-panel-collapse"
          aria-label={t("ide_side_panel.collapse")}
          title={t("ide_side_panel.collapse")}
          aria-controls={SIDE_PANEL_ID}
          aria-expanded
          onClick={() => setOpen(false)}
          className={HEADER_BTN}
        >
          <PanelRightClose className="h-4 w-4" aria-hidden />
        </button>
      </div>
      <div id={`${SIDE_PANEL_ID}-content`} role="tabpanel" className="min-h-0 flex-1">
        {current?.render()}
      </div>
    </aside>
  );
}
