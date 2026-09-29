/**
 * The live terminal of one coding session, drawn the way the Agentic IDE
 * draws a pane: the pane's own font, weight and ink, the bottom of the real
 * screen with its cursor, and the font fitted to the pane's column count so a
 * narrow TUI fills the window instead of hugging its left edge.
 *
 * It reads the same in-memory screen feed as the desk monitors, at their
 * near-live rate, jittered (AP-33); an unchanged screen never re-renders.
 */
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { fetchPaneScreens, type PaneScreen } from "@/lib/paneScreensApi";
import { TERMINAL_FONT_STACK, TERMINAL_FONT_WEIGHT } from "@/lib/terminalFont";
import { screenChanged, visibleRows } from "./terminalScreen";

const POLL_MIN_MS = 450;
const POLL_JITTER_MS = 250;
/** Font bounds in CSS pixels: a wide TUI shrinks the text, a narrow one never blows it up. */
const MIN_FONT = 10, MAX_FONT = 15;
/** Wider terminals than this are cut on the right rather than shrunk further. */
const MAX_FIT_COLS = 120;
/** A monospace glyph is about this wide per pixel of font size. */
const CHAR_RATIO = 0.6;
/** The panes draw at line height 1.0; a touch more keeps the small window airy. */
const LINE_RATIO = 1.15;
/** Horizontal padding of the screen, matching `.office-pane-screen` in the CSS. */
const PAD = 12;

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
    fit: Math.max(1, Math.floor(height / lineH)),
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

export function PaneLiveScreen({ workspaceId, paneKey, label, loadingText, emptyText, onOpen }: {
  workspaceId: string;
  paneKey: string;
  label: string;
  loadingText: string;
  emptyText: string;
  onOpen: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const screen = useLiveScreen(workspaceId, paneKey);
  const { width, height } = useBoxSize(box);
  const grid = liveGrid(screen?.cols ?? 80, width, height);
  const view = screen ? visibleRows(screen.lines, grid.fit, screen.cursor) : null;
  const cursor = screen?.cursor && view ? [screen.cursor[0] - view.first, screen.cursor[1]] as const : null;
  const empty = !view || view.rows.every((row) => !row.trim());

  return (
    <div ref={box} className="office-pane-screen" role="img" aria-label={label} onDoubleClick={onOpen}>
      {empty ? (
        <p className="office-pane-empty">{screen === undefined ? loadingText : emptyText}</p>
      ) : (
        <pre aria-hidden style={{
          fontFamily: TERMINAL_FONT_STACK, fontWeight: TERMINAL_FONT_WEIGHT, fontSize: grid.font, lineHeight: `${grid.lineH}px`,
        }}>
          {view!.rows.map((row, i) => {
            const line = row.length > grid.cols ? `${row.slice(0, grid.cols - 1)}…` : row;
            if (!cursor || cursor[0] !== i || cursor[1] >= grid.cols) return <div key={i}>{line || " "}</div>;
            const col = cursor[1];
            const padded = line.padEnd(col + 1, " ");
            return (
              <div key={i}>
                {padded.slice(0, col)}
                <span className="office-pane-cursor">{padded[col]}</span>
                {padded.slice(col + 1)}
              </div>
            );
          })}
        </pre>
      )}
    </div>
  );
}
