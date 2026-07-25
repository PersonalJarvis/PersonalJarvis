/**
 * One named pane of the Agentic IDE: xterm.js wired to
 * `/api/agentic-ide/pty/{name}`, running one coding agent in the chosen folder.
 *
 * Two conventions matter here and both are load-bearing:
 *
 * 1. The xterm instance lives in a ref, never in state — putting it in state
 *    re-renders the component on every output chunk and the pane stutters.
 * 2. Appearance and font size are applied to the LIVE instance in their own
 *    effects. Rebuilding the terminal when the user flips to dark mode would
 *    tear down the WebSocket, which kills the agent running behind it and
 *    loses the whole session. So the connect effect depends on the pane key
 *    alone.
 *
 * ## Why this pane is configured the way it is
 *
 * A coding agent's TUI is the hardest thing you can ask a web terminal to
 * draw: box-drawing frames, emoji status markers, live-rewritten spinner lines,
 * and a prompt box that redraws on every keystroke. Rendered with xterm's
 * defaults it came out visibly broken (reported 2026-07-25): text wrapped in
 * the wrong places, the frame characters drifted out of their columns, and
 * typing left artefacts behind. Four causes, all fixed here:
 *
 * * **Character width.** Without the Unicode 11 provider, xterm measures emoji
 *   and many box/symbol characters as one cell when the terminal on the other
 *   side counts them as two. Every such glyph then shifts the rest of the line
 *   by a column — which is exactly what "the frames look wrong" means.
 * * **ConPTY line semantics.** On Windows the agent runs behind ConPTY, which
 *   re-wraps and re-emits lines differently from a POSIX pty. xterm has a
 *   dedicated compatibility mode for it; without `windowsPty` the re-emitted
 *   lines stack up as duplicated, half-overwritten rows.
 * * **Measuring before the font is ready.** FitAddon derives the column count
 *   from one measured character. Run before the web font loads, it measures
 *   the fallback font, computes the wrong column count, and the agent then
 *   formats for a width the pane does not have. Hence the re-fit once
 *   `document.fonts.ready` resolves.
 * * **Renderer.** The DOM renderer draws each cell as an element; with a TUI
 *   redrawing on every keystroke that is both slow and subtly misaligned. The
 *   canvas renderer draws on a grid, which is what a terminal actually is.
 */
import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { CanvasAddon } from "@xterm/addon-canvas";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import "@xterm/xterm/css/xterm.css";
import {
  AlertCircle,
  Circle,
  Columns2,
  Loader2,
  Maximize2,
  Minimize2,
  Rows2,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  PANE_CHROME,
  themeFor,
  type TerminalAppearance,
} from "./terminalThemes";

export type PaneStatus = "connecting" | "live" | "exited" | "error";

interface AgenticTerminalProps {
  /** Terminal call-sign — also the WS path segment. */
  name: string;
  /** Agent label shown in the pane header ("Claude Code"). */
  displayName: string;
  appearance: TerminalAppearance;
  fontSize: number;
  /** Highlight this pane as the prompt target. */
  focused?: boolean;
  onFocus?: () => void;
  onStatus?: (status: PaneStatus, detail?: string) => void;
  /** True while this pane fills the whole grid. */
  maximized?: boolean;
  onToggleMaximize?: () => void;
  /** Open another terminal beside this one. */
  onSplitRight?: () => void;
  /** Open another terminal below this one. */
  onSplitDown?: () => void;
  /** Close this pane (the caller asks for confirmation first). */
  onClose?: () => void;
  /** Disable the split buttons — the workspace is at its terminal limit. */
  splitDisabled?: boolean;
}

function socketUrl(name: string, cols: number, rows: number): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const query = new URLSearchParams({ cols: String(cols), rows: String(rows) });
  return `${proto}://${window.location.host}/api/agentic-ide/pty/${encodeURIComponent(
    name,
  )}?${query.toString()}`;
}

export function AgenticTerminal({
  name,
  displayName,
  appearance,
  fontSize,
  focused = false,
  onFocus,
  onStatus,
  maximized = false,
  onToggleMaximize,
  onSplitRight,
  onSplitDown,
  onClose,
  splitDisabled = false,
}: AgenticTerminalProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  // Lets the font-size effect trigger a REAL resize (xterm + the terminal
  // process together) without reaching into the connect effect's socket.
  const resizeRef = useRef<(() => void) | null>(null);
  const statusRef = useRef<PaneStatus>("connecting");
  // Latest callbacks/appearance without re-running the connect effect.
  const onStatusRef = useRef(onStatus);
  const initialRef = useRef({ appearance, fontSize });
  onStatusRef.current = onStatus;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const term = new Terminal({
      convertEol: false,
      fontFamily:
        "'JetBrains Mono', 'Fira Code', 'SF Mono', Consolas, 'Courier New', monospace",
      fontSize: initialRef.current.fontSize,
      // Roomier than a console default — the single biggest readability win for
      // an agent that prints prose, diffs and file trees rather than log lines.
      // Kept integral-friendly: fractional cell heights round differently per
      // row and make a redrawn TUI box look ragged.
      lineHeight: 1.3,
      // Zero, not 0.2: extra tracking is added per cell, so a box-drawing frame
      // and the text under it accumulate different sub-pixel offsets and the
      // frame visibly bends. Monospace legibility comes from the line height.
      letterSpacing: 0,
      cursorBlink: true,
      cursorStyle: "bar",
      scrollback: 10000,
      // Required by the Unicode 11 width provider below.
      allowProposedApi: true,
      // Instant scrolling: an agent that redraws a live status line while
      // animating a scroll leaves visible tearing.
      smoothScrollDuration: 0,
      // Windows only. ConPTY re-emits and re-wraps lines in a way a POSIX pty
      // never does; without telling xterm which backend it is talking to, those
      // re-emitted lines pile up as duplicated, half-overwritten rows. Harmless
      // to declare on other platforms — the pty there simply never triggers it,
      // and the value is derived from the browser rather than assumed.
      windowsPty: /windows/i.test(navigator.userAgent)
        ? { backend: "conpty" as const }
        : undefined,
      theme: themeFor(initialRef.current.appearance),
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    // Character WIDTH, not appearance: without this xterm measures emoji and
    // many box/symbol glyphs as one cell while the agent on the other side
    // counted two, and every such glyph shifts the rest of the line one column
    // left. That is the "the frames are broken" symptom.
    try {
      const unicode = new Unicode11Addon();
      term.loadAddon(unicode);
      term.unicode.activeVersion = "11";
    } catch {
      /* proposed API unavailable in this build — widths stay at Unicode 6 */
    }
    term.open(container);
    // Canvas rather than the DOM renderer: a coding agent's TUI redraws its
    // prompt box on every keystroke, and per-cell DOM elements both lag and
    // land on fractional pixel offsets. Loaded AFTER open() because it needs
    // the mounted element. A failure here is not fatal — xterm falls back to
    // the DOM renderer, which draws correctly, just less crisply.
    try {
      term.loadAddon(new CanvasAddon());
    } catch {
      /* no canvas in this environment — the DOM renderer still works */
    }
    termRef.current = term;
    fitRef.current = fit;
    try {
      fit.fit();
    } catch {
      /* not measured yet — the ResizeObserver below will fit */
    }

    const report = (status: PaneStatus, detail?: string) => {
      statusRef.current = status;
      onStatusRef.current?.(status, detail);
    };

    let ws: WebSocket | null = null;
    let disposed = false;
    let everLive = false;

    const sendResize = () => {
      // A hidden pane measures 0x0 (maximizing another one hides this one), and
      // fitting to that would resize the PTY to zero columns — which permanently
      // wrecks the agent's full-screen drawing. Skip while not measurable; the
      // ResizeObserver fires again when the pane comes back.
      if (container.clientWidth < 8 || container.clientHeight < 8) return;
      try {
        fit.fit();
      } catch {
        return;
      }
      if (term.cols < 2 || term.rows < 2) return;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ t: "r", cols: term.cols, rows: term.rows }));
      }
    };
    resizeRef.current = sendResize;

    ws = new WebSocket(socketUrl(name, term.cols || 80, term.rows || 24));
    ws.onopen = () => {
      report("connecting");
      // The spawn used a best-effort size (the mount-time fit usually runs
      // before the grid cell is measured), and resizes sent while the socket
      // was connecting were dropped — without this the agent's full-screen TUI
      // keeps drawing at the wrong width and looks clipped.
      sendResize();
      requestAnimationFrame(sendResize);
    };
    ws.onmessage = (ev) => {
      let msg: { t?: string; d?: string; code?: number; message?: string };
      try {
        msg = JSON.parse(ev.data as string);
      } catch {
        return;
      }
      if (msg.t === "o") term.write(msg.d ?? "");
      else if (msg.t === "ready") {
        everLive = true;
        report("live");
        term.focus();
      } else if (msg.t === "exit") {
        report("exited", `exit code ${msg.code ?? "?"}`);
        term.write(
          `\r\n\x1b[33m[${displayName} exited — code ${msg.code ?? "?"}]\x1b[0m\r\n`,
        );
      } else if (msg.t === "error") {
        report("error", msg.message ?? "terminal error");
      }
    };
    ws.onerror = () => report("error", "Could not reach the terminal.");
    ws.onclose = (ev) => {
      if (everLive) {
        report("exited");
      } else if (!disposed) {
        report(
          "error",
          ev.code === 4401
            ? "Terminal authorization failed — reopen the pane to retry."
            : ev.code === 4404
              ? "This terminal is no longer part of the open workspace."
              : `Terminal connection closed (code ${ev.code || "?"}).`,
        );
      }
    };

    term.onData((data) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ t: "i", d: data }));
      }
    });

    // The mount-time fit measured whatever font was loaded at that moment. If
    // the display font arrives afterwards the cell width changes underneath the
    // already-computed column count, and the agent then formats its output for a
    // width the pane does not have — text wrapping in the wrong places, which is
    // exactly the reported symptom. Re-fit once fonts settle.
    let fontsSettled = false;
    const refitOnFonts = () => {
      if (disposed || fontsSettled) return;
      fontsSettled = true;
      term.clearTextureAtlas?.();
      sendResize();
    };
    if (typeof document !== "undefined" && document.fonts?.ready) {
      void document.fonts.ready.then(refitOnFonts).catch(() => undefined);
    } else {
      refitOnFonts();
    }

    // Resizes are coalesced: dragging a split or the window fires the observer
    // dozens of times a second, and every fit both reflows xterm's buffer and
    // sends a PTY resize the agent redraws for. Unthrottled that is the visible
    // flicker while resizing.
    let resizeTimer: number | undefined;
    const scheduleResize = () => {
      if (resizeTimer !== undefined) window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        resizeTimer = undefined;
        sendResize();
      }, 80);
    };

    window.addEventListener("resize", scheduleResize);
    const ro = new ResizeObserver(scheduleResize);
    ro.observe(container);

    return () => {
      disposed = true;
      if (resizeTimer !== undefined) window.clearTimeout(resizeTimer);
      window.removeEventListener("resize", scheduleResize);
      ro.disconnect();
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
      resizeRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see file header:
    // appearance/fontSize must NOT rebuild the pane (it would kill the agent).
  }, [name]);

  // Live restyle — no reconnect, so the running agent is untouched. The canvas
  // renderer caches rendered glyphs per colour in a texture atlas, so a theme
  // change has to invalidate it or the old palette keeps being painted.
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    term.options.theme = themeFor(appearance);
    term.clearTextureAtlas?.();
  }, [appearance]);

  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    term.options.fontSize = fontSize;
    term.clearTextureAtlas?.();
    // Changing the font size changes the COLUMN COUNT. Fitting locally without
    // telling the terminal process leaves the agent formatting for the old
    // width — it keeps wrapping at 100 columns in a pane that now holds 80, and
    // every line breaks in the wrong place. The two must move together, so this
    // goes through the same resize path the observer uses.
    resizeRef.current?.();
  }, [fontSize]);

  const chrome = PANE_CHROME[appearance];

  return (
    <div
      onMouseDown={onFocus}
      className={cn(
        "relative flex h-full w-full flex-col overflow-hidden rounded-xl border transition-shadow",
        focused
          ? "border-primary/60 shadow-[0_0_0_1px_hsl(var(--primary)/0.35),0_8px_24px_-12px_rgba(0,0,0,0.5)]"
          : "shadow-[0_4px_16px_-10px_rgba(0,0,0,0.4)]",
      )}
      style={{
        background: chrome.shell,
        borderColor: focused ? undefined : chrome.border,
      }}
      data-testid={`agentic-pane-${name}`}
    >
      <PaneHeader
        name={name}
        displayName={displayName}
        appearance={appearance}
        focused={focused}
        maximized={maximized}
        onToggleMaximize={onToggleMaximize}
        onSplitRight={onSplitRight}
        onSplitDown={onSplitDown}
        onClose={onClose}
        splitDisabled={splitDisabled}
      />
      <div ref={containerRef} className="flex-1 overflow-hidden px-2 pb-1 pt-1" />
    </div>
  );
}

function PaneHeader({
  name,
  displayName,
  appearance,
  focused,
  maximized,
  onToggleMaximize,
  onSplitRight,
  onSplitDown,
  onClose,
  splitDisabled,
}: {
  name: string;
  displayName: string;
  appearance: TerminalAppearance;
  focused: boolean;
  maximized: boolean;
  onToggleMaximize?: () => void;
  onSplitRight?: () => void;
  onSplitDown?: () => void;
  onClose?: () => void;
  splitDisabled: boolean;
}) {
  const light = appearance === "light";
  return (
    <header
      className="group/header flex items-center justify-between gap-2 border-b px-3 py-2"
      style={{
        borderColor: PANE_CHROME[appearance].border,
        background: light ? "rgba(0,0,0,0.025)" : "rgba(255,255,255,0.03)",
      }}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span
          className={cn(
            "rounded-md px-2 py-0.5 font-display text-[13px] font-semibold tracking-tight",
            focused ? "bg-primary/20 text-primary" : "",
          )}
          style={
            focused
              ? undefined
              : { color: light ? "#2b2b33" : "#e8e8ec", background: light ? "rgba(0,0,0,0.05)" : "rgba(255,255,255,0.06)" }
          }
        >
          {name}
        </span>
        <span
          className="truncate text-[11px] uppercase tracking-wider"
          style={{ color: light ? "#77777f" : "#8a8a95" }}
        >
          {displayName}
        </span>
      </div>

      {/* Pane actions. Kept quiet until the pane is hovered or focused, so a
          grid of eight terminals is not a wall of icons — but always reachable
          by keyboard, and always visible on the pane you are working in. */}
      <div
        className={cn(
          "flex shrink-0 items-center gap-0.5 transition-opacity",
          focused || maximized
            ? "opacity-100"
            : "opacity-0 focus-within:opacity-100 group-hover/header:opacity-100",
        )}
      >
        <PaneAction
          label={maximized ? `Restore ${name}` : `Maximize ${name}`}
          testId={`pane-maximize-${name}`}
          light={light}
          onClick={onToggleMaximize}
        >
          {maximized ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
        </PaneAction>
        <PaneAction
          label={`Open another terminal beside ${name}`}
          testId={`pane-split-right-${name}`}
          light={light}
          disabled={splitDisabled}
          onClick={onSplitRight}
        >
          <Columns2 className="h-3.5 w-3.5" />
        </PaneAction>
        <PaneAction
          label={`Open another terminal below ${name}`}
          testId={`pane-split-down-${name}`}
          light={light}
          disabled={splitDisabled}
          onClick={onSplitDown}
        >
          <Rows2 className="h-3.5 w-3.5" />
        </PaneAction>
        <PaneAction
          label={`Close ${name}`}
          testId={`pane-close-${name}`}
          light={light}
          danger
          onClick={onClose}
        >
          <X className="h-3.5 w-3.5" />
        </PaneAction>
      </div>
    </header>
  );
}

function PaneAction({
  label,
  testId,
  light,
  danger = false,
  disabled = false,
  onClick,
  children,
}: {
  label: string;
  testId: string;
  light: boolean;
  danger?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      data-testid={testId}
      disabled={disabled || !onClick}
      onClick={(e) => {
        // The pane's own mousedown selects it as the prompt target; an action
        // click must not also count as "typing here".
        e.stopPropagation();
        onClick?.();
      }}
      onMouseDown={(e) => e.stopPropagation()}
      className={cn(
        "flex h-6 w-6 items-center justify-center rounded transition-colors disabled:cursor-not-allowed disabled:opacity-30",
        danger
          ? "hover:bg-destructive/20 hover:text-destructive"
          : light
            ? "hover:bg-black/10"
            : "hover:bg-white/10",
      )}
      style={{ color: light ? "#55555e" : "#a8a8b2" }}
    >
      {children}
    </button>
  );
}

/** Small status pill reused by the grid toolbar. */
export function PaneStatusPill({ status, detail }: { status: PaneStatus; detail?: string }) {
  if (status === "live") {
    return (
      <span className="flex items-center gap-1 text-[11px] text-emerald-400" title={detail}>
        <Circle className="h-2 w-2 fill-current" />
        live
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="flex items-center gap-1 text-[11px] text-destructive" title={detail}>
        <AlertCircle className="h-3 w-3" />
        error
      </span>
    );
  }
  if (status === "exited") {
    return (
      <span className="text-[11px] text-muted-foreground" title={detail}>
        exited
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 text-[11px] text-muted-foreground" title={detail}>
      <Loader2 className="h-3 w-3 animate-spin" />
      starting
    </span>
  );
}
