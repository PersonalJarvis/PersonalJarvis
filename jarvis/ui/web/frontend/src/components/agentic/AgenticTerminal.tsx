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
import { usePaneFileDrag } from "./paneFileDrag";
import { PaneRecap } from "./PaneRecap";
import { attachToTerminal } from "@/lib/agenticIdeApi";
import type { RecapReason, RecapSource } from "@/lib/agenticIdeApi";
import { attachTerminalBridge } from "@/lib/editActions";
import { robustPaste } from "@/lib/clipboard";
import { installPasteBridge } from "./terminalPaste";
import { createKeyEventChain } from "./terminalKeyChain";
import { installNewlineBridge } from "./terminalNewline";
import { OffscreenBuffer } from "./offscreenBuffer";
import { installQuerySuppression } from "./terminalQueries";
import { PaneScrollbar } from "./PaneScrollbar";
import { openPaneSocket, type PaneSocket } from "./paneSocket";

export type PaneStatus = "connecting" | "live" | "exited" | "error";

/** A coding CLI a split may start, as offered by the pane's split menu. */
export interface SplitAgentChoice {
  /** Backend id — "claude", "codex", "shell". */
  name: string;
  /** What the user reads — "Claude Code", "Plain Terminal". */
  displayName: string;
  installed: boolean;
  /**
   * `"cli"` for a coding agent, `"shell"` for a plain terminal on this
   * machine's own shell. Carried so the menu can say what a choice actually
   * opens without knowing any entry by name.
   */
  kind?: string;
  /** One line under the name — the shell that would open, for instance. */
  description?: string;
}

export type SplitDirection = "right" | "down";

/**
 * Where a pane's recap came from, kept together rather than as five more props.
 *
 * None of it changes what the header SAYS — it changes what the card behind the
 * header can explain about it, which is the difference between a thin recap and
 * a thin recap that tells you no model could be reached to write a better one.
 */
export interface PaneRecapMeta {
  source?: RecapSource;
  reason?: RecapReason;
  /** The model that wrote it, when one did. */
  writer?: string;
  /** What went wrong the last time this pane was summarized. */
  note?: string;
  /** Unix seconds; 0 for the recap derived from the pane's own output. */
  generatedAt?: number;
}

/** What the recap card may do about the recap. Absent leaves it read-only. */
export interface PaneRecapActions {
  onSave?: (headline: string, detail: string) => Promise<void>;
  onClear?: () => Promise<void>;
  onRefresh?: () => Promise<void>;
}

interface AgenticTerminalProps {
  /** Terminal call-sign — also the WS path segment. */
  name: string;
  /**
   * Which workspace this pane belongs to.
   *
   * Sent with the socket so the backend can pin it: several workspaces can be
   * open, the front one changes while sockets are alive, and a keystroke must
   * reach the pane it was typed into rather than whichever workspace happens to
   * be showing when it arrives.
   */
  workspaceId?: string;
  /** Agent label shown in the pane header ("Claude Code"). */
  displayName: string;
  /**
   * What this session is doing, in one clause — the header's main label.
   *
   * It REPLACES the agent name there when present, because the agent name is
   * the same for every pane in the grid and this is the part that differs. The
   * CLI is still one hover away (the tooltip names it) and the pane's call-sign
   * badge never moves.
   */
  recap?: string;
  /** The several-sentence version of `recap`, read in the recap card. */
  recapDetail?: string;
  /** Who wrote the recap and why — the card's footer and its explanation. */
  recapMeta?: PaneRecapMeta;
  /** Rewriting, resetting and re-summarizing it. Absent = read-only card. */
  recapActions?: PaneRecapActions;
  /**
   * Which subscription this pane runs on ("Work seat"), when that is worth
   * saying. Undefined for everyone with a single login — the header must not
   * grow a badge that answers a question the user does not have.
   */
  accountLabel?: string | null;
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
  /** A drop or a paste could not be completed — the grid surfaces it. */
  onAttachError?: (message: string) => void;
  /** Called when the user asks a dead pane to start a fresh agent. */
  onRestart?: () => void;
  /**
   * Press on the pane's header — the grip that picks this pane up.
   *
   * The header rather than the whole pane, because the rest of the pane is a
   * live terminal: a drag started there would be a text selection inside the
   * agent's output, which is a thing people do all day. Omitted, the header is
   * an ordinary header and the pane cannot be dragged (a maximized pane, or one
   * in selection mode, has nowhere to be dropped).
   */
  onArrangeStart?: (event: React.PointerEvent) => void;
  /** True while THIS pane is the one being carried — it is drawn as lifted. */
  arranging?: boolean;
  /**
   * True while the workspace's geometry is actively being dragged.
   *
   * The pane then stops refitting itself: a fit reflows xterm's buffer AND
   * tells the agent its new size, and an agent answers that by redrawing its
   * whole screen. Sixty of those a second, across every pane a seam touches, is
   * what turned dragging a boundary into a slideshow — and every one of them is
   * discarded by the next pixel of movement. The size is taken in a single pass
   * the moment this goes false, which is also what stops the terminal's
   * contents from trailing the frame around them for longer than they must.
   */
  layoutBusy?: boolean;
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

export function AgenticTerminal({
  name,
  workspaceId,
  displayName,
  recap,
  recapDetail,
  accountLabel,
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
  onArrangeStart,
  arranging = false,
  layoutBusy = false,
}: AgenticTerminalProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  // The padded box around the xterm host. The pane's scrollbar overlays it and
  // measures hover against it — see ./PaneScrollbar.
  const scrollRegionRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  // Lets the font-size effect trigger a REAL resize (xterm + the terminal
  // process together) without reaching into the connect effect's socket.
  const resizeRef = useRef<(() => void) | null>(null);
  const statusRef = useRef<PaneStatus>("connecting");
  // Mirrored into state purely so the header can show/hide the restart button;
  // it transitions a handful of times per pane, never per output chunk.
  const [visibleStatus, setVisibleStatus] = useState<PaneStatus>("connecting");
  // Bumped once per built terminal. The scrollbar subscribes to xterm events,
  // and the instance behind `termRef` is replaced on every restart — without a
  // signal it would keep listening to a disposed one.
  const [termEpoch, setTermEpoch] = useState(0);
  const getTerminal = useCallback(() => termRef.current, []);
  // Latest callbacks/appearance without re-running the connect effect.
  const onStatusRef = useRef(onStatus);
  const onAttachErrorRef = useRef(onAttachError);
  const initialRef = useRef({ appearance, fontSize });
  // The ground this pane draws on, as of NOW — the socket tells the backend, so
  // that the agent's CLI is answered with the colours it is actually drawing on
  // when it asks. Read at connect time, hence a ref rather than the prop: the
  // connect effect must not re-run when the user flips the theme.
  const appearanceRef = useRef(appearance);
  // Read by the connect effect's resize scheduler, which is built once and
  // therefore cannot see the prop change.
  const layoutBusyRef = useRef(layoutBusy);
  onStatusRef.current = onStatus;
  onAttachErrorRef.current = onAttachError;
  appearanceRef.current = appearance;
  layoutBusyRef.current = layoutBusy;

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
    // Before anything is written to this terminal: the agent's CLI asks what
    // its terminal is and which colours it draws on within milliseconds of
    // starting, and an answer produced HERE has to cross the socket twice
    // before reaching it — too late, and then visible as junk in its prompt.
    // The backend answers those instead. See ./terminalQueries.
    const disposeQuerySuppression = installQuerySuppression(term.parser);
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
    setTermEpoch((epoch) => epoch + 1);
    // Let the app-wide right-click menu reach this terminal. It cannot use the
    // browser selection here — the canvas renderer above paints the text, so
    // there is no selectable DOM to read — and it must paste through xterm so
    // the sequence is bracketed rather than typed key by key.
    attachTerminalBridge(container, {
      getSelection: () => term.getSelection(),
      paste: (text) => term.paste(text),
      focus: () => term.focus(),
    });
    // Both keyboard bridges below claim keystrokes, and xterm holds exactly ONE
    // custom key handler — attaching them directly would leave only the last
    // one working, silently. See ./terminalKeyChain.
    const keys = createKeyEventChain(term);
    // Make Ctrl+V / Cmd+V paste. Left to itself xterm reads the chord as the
    // terminal control code ^V and CANCELS the keystroke, so the browser never
    // runs its own paste and nothing arrives at all — see ./terminalPaste.
    const disposePasteBridge = installPasteBridge(
      { attachCustomKeyEventHandler: keys.add, paste: (text) => term.paste(text) },
      container,
      {
        readClipboard: robustPaste,
        isMac: /mac|iphone|ipad/i.test(navigator.userAgent),
        onUnavailable: () =>
          onAttachErrorRef.current?.(
            "Could not read the clipboard on this machine.",
          ),
      },
    );
    // Make Shift+Enter (and Option/Cmd+Enter) break the line instead of sending
    // the half-written instruction — see ./terminalNewline.
    const disposeNewlineBridge = installNewlineBridge({
      attachCustomKeyEventHandler: keys.add,
      input: (data) => term.input(data),
    });
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

    let socket: PaneSocket | null = null;
    let disposed = false;
    // At most one line per KIND of trouble: one when the connection starts
    // wobbling, one if it is declared unreachable. A reconnect that succeeds
    // redraws the screen from the server's replay buffer anyway, so narrating
    // every attempt would scroll the agent's own output away for nothing — and
    // a pane that keeps knocking every half minute would otherwise stamp a red
    // line into the terminal twice a minute forever.
    let troubleShown: "retrying" | "unreachable" | null = null;

    /*
     * Is anyone actually looking at this pane?
     *
     * A workspace holds dozens of terminals and the browser draws all of them on
     * the SAME thread that has to notice a keypress — so a pane scrolled out of
     * the grid, or hidden behind a maximized sibling, was spending real frames
     * painting pixels nobody could see, at the direct expense of the pane being
     * typed into. Its output is parked instead and written in one call when it
     * comes back (see ./offscreenBuffer).
     *
     * Starts as VISIBLE: the observer's first callback is asynchronous, and a
     * pane that withheld output until it arrived would flicker blank on mount.
     */
    let paneVisible = true;
    const offscreen = new OffscreenBuffer();

    const showPane = () => {
      if (paneVisible) return;
      paneVisible = true;
      const held = offscreen.drain();
      if (held) term.write(held);
    };

    // Everything this pane draws goes through here, not just the agent's
    // stream: an exit banner written straight to xterm while output is parked
    // would appear ABOVE the output it is supposed to follow.
    const writeToPane = (text: string) => {
      if (paneVisible) {
        term.write(text);
        return;
      }
      offscreen.push(text);
      // A pane that has parked all it may hold WRITES rather than forgets. An
      // agent's TUI is drawn by relative cursor moves, so a stream that lost
      // its front does not repair itself — the pane would come back showing
      // the spinner row it rewrote last and blank rows where its prompt box
      // belongs. Parsing into a surface nobody is painting is the cheap half
      // of what parking avoids; a permanently broken screen is not.
      if (offscreen.full) term.write(offscreen.drain());
    };

    /*
     * The size the terminal PROCESS has actually been told.
     *
     * Recorded only when the socket took the frame, and that is the whole
     * point. A pane's socket is not open at all times — a backend restart, a
     * moment of unreachability, a reconnect in flight — and anything handed to
     * one in those states goes nowhere. Treating a size as delivered because
     * it was offered is what let one go missing for good: the pane looked
     * right, and the agent inside it went on formatting for the size it last
     * heard about, drawing its screen into a corner of a pane that had become
     * much larger. Left un-recorded, the next fit offers it again, and a fresh
     * socket is told unconditionally.
     */
    let sentSize: { cols: number; rows: number } | null = null;

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
      const size = { cols: term.cols, rows: term.rows };
      // Already delivered and unchanged: the fit above was the whole job.
      // Re-announcing a size makes the agent on the other end redraw its
      // entire screen, and a pane refits several times per settling layout.
      if (sentSize && sentSize.cols === size.cols && sentSize.rows === size.rows) {
        return;
      }
      if (socket?.send({ t: "r", ...size })) sentSize = size;
    };
    resizeRef.current = sendResize;

    socket = openPaneSocket(
      {
        name,
        workspaceId,
        cols: term.cols || 80,
        rows: term.rows || 24,
        appearance: appearanceRef.current,
      },
      {
        onOpen: () => {
          report("connecting");
          // A fresh socket has been told nothing about this pane, whatever the
          // one before it heard — so the size goes out again unconditionally.
          // This is also what hands over a size that was measured while the
          // pane was unreachable.
          sentSize = null;
          // The spawn used a best-effort size (the mount-time fit usually runs
          // before the grid cell is measured), and resizes sent while the socket
          // was connecting were dropped — without this the agent's full-screen
          // TUI keeps drawing at the wrong width and looks clipped.
          sendResize();
          requestAnimationFrame(sendResize);
        },
        onOutput: (text) => writeToPane(text),
        onReady: ({ resumed, reattached }) => {
          troubleShown = null;
          // Say which of THREE things happened. They look identical on screen and
          // only differ when it matters: an agent that never stopped still holds
          // everything, a resumed one re-read its history, and a fresh one will
          // give a blank stare to the first follow-up question.
          report(
            "live",
            reattached
              ? "still running — picked up where you left it"
              : resumed
                ? "continued its previous conversation"
                : "started a new conversation",
          );
          term.focus();
        },
        onExit: (code) => {
          report("exited", `exit code ${code}`);
          writeToPane(
            `\r\n\x1b[33m[${displayName} exited — code ${code}]\x1b[0m\r\n`,
          );
        },
        onTrouble: (message, retrying) => {
          if (disposed) return;
          // A scheduled retry means the pane is not dead yet. Calling it "error"
          // there is what left a whole grid painted red over a backend that was
          // merely restarting.
          report(retrying ? "connecting" : "error", message);
          const kind = retrying ? "retrying" : "unreachable";
          if (troubleShown === kind) return;
          troubleShown = kind;
          writeToPane(
            `\r\n\x1b[${retrying ? "33" : "31"}m[${message}]\x1b[0m\r\n`,
          );
        },
      },
    );

    term.onData((data) => {
      socket?.send({ t: "i", d: data });
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
      // Nothing at all while a seam or the prompt bar is being dragged: the
      // pane is mid-gesture, every size it could measure is about to be
      // replaced, and the fit would cost the frame the drag needs. The pane is
      // refitted from the `layoutBusy` effect the moment the drag ends.
      if (layoutBusyRef.current) return;
      if (resizeTimer !== undefined) window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        resizeTimer = undefined;
        sendResize();
      }, 80);
    };

    window.addEventListener("resize", scheduleResize);
    const ro = new ResizeObserver(scheduleResize);
    ro.observe(container);

    /*
     * Stop drawing panes nobody can see.
     *
     * Two cases, one mechanism: a pane scrolled past in a tall grid, and a pane
     * hidden with `display:none` because a sibling is maximized — neither
     * intersects the viewport, and both used to keep parsing and repainting an
     * agent's full-screen UI on the thread that owes the user a keystroke.
     *
     * The margin is generous on purpose: a pane one flick of the wheel away
     * catches up before it is looked at, so scrolling never shows a pane
     * mid-write. `IntersectionObserver` is absent in some test environments and
     * very old engines — there the pane simply stays visible, which is exactly
     * the behaviour this replaces.
     */
    let io: IntersectionObserver | null = null;
    if (typeof IntersectionObserver !== "undefined") {
      io = new IntersectionObserver(
        (entries) => {
          const onScreen = entries[entries.length - 1]?.isIntersecting ?? true;
          if (onScreen) showPane();
          else paneVisible = false;
        },
        { rootMargin: "300px" },
      );
      io.observe(container);
    }

    return () => {
      disposed = true;
      if (resizeTimer !== undefined) window.clearTimeout(resizeTimer);
      window.removeEventListener("resize", scheduleResize);
      ro.disconnect();
      io?.disconnect();
      disposePasteBridge();
      disposeNewlineBridge();
      disposeQuerySuppression();
      try {
        socket?.close();
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
   * Refit when the pane is maximized, and again when it is restored.
   *
   * Maximizing is by far the largest size change a pane ever makes — a cell in
   * a grid of ten becomes the whole window — and until now the terminal inside
   * it only found out through its ResizeObserver. That is a single debounced
   * notification, competing with everything else the grid re-lays out in the
   * same breath, and delivered when the browser gets round to it. Usually it
   * arrives and the pane fills. Occasionally it did not, and the result was the
   * reported bug: a pane visibly maximized with its agent still drawing at the
   * old cell's width, the rest of the window left blank.
   *
   * So the one size change the pane genuinely KNOWS about is driven from that
   * knowledge instead of waited for. Three passes because the grid settles over
   * a frame or two — and they are nearly free: a fit that lands on the size the
   * terminal already has sends nothing at all.
   */
  useEffect(() => {
    const refit = () => resizeRef.current?.();
    const frame = requestAnimationFrame(refit);
    const timers = [
      window.setTimeout(refit, 120),
      window.setTimeout(refit, 400),
    ];
    return () => {
      cancelAnimationFrame(frame);
      for (const timer of timers) window.clearTimeout(timer);
    };
  }, [maximized]);

  /*
   * Catch up the instant a drag lets go.
   *
   * While `layoutBusy` is true the pane deliberately ignores its own
   * ResizeObserver (see the prop), so this is where the size it ended up with
   * is finally taken. Immediately rather than through the observer's 80 ms
   * coalescing window: this is exactly the moment where the terminal's contents
   * still occupy the shape the pane had BEFORE the drag — the strip of empty
   * ground under the agent's last line — and every millisecond of it is
   * visible. The second pass one frame later is for the layout that settles
   * after the release (a scrollbar appearing, the prompt bar reflowing); a fit
   * that lands on the size the terminal already has sends nothing at all.
   */
  useEffect(() => {
    if (layoutBusy) return;
    resizeRef.current?.();
    const frame = requestAnimationFrame(() => resizeRef.current?.());
    return () => cancelAnimationFrame(frame);
  }, [layoutBusy]);

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
   *
   * WHEN the pane arms for a drop — and when it must stay quiet — lives in
   * ./paneFileDrag, because getting that wrong is what made a pane offer itself
   * to a user who was holding nothing at all (BUG-110).
   */
  const [attaching, setAttaching] = useState(false);

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

  const { dragging, handlers: dragHandlers } = usePaneFileDrag(
    useCallback(
      (dt: DataTransfer) => {
        onFocus?.();
        // Read BEFORE any await — the DataTransfer is gone after this returns.
        void attach(extractPaneDrop(dt));
      },
      [attach, onFocus],
    ),
  );

  // Clipboard images only — pasted TEXT belongs to xterm, which turns it into a
  // proper bracketed paste the agent's prompt box understands.
  //
  // Registered in the CAPTURE phase, and that is load-bearing rather than a
  // style choice: xterm's own paste handler calls `stopPropagation()`, so a
  // listener sitting on this container in the normal bubbling phase is never
  // reached at all and pasting a screenshot silently did nothing. Capture
  // travels down to the target, so it gets there first.
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
    container.addEventListener("paste", onPaste, true);
    return () => container.removeEventListener("paste", onPaste, true);
  }, [attach, name]);

  const chrome = PANE_CHROME[appearance];

  return (
    <div
      onMouseDown={onFocus}
      {...dragHandlers}
      className={cn(
        "relative flex h-full w-full flex-col overflow-hidden rounded-lg border transition-shadow",
        focused
          ? "border-primary/60 shadow-[0_0_0_1px_hsl(var(--primary)/0.35),0_8px_24px_-12px_rgba(0,0,0,0.5)]"
          : "shadow-[0_4px_16px_-10px_rgba(0,0,0,0.4)]",
        dragging && "border-primary shadow-[0_0_0_2px_hsl(var(--primary)/0.5)]",
        // Lifted out of the grid while it is being carried: the pane stays where
        // it is (moving it under the cursor would tear down nothing but would
        // reflow every other pane on every mouse move) and says so by fading.
        arranging && "opacity-45",
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
        onArrangeStart={onArrangeStart}
        arranging={arranging}
        name={name}
        displayName={displayName}
        recap={recap}
        recapDetail={recapDetail}
        accountLabel={accountLabel}
        appearance={appearance}
        focused={focused}
        maximized={maximized}
        onToggleMaximize={onToggleMaximize}
        onSplit={onSplit}
        agents={agents}
        onClose={onClose}
        splitDisabled={splitDisabled}
      />
      {/*
        Keep the visual inset OUTSIDE xterm's measured host. FitAddon reads the
        host's border-box but does not subtract padding on that host, so putting
        the inset there made it report one row more than the pane could show.
        The last terminal line was consequently clipped after a vertical resize.

        `min-h-0` is equally load-bearing: this is a shrinking flex child, and
        xterm's canvas must not become its implicit minimum height.
      */}
      <div
        ref={scrollRegionRef}
        className="relative min-h-0 flex-1 overflow-hidden px-1.5 pb-0.5 pt-0.5"
      >
        <div
          ref={containerRef}
          data-testid={`agentic-terminal-host-${name}`}
          // Read by ./index.css, which anchors the contents to the bottom for
          // the length of a drag — see the rule there for why that is the side
          // the unused ground belongs on.
          data-layout-busy={layoutBusy ? "true" : "false"}
          className="agentic-terminal-host h-full min-h-0 w-full overflow-hidden"
        />
        {/* Overlaid rather than laid out, and deliberately not the browser's
            own bar — see ./PaneScrollbar for why a CSS-only scrollbar could
            never work in a Claude Code pane. */}
        <PaneScrollbar
          name={name}
          regionRef={scrollRegionRef}
          hostRef={containerRef}
          getTerminal={getTerminal}
          epoch={termEpoch}
          appearance={appearance}
        />
      </div>
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
  recap,
  recapDetail,
  recapMeta,
  recapActions,
  accountLabel,
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
  onArrangeStart,
  arranging = false,
}: {
  name: string;
  displayName: string;
  recap?: string;
  recapDetail?: string;
  recapMeta?: PaneRecapMeta;
  recapActions?: PaneRecapActions;
  accountLabel?: string | null;
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
  /** Press on the header picks the pane up; absent leaves it undraggable. */
  onArrangeStart?: (event: React.PointerEvent) => void;
  arranging?: boolean;
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
      data-testid={`pane-header-${name}`}
      // The grip. It is the header itself rather than a separate handle icon,
      // because that is where the gesture is already expected — every window on
      // every desktop is dragged by its title bar.
      //
      // The press is ignored when it lands on one of the header's own controls:
      // the buttons stop `mousedown`, which says nothing about `pointerdown`, so
      // without this check pressing Close would also pick the pane up and a
      // twitchy hand would move a pane it meant to shut.
      onPointerDown={
        onArrangeStart
          ? (event) => {
              const target = event.target as HTMLElement | null;
              if (target?.closest("button, a, input, [role='menuitem']")) return;
              onArrangeStart(event);
            }
          : undefined
      }
      title={
        onArrangeStart
          ? `Drag ${name} by this bar to move it — drop it on another terminal to swap, or near an edge to place it there`
          : undefined
      }
      className={cn(
        "group/header relative flex items-center justify-between gap-1.5 border-b px-2 py-1",
        onArrangeStart && (arranging ? "cursor-grabbing" : "cursor-grab"),
      )}
      style={{
        borderColor: PANE_CHROME[appearance].border,
        background: light ? "rgba(0,0,0,0.025)" : "rgba(255,255,255,0.03)",
        // Claims the touch gesture for the drag. Without it a touch that starts
        // on the header scrolls the workspace instead of lifting the pane, and
        // the drag never begins at all.
        touchAction: onArrangeStart ? "none" : undefined,
      }}
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span
          className={cn(
            "shrink-0 rounded-md px-2 py-0.5 font-display text-[13px] font-semibold tracking-tight",
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
        <PaneRecap
          name={name}
          displayName={displayName}
          recap={recap}
          detail={recapDetail}
          source={recapMeta?.source}
          reason={recapMeta?.reason}
          writer={recapMeta?.writer}
          note={recapMeta?.note}
          generatedAt={recapMeta?.generatedAt}
          light={light}
          onSave={recapActions?.onSave}
          onClear={recapActions?.onClear}
          onRefresh={recapActions?.onRefresh}
        />
        {/* Which of several subscriptions this pane is spending. Only rendered
            when the user actually has more than one, so the header stays quiet
            for everybody else — but with two seats open side by side, knowing
            which pane bills which plan is the whole point. */}
        {accountLabel && (
          <span
            className="max-w-[8rem] shrink-0 truncate rounded-full px-1.5 text-[10px] tracking-wide"
            style={{
              color: light ? "#6b6b73" : "#9a9aa5",
              backgroundColor: light ? "#00000010" : "#ffffff12",
            }}
            title={`Running on ${accountLabel}`}
            data-testid={`pane-account-${name}`}
          >
            {accountLabel}
          </span>
        )}
      </div>

      {/* Pane actions stay visible in every terminal, including panes that are
          not focused, so the controls can be found without probing by hover. */}
      <div className="flex shrink-0 items-center gap-0.5 opacity-100">
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
 * The picker a split button opens: what should run in the new pane?
 *
 * Splitting used to inherit the anchor's agent silently, which made running a
 * Codex pane next to a Claude Code one impossible from the grid — you had to
 * close the workspace and start it again from the wizard. The backend always
 * accepted an agent per terminal; this is the surface that finally asks.
 *
 * The list is whatever the backend registered, so it is not a fixed pair of
 * CLIs: a plain terminal (this machine's own shell, no agent around it) sits in
 * the same menu, and a CLI registered later appears here without a change on
 * this side. An entry that is not installed stays listed but disabled, so the
 * absence is visible and explains itself rather than silently not being there.
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
        aria-label={`What should run ${direction === "right" ? "beside" : "below"} ${paneName}?`}
        data-testid={`pane-split-menu-${direction}-${paneName}`}
        className="absolute right-2 top-full z-50 mt-1 w-60 rounded-lg border border-border bg-card p-1 shadow-xl"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onDismiss();
        }}
      >
        <p className="px-2 py-1.5 text-[11px] uppercase tracking-wider text-muted-foreground">
          {direction === "right" ? "Open beside — what?" : "Split below — what?"}
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
            className="flex w-full items-start justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
          >
            <span className="min-w-0">
              <span className="block truncate">{agent.displayName}</span>
              {/* What this choice actually opens — "no agent, just a prompt"
                  is the difference a user needs before clicking, and it is the
                  entry's own words rather than a name this menu recognises. */}
              {agent.description && (
                <span className="block truncate text-[11px] text-muted-foreground">
                  {agent.description}
                </span>
              )}
            </span>
            {!agent.installed && (
              <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">
                {agent.kind === "shell" ? "no shell here" : "not installed"}
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
