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
 *    loses the whole session. So the connect effect depends on pane identity,
 *    restart intent and one-way initial geometry readiness — never live
 *    appearance state.
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
 *   formats for a width the pane does not have. Worse, xterm keeps drawing the
 *   real font's wider glyphs into that too-narrow grid, so the text smears
 *   across its own columns. Neither `document.fonts.ready` nor a re-fit fixes
 *   this — see `@/lib/terminalFont`, which does.
 * * **Renderer.** The DOM renderer draws each cell as an element; with a TUI
 *   redrawing on every keystroke that is both slow and subtly misaligned. The
 *   canvas renderer draws on a grid, which is what a terminal actually is.
 */
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { CanvasAddon } from "@xterm/addon-canvas";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import "@xterm/xterm/css/xterm.css";
import {
  Check,
  Columns2,
  Loader2,
  Maximize2,
  Minimize2,
  Paperclip,
  Pencil,
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
import {
  AgentPickerMenu,
  offersAgentChoice,
  type SplitAgentChoice,
} from "./AgentPicker";
import { describeExit, explainExit } from "./paneExit";
import { PaneRecap } from "./PaneRecap";
import { attachToTerminal } from "@/lib/agenticIdeApi";
import type { RecapReason, RecapSource } from "@/lib/agenticIdeApi";
import { attachTerminalBridge } from "@/lib/editActions";
import { robustPaste } from "@/lib/clipboard";
import {
  TERMINAL_FONT_STACK,
  alignTerminalCells,
  syncTerminalFont,
} from "@/lib/terminalFont";
import {
  activateTerminalLink,
  TERMINAL_OSC_LINK_HANDLER,
} from "@/lib/terminalLinks";
import { installPasteBridge } from "./terminalPaste";
import { createKeyEventChain } from "./terminalKeyChain";
import { installNewlineBridge } from "./terminalNewline";
import { cancelPaneReflow, queuePaneReflow } from "./paneReflowQueue";
import {
  boxOnScreen,
  OffscreenBuffer,
  OFFSCREEN_MARGIN_PX,
  PARKED_RECHECK_MS,
} from "./offscreenBuffer";
import { installQuerySuppression } from "./terminalQueries";
import { PaneScrollRail } from "./PaneScrollRail";
import {
  openPaneSocket,
  type PaneSocket,
  type PromptDelivery,
} from "./paneSocket";
import { PromptReceipt } from "./PromptReceipt";
import { PromptHistoryButton } from "./PromptHistoryButton";

/**
 * How old a delivery may be and still raise its receipt on a fresh connection.
 *
 * Only applies to the receipt recovered from the HANDSHAKE — a prompt arriving
 * live always shows one. Half an hour comfortably covers "I looked away and
 * came back" while keeping a reopened workspace from re-announcing deliveries
 * the user long since watched play out.
 */
const RECEIPT_MAX_AGE_MS = 30 * 60 * 1000;

/**
 * How long a call-sign may be — the same cap the backend enforces
 * (`MAX_TERMINAL_NAME`), so the field stops where the save would have failed
 * rather than letting somebody type a name that comes back rejected.
 */
const MAX_TERMINAL_NAME = 40;

export type PaneStatus = "connecting" | "live" | "exited" | "error";

/**
 * Is the whole document out of sight — window behind another, minimized, or in
 * a background tab?
 *
 * Read through a function rather than inlined so a pane behaves the same in a
 * test environment that ships no `document`, and so the two places that must
 * agree about it cannot drift apart.
 */
function documentHidden(): boolean {
  return typeof document !== "undefined" && document.hidden === true;
}

/**
 * A coding CLI a split may start.
 *
 * Re-exported rather than declared here: the same list is offered by the chat
 * view's rail and by an empty workspace, so it belongs to the picker they all
 * share (see `AgentPicker`). Kept exported from this module because that is
 * where every caller already imports it from.
 */
export type { SplitAgentChoice };

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
  /** Number shown beside the pane header's prompt-history icon. */
  promptCount?: number;
  appearance: TerminalAppearance;
  fontSize: number;
  /**
   * Has the grid measured the pane's final opening geometry?
   *
   * Area-aware layouts cannot know their band count until the container has a
   * height. Opening the PTY during the width-only first pass attaches it at one
   * size and immediately moves it to another, while its replay is still being
   * parsed. Omitted for standalone uses, which already render at a fixed size.
   */
  geometryReady?: boolean;
  /** Highlight this pane as the prompt target. */
  focused?: boolean;
  /** Is this pane currently visible on the chat stage? */
  active?: boolean;
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
  /**
   * Give this pane another call-sign.
   *
   * Answers whether the name was accepted, so the editor can stay open with the
   * text still in it when the workspace already has a pane by that name.
   * Omitted, the header shows no rename control at all rather than one that
   * would do nothing.
   */
  onRename?: (name: string) => Promise<boolean>;
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
  recapMeta,
  recapActions,
  accountLabel,
  promptCount = 0,
  appearance,
  fontSize,
  geometryReady = true,
  focused = false,
  active = true,
  onFocus,
  onStatus,
  maximized = false,
  onToggleMaximize,
  onSplit,
  agents,
  onRename,
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
  const terminalRegionRef = useRef<HTMLDivElement | null>(null);
  const terminalRegionId = useId();
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  // Lets the font-size effect trigger a REAL resize (xterm + the terminal
  // process together) without reaching into the connect effect's socket.
  const resizeRef = useRef<(() => void) | null>(null);
  const claimResizeRef = useRef<(() => void) | null>(null);
  const visibilityRef = useRef<{ show: () => void; park: () => void } | null>(null);
  const statusRef = useRef<PaneStatus>("connecting");
  // Mirrored into state purely so the header can show/hide the restart button;
  // it transitions a handful of times per pane, never per output chunk.
  const [visibleStatus, setVisibleStatus] = useState<PaneStatus>("connecting");
  /*
   * Has this pane's agent drawn anything yet?
   *
   * A pane opened by voice is an EMPTY BLACK RECTANGLE for several seconds: the
   * grid renders it the moment the workspace state arrives, and the CLI inside
   * it only paints once the socket is up, a cold-start slot is free and the
   * process has booted — measured at 2.6-2.8 s on a healthy machine, longer
   * while the grid is busy relaying itself out. Nothing said so, so "open two
   * more terminals" looked like it had silently failed and the panes were
   * closed and asked for again (maintainer report 2026-07-28).
   *
   * Flipped by the first byte the agent writes, never back — a pane that has
   * painted once is a pane the user can read, whatever its socket does later.
   * Reconnects therefore stay quiet: the replayed screen is already there.
   */
  const [painted, setPainted] = useState(false);
  // The terminal itself stays in a ref. This small epoch only tells the scroll
  // rail that the ref now points at a new instance after a restart.
  const [terminalEpoch, setTerminalEpoch] = useState(0);
  /**
   * The delivery this pane is currently showing a receipt for, if any.
   *
   * Held here rather than derived from the terminal's contents because the
   * terminal is precisely what cannot be trusted to show it — see
   * ./PromptReceipt for the four ways a delivered prompt fails to reach the
   * screen. Fed from two independent sources so that neither being unavailable
   * loses the proof: the socket's `prompt` frame (instant, lossy) and the
   * pane's `last_prompt_at` in the workspace state (durable, one poll late).
   */
  const [receipt, setReceipt] = useState<PromptDelivery | null>(null);
  /** The delivery the user has already waved away; never shown again. */
  const [dismissedAt, setDismissedAt] = useState<number | null>(null);
  /** Briefly true right after a delivery — draws the eye to the right pane. */
  const [justDelivered, setJustDelivered] = useState(false);
  // Latest callbacks/appearance without re-running the connect effect.
  const onStatusRef = useRef(onStatus);
  const onAttachErrorRef = useRef(onAttachError);
  const initialRef = useRef({ appearance, fontSize });
  // The ground this pane draws on, as of NOW — the socket tells the backend, so
  // that the agent's CLI is answered with the colours it is actually drawing on
  // when it asks. Read at connect time, hence a ref rather than the prop: the
  // connect effect must not re-run when the user flips the theme.
  const appearanceRef = useRef(appearance);
  const activeRef = useRef(active);
  const focusedRef = useRef(focused);
  // Read by the connect effect's resize scheduler, which is built once and
  // therefore cannot see the prop change.
  const layoutBusyRef = useRef(layoutBusy);
  onStatusRef.current = onStatus;
  onAttachErrorRef.current = onAttachError;
  appearanceRef.current = appearance;
  activeRef.current = active;
  focusedRef.current = focused;
  layoutBusyRef.current = layoutBusy;

  useEffect(() => {
    if (!geometryReady) return;
    const container = containerRef.current;
    if (!container) return;

    // A restart builds a brand-new terminal on a blank screen, so the pane owes
    // the user the same "it is coming up" answer it owed on its first mount.
    setPainted(false);

    const term = new Terminal({
      convertEol: false,
      // Shared with the measurement in ./../../lib/terminalFont: a pane that
      // measured a different stack from the one it draws with is the bug that
      // module exists to prevent.
      fontFamily: TERMINAL_FONT_STACK,
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
      // Plain clicks belong to text selection. xterm otherwise treats an
      // ordinary click on an OSC-8 link as navigation and shows a native
      // warning dialog inside the desktop WebView.
      linkHandler: TERMINAL_OSC_LINK_HANDLER,
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
    term.loadAddon(new WebLinksAddon(activateTerminalLink));
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
    setTerminalEpoch((current) => current + 1);
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
      {
        attachCustomKeyEventHandler: keys.add,
        paste: (text) => term.paste(text),
      },
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
    let paneVisible = activeRef.current;
    const offscreen = new OffscreenBuffer();
    /** When this pane last measured itself while parked (see `recheckParked`). */
    let parkedCheckedAt = 0;
    /** The pending deadline flush, if this pane is holding anything. */
    let holdTimer: number | undefined;

    const cancelHoldTimer = () => {
      if (holdTimer === undefined) return;
      window.clearTimeout(holdTimer);
      holdTimer = undefined;
    };

    /**
     * Write what is held, whether or not this pane believes it is watched.
     *
     * The deadline half of parking (see ./offscreenBuffer): being wrong about
     * visibility must cost a coalesced write, never a screen that stopped.
     */
    const flushHeld = () => {
      cancelHoldTimer();
      const held = offscreen.drain();
      if (held) term.write(held);
    };

    /** Make sure the held output has a flush coming, without moving one nearer. */
    const armHoldTimer = () => {
      if (holdTimer !== undefined) return;
      const due = offscreen.dueIn();
      if (due === null) return;
      holdTimer = window.setTimeout(() => {
        holdTimer = undefined;
        if (disposed) return;
        flushHeld();
      }, due);
    };

    const showPane = () => {
      if (!activeRef.current) return;
      if (paneVisible) return;
      paneVisible = true;
      flushHeld();
    };

    const parkPane = () => {
      paneVisible = false;
      // The next chunk of output measures immediately rather than waiting out
      // an interval that started before this pane was even parked.
      parkedCheckedAt = 0;
    };
    const visibility = { show: showPane, park: parkPane };
    visibilityRef.current = visibility;

    /**
     * Un-park this pane if it is genuinely on screen — measured, not remembered.
     *
     * The observer below reports CHANGES, and a pane can be left parked in a
     * state no change ever leads out of (see `boxOnScreen`). This is the way
     * back, and it is called from the two moments that produce exactly that
     * state: the document becoming visible again, and the pane being resized.
     */
    const revealIfOnScreen = () => {
      if (!activeRef.current) return;
      if (paneVisible) return;
      const box = container.getBoundingClientRect();
      const viewport = {
        width: window.innerWidth || 0,
        height: window.innerHeight || 0,
      };
      if (boxOnScreen(box, viewport)) showPane();
    };

    /**
     * A parked pane that is still being talked to, asking the only question
     * that matters: can the user see me right now?
     *
     * The observer answers that for every state it reports. The failure this
     * guards is the state it does NOT report — and the two known ones
     * (`visibilitychange`, resize) were found one live incident at a time, so
     * assuming they are the last two is how the next one costs another
     * afternoon. The symptom is always identical and always severe: Jarvis
     * types a prompt into a pane, the agent starts work, and the user watches
     * an empty rectangle and concludes nothing was sent (reported 2026-07-27,
     * where the prompt reached the agent 1.4 s BEFORE it was announced and the
     * pane showed its boot screen for another minute).
     *
     * Throttled, because output arrives in frames and a rectangle measurement
     * forces layout: at most one per {@link PARKED_RECHECK_MS} per parked pane,
     * and none at all for a parked pane whose agent has gone quiet — nobody
     * misses a screen that is not changing.
     */
    const recheckParked = () => {
      const now = Date.now();
      if (now - parkedCheckedAt < PARKED_RECHECK_MS) return;
      parkedCheckedAt = now;
      revealIfOnScreen();
    };

    // Everything this pane draws goes through here, not just the agent's
    // stream: an exit banner written straight to xterm while output is parked
    // would appear ABOVE the output it is supposed to follow.
    const writeToPane = (text: string, afterWrite?: () => void) => {
      if (!text) return;
      // The first byte is what retires the "starting" overlay — and it is taken
      // HERE rather than at the socket, so a pane whose output is parked
      // offscreen still counts as painted. It has a screen; nobody is looking
      // at it. Cheap to call per chunk: React bails out on an unchanged value.
      setPainted(true);
      if (!paneVisible) recheckParked();
      if (paneVisible) {
        term.write(text, afterWrite);
        return;
      }
      offscreen.push(text);
      // A pane that has parked all it may hold WRITES rather than forgets. An
      // agent's TUI is drawn by relative cursor moves, so a stream that lost
      // its front does not repair itself — the pane would come back showing
      // the spinner row it rewrote last and blank rows where its prompt box
      // belongs. Parsing into a surface nobody is painting is the cheap half
      // of what parking avoids; a permanently broken screen is not.
      //
      // The same answer, for the same reason, once it has held long enough:
      // this pane's belief that nobody is watching may simply be wrong, and
      // that must cost a coalesced write rather than a frozen terminal. The
      // timer covers the case this branch cannot — an agent that goes quiet
      // right after saying something would otherwise hold that last word for
      // as long as it stays quiet.
      if (offscreen.full || offscreen.stale()) {
        flushHeld();
        return;
      }
      armHoldTimer();
    };

    /**
     * Draw the screen this pane is re-joining — on a terminal cleared first.
     *
     * A replay is not a big chunk of output, it is a REBUILD: the server hands
     * over the raw bytes that drew the screen the agent is looking at, so that
     * a pane which was away — a reconnect, a backend restart, a workspace
     * switch — comes back showing the interface rather than a blank rectangle.
     *
     * Writing it onto whatever is already here draws that interface a second
     * time over the copy still on screen, and the two do not stack tidily. An
     * Ink TUI (Claude Code, Codex) skips unchanged cells with cursor moves
     * instead of overwriting them with spaces, so the first copy shows THROUGH
     * the second, character by character: "plus everything new" came back as
     * "plueverythingwnew" (reported 2026-07-29, three panes, unreadable). Nor does
     * it heal — the agent repaints its own visible rows and never the
     * scrollback above them, so every reconnect added another layer.
     *
     * `reset()` rather than `clear()`: the replay re-states the screen modes
     * the agent negotiated (alternate screen, mouse tracking), and those have
     * to start from a known state or the pane inherits half of the old one.
     */
    const replayToPane = (text: string) => {
      if (!text) return;
      // Parked output belongs to the screen this replay REPLACES, and it was
      // captured before it. Written afterwards it would paint the older screen
      // over the newer one; written before, it would be reset away regardless.
      // The deadline goes with it — a flush firing after this would draw the
      // screen this replay just replaced.
      cancelHoldTimer();
      offscreen.drain();
      term.reset();
      // Through the ordinary path, so a replay arriving while nobody is looking
      // is parked and un-parked by the same rules as anything else — and so it
      // counts as the pane having painted.
      writeToPane(text, () => {
        // A selected chat returning from another session opens on the live
        // prompt, never several screens above it.
        if (activeRef.current) term.scrollToBottom?.();
      });
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

    const viewerMayOwn = () =>
      activeRef.current &&
      !documentHidden() &&
      (typeof document === "undefined" ||
        typeof document.hasFocus !== "function" ||
        document.hasFocus());

    const sendResize = (claimOwner = false) => {
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
      // Resizing a pane is the other half of the un-park story. Maximizing one,
      // dragging a seam, changing the font — all of them are a user opening up
      // a pane to READ it, and the pane it opens must not be a parked one still
      // holding its agent's screen. BEFORE the early return below, deliberately:
      // whether a size still needs announcing says nothing about whether the
      // pane is being looked at, and a pane that came back at exactly the size
      // it left would otherwise stay dark. The measurement inside only un-parks
      // a pane that really is on screen.
      revealIfOnScreen();
      const size = { cols: term.cols, rows: term.rows };
      // Already delivered and unchanged: the fit above was the whole job.
      // Re-announcing a size makes the agent on the other end redraw its
      // entire screen, and a pane refits several times per settling layout.
      if (
        !claimOwner &&
        sentSize &&
        sentSize.cols === size.cols &&
        sentSize.rows === size.rows
      ) {
        return;
      }
      if (socket?.send({ t: claimOwner ? "claim" : "r", ...size })) sentSize = size;
    };
    resizeRef.current = sendResize;
    const claimResize = () => {
      if (viewerMayOwn()) sendResize(true);
    };
    claimResizeRef.current = claimResize;

    socket = openPaneSocket(
      {
        name,
        workspaceId,
        cols: term.cols || 80,
        rows: term.rows || 24,
        appearance: appearanceRef.current,
        claimOwner: viewerMayOwn(),
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
          requestAnimationFrame(() => sendResize());
        },
        onOutput: (text) => writeToPane(text),
        onReplay: (text) => replayToPane(text),
        /**
         * A prompt just landed in this pane — make that impossible to miss.
         *
         * Three things, and the order matters. The pane is un-parked FIRST:
         * whatever the observer believes, output held back now is output the
         * user will not see while they are looking straight at the receipt
         * telling them it arrived. Then it is scrolled into the viewport,
         * because a receipt drawn on a pane two screens down is no better than
         * no receipt. Only then does it flash, which is the part that is merely
         * nice.
         *
         * None of this depends on the agent echoing anything, which is the
         * whole point: this path is what remains true when the terminal is a
         * black rectangle.
         */
        onPrompt: (delivery) => {
          if (disposed) return;
          if (activeRef.current) showPane();
          setReceipt(delivery);
          setJustDelivered(true);
          window.setTimeout(() => setJustDelivered(false), 2_000);
          if (activeRef.current) {
            try {
              container.scrollIntoView({ block: "nearest", behavior: "auto" });
            } catch {
              // Older engines take no options object; position is a nicety and
              // the receipt is legible wherever the pane happens to sit.
              container.scrollIntoView();
            }
          }
        },
        onReady: ({ resumed, reattached, lastPrompt }) => {
          troubleShown = null;
          // What this pane was told BEFORE this socket existed. A reload, a
          // reconnect or a second window opened afterwards would otherwise
          // show no receipt at all for a prompt that really was delivered —
          // and that viewer is exactly the one with reason to doubt it.
          //
          // Bounded by age, because "durable" and "permanent" are different
          // requirements. The receipt answers a question that is asked in the
          // minutes after a delivery ("did that actually go?"); re-raising
          // yesterday's every time a workspace is reopened would train the
          // user to close it unread, which is how the next real one gets
          // missed. Older deliveries stay readable on demand — the pane's
          // state keeps them, and `GET /terminals/{name}/prompt` returns the
          // text in full.
          if (
            lastPrompt?.at !== null &&
            lastPrompt !== null &&
            Date.now() - lastPrompt.at * 1000 < RECEIPT_MAX_AGE_MS
          ) {
            setReceipt(lastPrompt);
          }
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
          if (
            activeRef.current &&
            focusedRef.current &&
            (document.activeElement === null || document.activeElement === document.body)
          ) {
            term.focus();
          }
        },
        onExit: (code) => {
          report("exited", explainExit(code));
          writeToPane(
            `\r\n\x1b[33m${describeExit(displayName, code)}\x1b[0m\r\n`,
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
    // the display font arrives afterwards, the cell width changes underneath the
    // already-computed column count: the agent formats its output for a width
    // the pane does not have, and every glyph is painted wider than the cell it
    // owns until the line has drifted a full column out of its grid. Both are
    // the reported symptom. See ./../../lib/terminalFont for why waiting on
    // `document.fonts.ready` and re-fitting cannot fix either.
    const disposeFontSync = syncTerminalFont(term, () => {
      if (disposed) return;
      term.clearTextureAtlas?.();
      sendResize();
    });

    // Resizes are coalesced: dragging a split or the window fires the observer
    // dozens of times a second, and every fit both reflows xterm's buffer and
    // sends a PTY resize the agent redraws for. Unthrottled that is the visible
    // flicker while resizing.
    let resizeTimer: number | undefined;
    // The queued form of `sendResize`, kept as ONE stable function so the queue
    // can recognise this pane's pending reflow — both to skip a duplicate and
    // to drop it when the pane goes away. See ./paneReflowQueue for why panes
    // must not reflow in the same frame as each other.
    const reflow = () => sendResize();
    const scheduleResize = () => {
      // Nothing at all while a seam or the prompt bar is being dragged: the
      // pane is mid-gesture, every size it could measure is about to be
      // replaced, and the fit would cost the frame the drag needs. The pane is
      // refitted from the `layoutBusy` effect the moment the drag ends.
      if (layoutBusyRef.current) return;
      if (resizeTimer !== undefined) window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        resizeTimer = undefined;
        queuePaneReflow(reflow);
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
          if (onScreen) {
            showPane();
            return;
          }
          // A hidden DOCUMENT is not an off-screen pane, and treating the two
          // as one is what left panes black for minutes at a time. While the
          // window is behind another one, minimized, or its tab in the
          // background, EVERY element here reports intersecting nothing — and
          // bringing the window back changes no geometry, so no further
          // callback ever arrives to undo it. Parking on that verdict meant a
          // pane opened while the user was looking elsewhere never drew again
          // (measured 2026-07-27: a workspace spawned by voice came back with
          // its new panes empty, and a chatty CLI took ~3 minutes to appear —
          // exactly how long 256 KB of output takes to force the buffer out).
          //
          // Parking is also pointless in that state: a hidden document paints
          // nothing, so there is no frame budget being defended. The whole
          // reason this exists is panes competing for the main thread WHILE the
          // user watches another one.
          if (!documentHidden()) parkPane();
        },
        { rootMargin: `${OFFSCREEN_MARGIN_PX}px` },
      );
      io.observe(container);
    }

    /*
     * The window came back — check whether this pane is on screen now.
     *
     * The observer cannot answer this on its own: showing a window again moves
     * neither geometry nor scroll position, which are the only things that make
     * it recompute. Without this a pane parked while the app sat behind an
     * editor stayed parked after the user switched back to it.
     */
    const onDocumentVisible = () => {
      if (documentHidden()) return;
      revealIfOnScreen();
      claimResize();
    };
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onDocumentVisible);
    }
    window.addEventListener("focus", claimResize);

    return () => {
      disposed = true;
      // Before the terminal is disposed below: a deadline flush one tick later
      // would write into it after it is gone.
      cancelHoldTimer();
      if (resizeTimer !== undefined) window.clearTimeout(resizeTimer);
      // A queued reflow outlives the pane by up to a frame, and would then fit
      // a disposed terminal inside a detached element.
      cancelPaneReflow(reflow);
      window.removeEventListener("resize", scheduleResize);
      window.removeEventListener("focus", claimResize);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onDocumentVisible);
      }
      ro.disconnect();
      io?.disconnect();
      disposeFontSync();
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
      claimResizeRef.current = null;
      if (visibilityRef.current === visibility) visibilityRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see file header:
    // appearance/fontSize must NOT rebuild the pane (it would kill the agent).
  }, [name, restartToken, geometryReady]);

  /*
   * A chat-stage switch changes a pane from `display:none` to the full canvas
   * in one commit. Refit and follow the live tail before that frame paints;
   * hidden siblings stay parked even when a prompt is addressed to them.
   */
  useLayoutEffect(() => {
    if (!active) {
      visibilityRef.current?.park();
      return;
    }
    const reveal = () => {
      visibilityRef.current?.show();
      resizeRef.current?.();
      claimResizeRef.current?.();
      termRef.current?.scrollToBottom?.();
    };
    reveal();
    const frame = requestAnimationFrame(reveal);
    return () => cancelAnimationFrame(frame);
  }, [active]);

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
    // A new size is a new glyph advance, and so a new fraction of a pixel for
    // the canvas renderer to floor away. Re-align before the fit below, or the
    // pane spends this size with its glyphs overhanging their cells.
    alignTerminalCells(term);
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
      onMouseDown={() => {
        onFocus?.();
        claimResizeRef.current?.();
      }}
      {...dragHandlers}
      className={cn(
        // One quiet border per pane; the focused one carries the workspace's
        // only standing accent. The old per-pane drop shadows made a grid of
        // twelve read as twelve floating cards — a tiling terminal is a wall,
        // and a wall needs edges, not elevation.
        "relative flex h-full w-full flex-col overflow-hidden rounded-lg border transition-shadow",
        focused && "border-primary/60 shadow-[0_0_0_1px_hsl(var(--primary)/0.3)]",
        dragging && "border-primary shadow-[0_0_0_2px_hsl(var(--primary)/0.5)]",
        // A prompt just landed here. Two seconds of ring, for the one job the
        // receipt below cannot do: telling the user WHICH pane out of eight to
        // look at. Colour and shadow only — nothing moves, because a pane that
        // jumps while an agent is drawing into it is worse than a quiet one.
        justDelivered &&
          "border-primary shadow-[0_0_0_2px_hsl(var(--primary)/0.6),0_0_28px_-4px_hsl(var(--primary)/0.55)]",
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
        workspaceId={workspaceId}
        dead={visibleStatus === "exited" || visibleStatus === "error"}
        onRestart={onRestart}
        onArrangeStart={onArrangeStart}
        arranging={arranging}
        name={name}
        displayName={displayName}
        recap={recap}
        recapDetail={recapDetail}
        recapMeta={recapMeta}
        recapActions={recapActions}
        accountLabel={accountLabel}
        promptCount={promptCount}
        appearance={appearance}
        focused={focused}
        maximized={maximized}
        onToggleMaximize={onToggleMaximize}
        onSplit={onSplit}
        agents={agents}
        onRename={onRename}
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
        ref={terminalRegionRef}
        id={terminalRegionId}
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
        <PaneScrollRail
          name={name}
          controlsId={terminalRegionId}
          regionRef={terminalRegionRef}
          hostRef={containerRef}
          terminalRef={termRef}
          epoch={terminalEpoch}
          appearance={appearance}
          onFocus={onFocus}
        />
        {/*
          The pane says it is starting, instead of being a black rectangle.

          Only until the agent's first byte, and only while nothing has gone
          wrong: an exited or unreachable pane has its own, more specific
          answer in the header and must not be covered by a hopeful spinner.
          `pointer-events-none` throughout — the terminal underneath keeps
          every click and keystroke, so typing into a pane that is still
          booting behaves exactly as it did before.
        */}
        {/*
          Proof that this pane was handed a prompt, drawn by the app rather
          than read out of the agent's screen.

          Sits INSIDE the terminal region so it points at the pane it is
          talking about, and above the scrollbar overlay so a fleet of panes
          cannot bury it. It is the answer to the failure this whole file keeps
          circling: a delivery that really happened, on a pane that showed
          nothing, told to a user who then had every reason to believe Jarvis
          had invented it. See ./PromptReceipt.
        */}
        {receipt && receipt.at !== null && receipt.at !== dismissedAt && (
          <PromptReceipt
            key={receipt.at}
            terminal={name}
            workspaceId={workspaceId}
            at={receipt.at}
            preview={receipt.text}
            chars={receipt.chars}
            submitted={receipt.submitted}
            onDismiss={() => setDismissedAt(receipt.at)}
          />
        )}
        {!painted && visibleStatus === "connecting" && (
          <div
            data-testid={`agentic-pane-starting-${name}`}
            className="pointer-events-none absolute inset-0 flex items-center justify-center"
          >
            <div
              className="flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm text-muted-foreground"
              style={{ background: chrome.shell, borderColor: chrome.border }}
            >
              <Loader2 className="h-4 w-4 animate-spin text-primary" />
              <span>Starting {displayName}…</span>
            </div>
          </div>
        )}
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
  workspaceId,
  name,
  displayName,
  recap,
  recapDetail,
  recapMeta,
  recapActions,
  accountLabel,
  promptCount,
  appearance,
  focused,
  maximized,
  onToggleMaximize,
  onSplit,
  agents,
  onRename,
  onClose,
  splitDisabled,
  dead,
  onRestart,
  onArrangeStart,
  arranging = false,
}: {
  workspaceId?: string;
  name: string;
  displayName: string;
  recap?: string;
  recapDetail?: string;
  recapMeta?: PaneRecapMeta;
  recapActions?: PaneRecapActions;
  accountLabel?: string | null;
  promptCount: number;
  appearance: TerminalAppearance;
  focused: boolean;
  maximized: boolean;
  onToggleMaximize?: () => void;
  onSplit?: (direction: SplitDirection, agent?: string) => void;
  agents?: SplitAgentChoice[];
  onRename?: (name: string) => Promise<boolean>;
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
  // The call-sign editor: null while the badge is just a badge, otherwise the
  // text being typed. Empty string is a real state (the field was cleared), so
  // "is it open" cannot be read off the text — hence null rather than "".
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const commitRename = async () => {
    const wanted = (draft ?? "").trim();
    if (!wanted || wanted === name) {
      setDraft(null);
      return;
    }
    setSaving(true);
    // Kept open on a refusal — a duplicate call-sign is a name to CHANGE, and
    // throwing the typing away would make the user retype the part that was
    // fine. The grid says what went wrong.
    const accepted = await onRename?.(wanted);
    setSaving(false);
    if (accepted !== false) setDraft(null);
  };

  // With one installed CLI there is nothing to pick, so the button splits
  // straight away — a menu with a single entry is a click tax, not a choice.
  const choices = agents ?? [];
  const offersChoice = offersAgentChoice(choices);

  const startSplit = (direction: SplitDirection) => {
    if (offersChoice)
      setPicking((current) => (current === direction ? null : direction));
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
              if (target?.closest("button, a, input, [role='menuitem']"))
                return;
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
        // No tinted strip of its own: the header shares the terminal's ground
        // and the border underneath is enough to say where the output begins.
        // Twelve tinted bands across the workspace were twelve horizontal
        // stripes the eye had to skip on the way to the text that matters.
        "group/header relative flex items-center justify-between gap-1.5 border-b px-2 py-0.5",
        onArrangeStart && (arranging ? "cursor-grabbing" : "cursor-grab"),
      )}
      style={{
        borderColor: PANE_CHROME[appearance].border,
        // Claims the touch gesture for the drag. Without it a touch that starts
        // on the header scrolls the workspace instead of lifting the pane, and
        // the drag never begins at all.
        touchAction: onArrangeStart ? "none" : undefined,
      }}
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        {draft !== null ? (
          /*
           * The call-sign, being typed.
           *
           * A form rather than a bare input so Enter saves the way it does in
           * every other name field in the app, and Escape closes it — the two
           * keys somebody renaming a pane will reach for without looking.
           */
          <form
            className="flex shrink-0 items-center gap-1"
            onSubmit={(event) => {
              event.preventDefault();
              void commitRename();
            }}
          >
            <input
              autoFocus
              value={draft}
              maxLength={MAX_TERMINAL_NAME}
              disabled={saving}
              aria-label={`Rename ${name}`}
              data-testid={`pane-rename-input-${name}`}
              onFocus={(event) => event.currentTarget.select()}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") setDraft(null);
                // The pane underneath is a live terminal listening for keys.
                // Without this, typing a name also types into the agent.
                event.stopPropagation();
              }}
              className={cn(
                "w-32 rounded-md px-2 py-0.5 font-display text-[13px] font-semibold tracking-tight outline-none",
                "border border-primary/50 focus:border-primary disabled:opacity-60",
              )}
              style={{
                color: light ? "#2b2b33" : "#e8e8ec",
                background: light ? "rgba(0,0,0,0.04)" : "rgba(255,255,255,0.06)",
              }}
            />
            <button
              type="submit"
              disabled={saving || !draft.trim()}
              aria-label={`Save name for ${name}`}
              data-testid={`pane-rename-save-${name}`}
              className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-primary hover:bg-primary/15 disabled:opacity-40"
            >
              {saving ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Check className="h-3 w-3" />
              )}
            </button>
            <button
              type="button"
              disabled={saving}
              aria-label={`Cancel renaming ${name}`}
              onClick={() => setDraft(null)}
              className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted"
            >
              <X className="h-3 w-3" />
            </button>
          </form>
        ) : (
          <>
            <span
              // Double-click is the gesture every tab strip and file manager
              // already uses for "rename this", so it is offered here too — but
              // it is never the only way in, because nothing on screen advertises
              // it. The pencil beside it is what makes the feature findable.
              onDoubleClick={onRename ? () => setDraft(name) : undefined}
              title={onRename ? `${name} — double-click to rename` : undefined}
              // The focused pane's call-sign is the workspace's one standing
              // accent; every other name is plain text. A filled badge on all
              // twelve panes marked nothing, because a marker everyone wears
              // is a uniform.
              className={cn(
                "shrink-0 rounded-md px-1.5 py-0.5 font-display text-[13px] font-semibold tracking-tight",
                focused ? "bg-primary/20 text-primary" : "",
              )}
              style={focused ? undefined : { color: light ? "#2b2b33" : "#e8e8ec" }}
            >
              {name}
            </span>
            {onRename && (
              <button
                type="button"
                aria-label={`Rename ${name}`}
                title={`Rename ${name}`}
                data-testid={`pane-rename-${name}`}
                onClick={() => setDraft(name)}
                onMouseDown={(event) => event.stopPropagation()}
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded transition-opacity",
                  "opacity-0 focus-visible:opacity-100 group-hover/header:opacity-100",
                  light
                    ? "text-[#6b6b73] hover:bg-black/10"
                    : "text-[#9a9aa5] hover:bg-white/10",
                )}
              >
                <Pencil className="h-3 w-3" />
              </button>
            )}
          </>
        )}
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

      {/* Restart is an EVENT, not furniture — a dead agent must announce
          itself whether or not anyone hovers, so it sits outside the cluster
          that hides. */}
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
          className="mr-1 flex shrink-0 items-center gap-1 rounded bg-primary/20 px-2 py-0.5 text-[11px] font-medium text-primary transition-colors hover:bg-primary/30"
        >
          <RotateCcw className="h-3 w-3" />
          Restart
        </button>
      )}

      {/* Pane actions appear where the eye already is: on the pane under the
          pointer, on the focused pane, and while one of their menus is open.
          Five buttons on every header of a twelve-pane wall were sixty
          controls nobody was using at once — the redesign shows each pane's
          five exactly when that pane is the one being worked. They stay in
          the DOM throughout (opacity, not display), so keyboard focus and
          tests reach them regardless. */}
      <div
        className={cn(
          "flex shrink-0 items-center gap-0.5 transition-opacity",
          focused || maximized || picking !== null
            ? "opacity-100"
            : "opacity-0 focus-within:opacity-100 group-hover/header:opacity-100",
        )}
      >
        <PromptHistoryButton
          terminal={name}
          workspaceId={workspaceId}
          count={promptCount}
          light={light}
        />
        <PaneAction
          label={maximized ? `Restore ${name}` : `Maximize ${name}`}
          testId={`pane-maximize-${name}`}
          light={light}
          onClick={onToggleMaximize}
        >
          {maximized ? (
            <Minimize2 className="h-3.5 w-3.5" />
          ) : (
            <Maximize2 className="h-3.5 w-3.5" />
          )}
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
        <AgentPickerMenu
          title={
            picking === "right" ? "Open beside — what?" : "Split below — what?"
          }
          ariaLabel={`What should run ${picking === "right" ? "beside" : "below"} ${name}?`}
          agents={choices}
          testId={`pane-split-menu-${picking}-${name}`}
          itemTestId={(agent) => `pane-split-${picking}-${name}-${agent}`}
          className="right-2 top-full mt-1"
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

/*
 * The pane badge used to live here and reported the SOCKET: "live" for any pane
 * with a working pipe, which is nearly all of them nearly all of the time. It
 * moved to ./PaneActivityPill, which answers the question people were actually
 * reading it for — is this agent still working — and still reports the pipe in
 * the three cases where the pipe is the news.
 */
