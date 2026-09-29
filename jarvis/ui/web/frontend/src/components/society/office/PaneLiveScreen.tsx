/**
 * A small live window onto one coding session's terminal, for the session's
 * command panel: a title bar with the run state and a "Live" beat, and below
 * it the bottom of the real screen, cursor included, with the font fitted to
 * the pane's own column count so a narrow TUI fills the window instead of
 * hugging its left edge.
 *
 * It reads the same in-memory screen feed as the desk monitors, at their
 * near-live rate, jittered (AP-33); an unchanged screen never re-renders.
 */
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { Maximize2 } from "lucide-react";
import { useT } from "@/i18n";
import { fetchPaneScreens, type PaneScreen } from "@/lib/paneScreensApi";
import { screenChanged, visibleRows } from "./terminalScreen";

const POLL_MIN_MS = 450;
const POLL_JITTER_MS = 250;
/** Font bounds in CSS pixels: a wide TUI shrinks the text, a narrow one never blows it up. */
const MIN_FONT = 10, MAX_FONT = 14;
/** Wider terminals than this are cut on the right rather than shrunk further. */
const MAX_FIT_COLS = 120;
/** A monospace glyph is about this wide per pixel of font size. */
const CHAR_RATIO = 0.6;
const LINE_RATIO = 1.3;
/** Inner padding of the screen, matching `.office-live-body` in the CSS. */
const PAD = 10;

export interface LiveGrid { font: number; lineH: number; cols: number; fit: number }

/** Font and grid for a terminal `cols` wide in a box of `width` x `height`. Pure. */
export function liveGrid(cols: number, width: number, height: number): LiveGrid {
  const inner = Math.max(0, width - 2 * PAD);
  const want = Math.max(20, Math.min(cols || 80, MAX_FIT_COLS));
  const font = Math.max(MIN_FONT, Math.min(MAX_FONT, Math.floor(inner / (want * CHAR_RATIO))));
  const lineH = Math.round(font * LINE_RATIO);
  return {
    font,
    lineH,
    cols: Math.max(1, Math.floor(inner / (font * CHAR_RATIO))),
    fit: Math.max(1, Math.floor((height - 2 * PAD) / lineH)),
  };
}

function useLiveScreen(workspaceId: string, key: string): PaneScreen | null | undefined {
  // undefined = not loaded yet, null = the feed does not know this pane.
  const [screen, setScreen] = useState<PaneScreen | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    let failing = false;
    let timer: ReturnType<typeof setTimeout>;
    setScreen(undefined);
    const tick = async () => {
      try {
        const [next] = await fetchPaneScreens([{ workspaceId, key }]);
        if (!alive) return;
        failing = false;
        setScreen((prev) => (next ? (prev && !screenChanged(prev, next) ? prev : next) : null));
      } catch (err) {
        // The last good screen stays up; only the first failure of a streak is worth a line.
        if (!failing) console.warn("Pane screen unavailable", err);
        failing = true;
      }
      if (alive) timer = setTimeout(() => void tick(), POLL_MIN_MS + Math.random() * POLL_JITTER_MS);
    };
    timer = setTimeout(() => void tick(), Math.random() * POLL_JITTER_MS);
    return () => { alive = false; clearTimeout(timer); };
  }, [workspaceId, key]);
  return screen;
}

function useBoxSize(ref: RefObject<HTMLElement | null>): { width: number; height: number } {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const width = el.clientWidth, height = el.clientHeight;
      setSize((prev) => (prev.width === width && prev.height === height ? prev : { width, height }));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

export function PaneLiveScreen({ workspaceId, paneKey, title, dot, stateLabel, agentName, onOpen }: {
  workspaceId: string;
  paneKey: string;
  title: string;
  /** The IDE's dot reading of the pane: working / waiting / idle / error. */
  dot: string;
  stateLabel: string;
  agentName: string;
  onOpen: () => void;
}) {
  const t = useT();
  const body = useRef<HTMLDivElement>(null);
  const screen = useLiveScreen(workspaceId, paneKey);
  const { width, height } = useBoxSize(body);
  const grid = liveGrid(screen?.cols ?? 80, width, height);
  const view = screen ? visibleRows(screen.lines, grid.fit, screen.cursor) : null;
  const cursor = screen?.cursor && view ? [screen.cursor[0] - view.first, screen.cursor[1]] as const : null;
  const working = dot === "working";
  const empty = !view || view.rows.every((row) => !row.trim());

  return (
    <section className="office-live" data-dot={dot} aria-label={t("society.office.cmd_screen").replace("{0}", agentName)}>
      <header className="office-live-bar">
        <i className="office-dot" data-dot={dot} aria-hidden />
        <span className="office-live-title" title={title}>{title}</span>
        <span className="office-live-state">{stateLabel}</span>
        {working && <span className="office-live-beat">{t("society.office.cmd_live")}</span>}
        {screen && <span className="office-live-size">{screen.cols}×{screen.rows}</span>}
        <button type="button" className="office-live-open" onClick={onOpen}
          title={t("society.office.cmd_screen_open")} aria-label={t("society.office.cmd_screen_open")}>
          <Maximize2 aria-hidden />
        </button>
      </header>
      <div ref={body} className="office-live-body" onDoubleClick={onOpen}>
        {empty ? (
          <p className="office-live-empty">
            {screen === undefined ? t("society.office.cmd_screen_loading") : t("society.office.cmd_screen_empty")}
          </p>
        ) : (
          <pre className="office-live-rows" aria-live="off" style={{ fontSize: grid.font, lineHeight: `${grid.lineH}px` }}>
            {view!.rows.map((row, i) => {
              const line = row.length > grid.cols ? `${row.slice(0, grid.cols - 1)}…` : row;
              if (!cursor || cursor[0] !== i || cursor[1] >= grid.cols) return <div key={i}>{line || " "}</div>;
              const col = cursor[1];
              const padded = line.padEnd(col + 1, " ");
              return (
                <div key={i}>
                  {padded.slice(0, col)}
                  <span className="office-live-cursor">{padded[col]}</span>
                  {padded.slice(col + 1)}
                </div>
              );
            })}
          </pre>
        )}
      </div>
    </section>
  );
}
