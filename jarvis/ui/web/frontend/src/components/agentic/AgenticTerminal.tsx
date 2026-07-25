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
import { useCallback, useEffect, useRef, useState } from "react";
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
  Paperclip,
  Rows2,
  RotateCcw,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  PANE_CHROME,
  themeFor,
  type TerminalAppearance,
} from "./terminalThemes";
import {
  extractPaneDrop,
  extractPasteFiles,
  isEmptyPayload,
  nameClipboardFile,
  type PaneDropPayload,
} from "./paneDrop";
import { attachToTerminal } from "@/lib/agenticIdeApi";

export type PaneStatus = "connecting" | "live" | "exited" | "error";

/** A coding CLI a split may start, as offered by the pane's split menu. */
export interface SplitAgentChoice {
  /** Backend id — "claude", "codex". */
  name: string;
  /** What the user reads — "Claude Code". */
  displayName: string;
  installed: boolean;
}

export type SplitDirection = "right" | "down";

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
  /**
   * Open another terminal beside or below this one.
   *
   * `agent` is the coding CLI the user picked from the split menu; omitted, the
   * new pane inherits this pane's agent.
   */
  onSplit?: (direction: SplitDirection, agent?: string) => void;
  /**
   * Coding CLIs the split menu offers. With one (or none) there is nothing to
   * choose, so the split buttons act immediately instead of opening a menu.
   */
  agents?: SplitAgentChoice[];
  /** Close this pane (the caller asks for confirmation first). */
  onClose?: () => void;
  /** Disable the split buttons — the workspace is at its terminal limit. */
  splitDisabled?: boolean;
  /** A dropped or pasted file could not be attached — the grid surfaces it. */
  onAttachError?: (message: string) => void;
  /** Called when the user asks a dead pane to start a fresh agent. */
  onRestart?: () => void;
  /**
   * Bump to reconnect this pane.
   *
   * An exited agent leaves a dead pane with no way back: the connect effect runs
   * on mount only (deliberately — see the file header), and remounting is not an
   * option because it is what kills a LIVE agent. So the token is part of the
   * effect's identity: changing it tears down this one socket and opens a fresh
   * one, and the backend spawns a new agent for it.
   */
  restartToken?: number;
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
  onSplit,
  agents,
  onClose,
  splitDisabled = false,
  onAttachError,
  onRestart,
  restartToken = 0,
}: AgenticTerminalProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  // Lets the font-size effect trigger a REAL resize (xterm + the terminal
  // process together) without reaching into the connect effect's socket.
  const resizeRef = useRef<(() => void) | null>(null);
  const statusRef = useRef<PaneStatus>("connecting");
  // Mirrored into state purely so the header can show/hide the restart button;
  // it transitions a handful of times per pane, never per output chunk.
  const [visibleStatus, setVisibleStatus] = useState<PaneStatus>("connecting");
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
      setVisibleStatus(status);
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
  }, [name, restartToken]);

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

  /*
   * Drag a file onto the pane, or paste a screenshot into it.
   *
   * A native terminal writes a dragged file's PATH into the prompt; a browser
   * cannot, because it never tells a web page where a file lives. So the drop is
   * read synchronously (a DataTransfer empties the instant this handler
   * returns — see ./paneDrop), handed to the backend, and what comes back is
   * already typed into the agent's input.
   *
   * Deliberately typed and NOT submitted: someone dropping a screenshot wants to
   * say what to do with it. The reference appears, the cursor sits after it.
   */
  const [dragging, setDragging] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const dragDepth = useRef(0);

  const attach = useCallback(
    async (payload: PaneDropPayload) => {
      if (isEmptyPayload(payload)) {
        onAttachError?.("That drop carried no file this pane could use.");
        return;
      }
      setAttaching(true);
      try {
        await attachToTerminal(name, payload);
        termRef.current?.focus();
      } catch (e) {
        onAttachError?.((e as Error).message);
      } finally {
        setAttaching(false);
      }
    },
    [name, onAttachError],
  );

  // Clipboard images only — text paste stays with xterm, which already does it
  // right, and intercepting it would break ordinary copy-paste into the agent.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const onPaste = (event: ClipboardEvent) => {
      const images = extractPasteFiles(event.clipboardData).map((f) =>
        nameClipboardFile(f, name),
      );
      if (images.length === 0) return;
      event.preventDefault();
      void attach({ paths: [], files: images });
    };
    container.addEventListener("paste", onPaste);
    return () => container.removeEventListener("paste", onPaste);
  }, [attach, name]);

  const chrome = PANE_CHROME[appearance];

  return (
    <div
      onMouseDown={onFocus}
      onDragEnter={(e) => {
        e.preventDefault();
        // Counted, not a boolean: dragging across the xterm canvas fires
        // enter/leave for every child element, and a boolean flickers.
        dragDepth.current += 1;
        setDragging(true);
      }}
      onDragOver={(e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={() => {
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (dragDepth.current === 0) setDragging(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        dragDepth.current = 0;
        setDragging(false);
        onFocus?.();
        // Read BEFORE any await — the DataTransfer is gone after this returns.
        void attach(extractPaneDrop(e.dataTransfer));
      }}
      className={cn(
        "relative flex h-full w-full flex-col overflow-hidden rounded-xl border transition-shadow",
        focused
          ? "border-primary/60 shadow-[0_0_0_1px_hsl(var(--primary)/0.35),0_8px_24px_-12px_rgba(0,0,0,0.5)]"
          : "shadow-[0_4px_16px_-10px_rgba(0,0,0,0.4)]",
        dragging && "border-primary shadow-[0_0_0_2px_hsl(var(--primary)/0.5)]",
      )}
      style={{
        background: chrome.shell,
        borderColor: focused ? undefined : chrome.border,
      }}
      data-testid={`agentic-pane-${name}`}
    >
      <PaneHeader
        dead={visibleStatus === "exited" || visibleStatus === "error"}
        onRestart={onRestart}
        name={name}
        displayName={displayName}
        appearance={appearance}
        focused={focused}
        maximized={maximized}
        onToggleMaximize={onToggleMaximize}
        onSplit={onSplit}
        agents={agents}
        onClose={onClose}
        splitDisabled={splitDisabled}
      />
      <div ref={containerRef} className="flex-1 overflow-hidden px-2 pb-1 pt-1" />
      {(dragging || attaching) && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-background/70 backdrop-blur-[2px]">
          <div className="flex items-center gap-2 rounded-xl border border-primary/50 bg-card px-4 py-2.5 text-sm shadow-lg">
            {attaching ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                <span>Attaching to {name}…</span>
              </>
            ) : (
              <>
                <Paperclip className="h-4 w-4 text-primary" />
                <span>
                  Drop to put it in front of <strong>{name}</strong>
                </span>
              </>
            )}
          </div>
        </div>
      )}
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
  onSplit,
  agents,
  onClose,
  splitDisabled,
  dead,
  onRestart,
}: {
  name: string;
  displayName: string;
  appearance: TerminalAppearance;
  focused: boolean;
  maximized: boolean;
  onToggleMaximize?: () => void;
  onSplit?: (direction: SplitDirection, agent?: string) => void;
  agents?: SplitAgentChoice[];
  onClose?: () => void;
  splitDisabled: boolean;
  /** The agent in this pane has exited or failed — offer to start it again. */
  dead: boolean;
  onRestart?: () => void;
}) {
  const light = appearance === "light";
  // Which split button opened the CLI picker, if any.
  const [picking, setPicking] = useState<SplitDirection | null>(null);

  // With one installed CLI there is nothing to pick, so the button splits
  // straight away — a menu with a single entry is a click tax, not a choice.
  const choices = agents ?? [];
  const offersChoice = choices.filter((a) => a.installed).length > 1;

  const startSplit = (direction: SplitDirection) => {
    if (offersChoice) setPicking((current) => (current === direction ? null : direction));
    else onSplit?.(direction);
  };

  return (
    <header
      className="group/header relative flex items-center justify-between gap-2 border-b px-3 py-2"
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
        {dead && (
          <button
            type="button"
            aria-label={`Restart ${name}`}
            title={`Start a fresh ${displayName} in ${name}`}
            data-testid={`pane-restart-${name}`}
            onClick={(e) => {
              e.stopPropagation();
              onRestart?.();
            }}
            onMouseDown={(e) => e.stopPropagation()}
            className="mr-1 flex items-center gap-1 rounded bg-primary/20 px-2 py-0.5 text-[11px] font-medium text-primary transition-colors hover:bg-primary/30"
          >
            <RotateCcw className="h-3 w-3" />
            Restart
          </button>
        )}
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
          expanded={offersChoice ? picking === "right" : undefined}
          onClick={onSplit ? () => startSplit("right") : undefined}
        >
          <Columns2 className="h-3.5 w-3.5" />
        </PaneAction>
        <PaneAction
          label={`Split ${name} and open a terminal below it`}
          testId={`pane-split-down-${name}`}
          light={light}
          disabled={splitDisabled}
          expanded={offersChoice ? picking === "down" : undefined}
          onClick={onSplit ? () => startSplit("down") : undefined}
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

      {picking && (
        <SplitAgentMenu
          paneName={name}
          direction={picking}
          agents={choices}
          onDismiss={() => setPicking(null)}
          onPick={(agent) => {
            setPicking(null);
            onSplit?.(picking, agent);
          }}
        />
      )}
    </header>
  );
}

/**
 * The CLI picker a split button opens.
 *
 * Splitting used to inherit the anchor's agent silently, which made running a
 * Codex pane next to a Claude Code one impossible from the grid — you had to
 * close the workspace and start it again from the wizard. The backend always
 * accepted an agent per terminal; this is the surface that finally asks.
 *
 * A CLI that is not installed stays listed but disabled, so the absence is
 * visible and explains itself rather than the entry simply not being there.
 */
function SplitAgentMenu({
  paneName,
  direction,
  agents,
  onPick,
  onDismiss,
}: {
  paneName: string;
  direction: SplitDirection;
  agents: SplitAgentChoice[];
  onPick: (agent: string) => void;
  onDismiss: () => void;
}) {
  return (
    <>
      {/* Click-anywhere-else to dismiss, without a global listener that would
          outlive the pane. */}
      <div className="fixed inset-0 z-40" onMouseDown={onDismiss} />
      <div
        role="menu"
        aria-label={`Which CLI should run ${direction === "right" ? "beside" : "below"} ${paneName}?`}
        data-testid={`pane-split-menu-${direction}-${paneName}`}
        className="absolute right-2 top-full z-50 mt-1 w-56 rounded-lg border border-border bg-card p-1 shadow-xl"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onDismiss();
        }}
      >
        <p className="px-2 py-1.5 text-[11px] uppercase tracking-wider text-muted-foreground">
          {direction === "right" ? "Open beside — which CLI?" : "Split below — which CLI?"}
        </p>
        {agents.map((agent) => (
          <button
            key={agent.name}
            type="button"
            role="menuitem"
            autoFocus={agent.installed && agent === agents.find((a) => a.installed)}
            disabled={!agent.installed}
            data-testid={`pane-split-${direction}-${paneName}-${agent.name}`}
            onClick={(e) => {
              e.stopPropagation();
              onPick(agent.name);
            }}
            className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
          >
            <span>{agent.displayName}</span>
            {!agent.installed && (
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                not installed
              </span>
            )}
          </button>
        ))}
      </div>
    </>
  );
}

function PaneAction({
  label,
  testId,
  light,
  danger = false,
  disabled = false,
  expanded,
  onClick,
  children,
}: {
  label: string;
  testId: string;
  light: boolean;
  danger?: boolean;
  disabled?: boolean;
  /** Set when this button opens a menu — announces its state to a screen reader. */
  expanded?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      data-testid={testId}
      aria-haspopup={expanded === undefined ? undefined : "menu"}
      aria-expanded={expanded}
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
