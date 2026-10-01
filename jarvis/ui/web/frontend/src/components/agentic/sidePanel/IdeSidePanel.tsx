import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, Maximize2, Minimize2, MoveHorizontal, PanelRightClose, Plus, X } from "lucide-react";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useResizablePane } from "@/hooks/useResizablePane";
import { PaneResizer } from "@/components/layout/PaneResizer";
import { SIDE_PANEL_ID, useIdeSidePanelStore } from "@/store/ideSidePanel";
import { useExplorerPathRouting } from "@/store/ideExplorer";
import { usePaneReviewTracking } from "@/store/paneReviews";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useWorkspacePanes } from "@/store/workspacePanes";
import { dotKindFor, workspaceAgents } from "./agentStatus";
import { SIDE_PANEL_TABS, sidePanelTab } from "./sidePanelTabs";

const WIDTH_KEY = "jarvis.agenticIde.sidePanelWidth.v1";
const DEFAULT_PX = 340;
const MIN_PX = 260;
const MAX_PX = 720;
/** The office map needs room to walk around in: its tab opens the panel at least this wide. */
const OFFICE_MIN_PX = 520;
/** Terminal canvas kept visible while the panel is open. */
const GRID_RESERVED_PX = 320;

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
/**
 * `markInUse`: frame the panel in the signal blue while the reader works in it,
 * the way a focused terminal tile is framed (the minimal pane style).
 */
export function IdeSidePanelFrame({ children, markInUse = false }: { children: ReactNode; markInUse?: boolean }) {
  const t = useT();
  const open = useIdeSidePanelStore((state) => state.open);
  const inUse = useIdeSidePanelStore((state) => state.inUse);
  const setInUse = useIdeSidePanelStore((state) => state.setInUse);
  const active = useIdeSidePanelStore((state) => state.active);
  const maximized = useIdeSidePanelStore((state) => state.maximized);
  // Ctrl+click on a path in any terminal opens it in the Explorer tab.
  useExplorerPathRouting();
  // A click on a pane in the grid counts as reviewing that agent.
  usePaneReviewTracking();
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

  // Bringing the office forward widens a narrow panel once; the user can drag
  // it back and it stays where they left it until the office is picked again.
  const officeInFront = open && active === "office";
  const paneRef = useRef(pane);
  paneRef.current = pane;
  useEffect(() => {
    if (officeInFront && paneRef.current.size < OFFICE_MIN_PX) paneRef.current.resize(OFFICE_MIN_PX);
  }, [officeInFront]);

  return (
    <div ref={frame} className="relative flex h-full min-h-0 w-full">
      {/* Hidden, not removed, under a maximized panel: the glass theme's panel is
          see-through, and the live terminals keep their size and sockets. */}
      <div data-testid="ide-side-panel-grid" className={cn("h-full min-h-0 min-w-0 flex-1", maximized && "invisible")}>{children}</div>
      {/* Maximized, the host keeps its width in the row so the terminals behind
          it never resize, and stops being the positioning box: the panel then
          anchors to the frame and covers the whole view. */}
      <div
        data-testid="ide-side-panel-host"
        className={cn(
          "h-full shrink-0",
          !maximized && "relative",
          !pane.isResizing && "transition-[width] duration-200 motion-reduce:transition-none",
        )}
        style={{ width: open ? width : 0 }}
        aria-hidden={!open}
      >
        {open && (
          <>
            {!maximized && (
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
            )}
            {/* Same element either way, so the live office scene is never remounted.
                A press anywhere in it (capture phase: the office canvas and the
                terminals in it stop their own events) makes it the area in use. */}
            <div data-testid="ide-side-panel-body" className={maximized ? "absolute inset-0 z-30" : "relative h-full"}
              onPointerDownCapture={() => setInUse(true)}>
              <IdeSidePanel />
              {/* The "you are here" frame, drawn over everything in the panel so
                  no tab header or scene can cover part of it. */}
              {markInUse && inUse && (
                <div aria-hidden="true" data-testid="ide-side-panel-in-use"
                  className="pointer-events-none absolute inset-0 z-40 ring-2 ring-inset ring-accent" />
              )}
            </div>
          </>
        )}
      </div>
      {!open && <IdeSidePanelRail />}
    </div>
  );
}

/**
 * The closed panel's edge: one labelled button per tab, always in view.
 *
 * The caption toggle alone was easy to miss (maintainer, 2026-09-28), so a
 * closed panel leaves this narrow rail on the right edge — the way an editor's
 * activity bar does — and an agent waiting for the user shows up here as a
 * count even while the panel is shut.
 */
function IdeSidePanelRail() {
  const t = useT();
  const openTab = useIdeSidePanelStore((state) => state.openTab);
  const activeWorkspaceId = useIdeProjectsStore((state) => state.activeWorkspaceId);
  const panes = useWorkspacePanes();
  const waiting = workspaceAgents(panes, activeWorkspaceId).filter((pane) => dotKindFor(pane) === "waiting").length;
  return (
    <nav
      data-testid="ide-side-panel-rail"
      aria-label={t("ide_side_panel.rail_aria")}
      className="flex h-full w-12 shrink-0 flex-col items-center gap-1.5 border-l border-border/60 bg-card/40 py-2"
    >
      {SIDE_PANEL_TABS.map((tab) => {
        const label = t(tab.labelKey);
        const badge = tab.id === "agents" && waiting > 0 ? waiting : 0;
        return (
          <button
            key={tab.id}
            type="button"
            data-testid={`ide-side-panel-rail-${tab.id}`}
            onClick={() => openTab(tab.id)}
            title={label}
            aria-label={badge ? `${label} (${fill(t("ide_side_panel.rail_waiting"), { n: badge })})` : label}
            aria-controls={SIDE_PANEL_ID}
            aria-expanded={false}
            className="relative flex w-10 flex-col items-center gap-0.5 rounded-lg py-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <tab.icon className="h-[18px] w-[18px]" aria-hidden />
            <span className="max-w-full truncate text-[9.5px] font-medium leading-none">{label}</span>
            {badge > 0 && (
              <span className="absolute right-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-warning px-1 text-[10px] font-semibold tabular-nums text-background">
                {badge}
              </span>
            )}
          </button>
        );
      })}
    </nav>
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
  const maximized = useIdeSidePanelStore((state) => state.maximized);
  const setMaximized = useIdeSidePanelStore((state) => state.setMaximized);
  const [menuOpen, setMenuOpen] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
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
            title={t("ide_side_panel.add_tab")}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((value) => !value)}
            className={HEADER_BTN}
          >
            <Plus className="h-4 w-4" aria-hidden />
          </button>
          {/* Every tab the panel can hold, always: an open one is ticked and
              picking it brings it forward, a closed one is added. */}
          {menuOpen && (
            <div role="menu" aria-label={t("ide_side_panel.add_tab")} className="absolute right-0 top-full z-30 mt-1 min-w-48 rounded-lg border border-border bg-popover p-1 shadow-float">
              {SIDE_PANEL_TABS.map((tab) => {
                const isOpen = tabs.includes(tab.id);
                return (
                  <button
                    key={tab.id}
                    type="button"
                    role="menuitemcheckbox"
                    aria-checked={isOpen}
                    data-testid={`ide-side-panel-add-${tab.id}`}
                    onClick={() => { openTab(tab.id); setMenuOpen(false); }}
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground hover:bg-secondary"
                  >
                    <tab.icon className="h-4 w-4 text-muted-foreground" aria-hidden />
                    <span className="flex-1">{t(tab.labelKey)}</span>
                    {isOpen && <Check className="h-3.5 w-3.5 text-accent" aria-hidden />}
                  </button>
                );
              })}
            </div>
          )}
        </div>
        <button
          type="button"
          data-testid="ide-side-panel-maximize"
          aria-label={t(maximized ? "ide_side_panel.restore" : "ide_side_panel.maximize")}
          title={t(maximized ? "ide_side_panel.restore" : "ide_side_panel.maximize")}
          aria-pressed={maximized}
          onClick={() => setMaximized(!maximized)}
          className={HEADER_BTN}
        >
          {maximized ? <Minimize2 className="h-4 w-4" aria-hidden /> : <Maximize2 className="h-4 w-4" aria-hidden />}
        </button>
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
      {/* Keyed by tab: Changes and Folder share one component and must not share its state. */}
      <div key={active} id={`${SIDE_PANEL_ID}-content`} role="tabpanel" className="min-h-0 flex-1">
        {current?.render()}
      </div>
    </aside>
  );
}
