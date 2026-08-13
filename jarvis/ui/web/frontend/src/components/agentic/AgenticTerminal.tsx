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
import { createPortal } from "react-dom";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { CanvasAddon } from "@xterm/addon-canvas";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import "@xterm/xterm/css/xterm.css";
import {
  BookOpenText,
  Check,
  Loader2,
  Maximize2,
  Minimize2,
  Paperclip,
  Pencil,
  RotateCcw,
  X,
} from "lucide-react";
import { SplitBelowIcon, SplitRightIcon } from "./splitIcons";
// A leaf module with no DOM and no terminal in it, which is the point: the
// wizard quotes this same number before any pane exists — see ./layout.
import { WORKABLE_COLS } from "./layout";
import { cn } from "@/lib/utils";
import {
  MINIMUM_CONTRAST_RATIO,
  PANE_BRAND,
  PANE_CHROME,
  themeFor,
  type TerminalAppearance,
} from "./terminalThemes";
import { clearTuiCanvasFill } from "./terminalGlass";
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
import { PaneActivityPill, paneActivityLabel } from "./PaneActivityPill";
import { PaneRecap } from "./PaneRecap";
import { attachToTerminal } from "@/lib/agenticIdeApi";
import type { PaneActivity, RecapReason, RecapSource } from "@/lib/agenticIdeApi";
import { attachTerminalBridge } from "@/lib/editActions";
import { robustCopy, robustPaste } from "@/lib/clipboard";
import {
  TERMINAL_FONT_STACK,
  alignTerminalCells,
  syncTerminalFont,
} from "@/lib/terminalFont";
import {
  createTerminalLinkActivator,
  createTerminalOscLinkHandler,
  TerminalPathLinksAddon,
} from "@/lib/terminalLinks";
import { installPasteBridge } from "./terminalPaste";
import { installCopyBridge } from "./terminalCopy";
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
import {
  bindTerminalScrollRegion,
  captureWheelForTerminalHistory,
} from "./terminalScrollSurface";
import {
  openPaneSocket,
  type PaneSocket,
  type PromptDelivery,
} from "./paneSocket";
import { PromptReceipt } from "./PromptReceipt";
import { PromptHistoryButton } from "./PromptHistoryButton";
import { PaneConversationDialog } from "./PaneConversationDialog";
import { useT } from "@/i18n";

/**
 * How old a delivery may be and still raise its receipt on a fresh connection.
 *
 * Only applies to the receipt recovered from the HANDSHAKE — a prompt arriving
 * live always shows one. Half an hour comfortably covers "I looked away and
 * came back" while keeping a reopened workspace from re-announcing deliveries
 * the user long since watched play out.
 */
const RECEIPT_MAX_AGE_MS = 30 * 60 * 1000;

type PreservedTerminalViewport = {
  line: number;
  followsTail: boolean;
};

/** Remember what the reader was looking at before a pane is rebuilt or hidden. */
function captureTerminalViewport(
  term: Terminal | null,
): PreservedTerminalViewport | null {
  if (!term) return null;
  const buffer = term.buffer.active;
  return {
    line: buffer.viewportY,
    // Alternate-screen applications do not own scrollback. Their only honest
    // position is the live screen, just like a normal buffer already at its end.
    followsTail: buffer.type !== "normal" || buffer.viewportY >= buffer.baseY,
  };
}

/** Put a pane back where the reader left it, without following new output. */
function restoreTerminalViewport(
  term: Terminal | null,
  viewport: PreservedTerminalViewport | null,
) {
  if (!term) return;
  const buffer = term.buffer.active;
  if (!viewport || viewport.followsTail || buffer.type !== "normal") {
    term.scrollToBottom();
    return;
  }
  term.scrollToLine(Math.min(viewport.line, buffer.baseY));
}

/**
 * How long a rebuilding pane must see NO further output before it is revealed.
 *
 * A replay is only HALF of a rebuild. Handing the recorded bytes over cannot
 * repair a tail that lost its opening frame, nor one whose cursor moves belong
 * to another geometry — so the server follows it by asking the agent to paint
 * its interface again, with a one-row window-size change held for 80 ms and put
 * back (`SessionRegistry._nudge_repaint`). That answer is a SECOND full screen
 * and it lands AFTER the replay has parsed, which is where the pane used to be
 * revealed: the repaint then played out in front of the reader (reported
 * 2026-08-09 — every switch onto a Codex pane opened on the top of its history
 * and raced down, with the replay curtain already in place and working).
 *
 * Only a normal-buffer CLI shows it. An alternate-screen agent (Claude Code)
 * repaints over itself, so nothing travels and nothing is there to hide.
 *
 * So the reveal waits for the pane's output to go QUIET rather than for one
 * write to finish. Comfortably past the nudge's own 80 ms plus a loopback round
 * trip, and short enough that nobody can time it.
 */
export const REBUILD_QUIET_MS = 140;

/**
 * The longest a pane may stay hidden while it rebuilds, however talkative.
 *
 * The quiet window assumes the redraw ENDS. An agent midway through streaming
 * an answer never goes quiet at all, and waiting on it would trade a visible
 * scroll for a pane that simply does not come back. Past this the pane is shown
 * mid-stream — which is what every other terminal in the app looks like while
 * an agent is talking.
 */
export const REBUILD_SETTLE_MAX_MS = 450;

/**
 * The longest a geometry change waits for the pane's parser to reach a gap.
 *
 * Resizing xterm REFLOWS its buffer, and a reflow that lands between two slices
 * of a write moves the rows the half-parsed escape stream is addressing. An
 * agent's TUI is drawn by relative moves — "up twelve rows, erase from here" —
 * so the erase then lands on rows that hold something else: the frame's rule
 * drawn twice, an answer printed under the copy it was replacing, or, when the
 * miscount runs the other way, a pane wiped down to its status line. That is
 * the whole class of "moving a terminal breaks it" (reported 2026-08-11), and
 * the gate below is the answer: fit at a moment when nothing is mid-parse.
 *
 * The wait is bounded because a pane streaming without pause has no gap to
 * offer, and a terminal that never follows its tile is worse than one frame
 * parsed across a reflow — the agent would go on formatting for a size no
 * window is showing. Comfortably longer than one of xterm's ~12 ms parse
 * slices and than any single chunk a socket delivers, short enough that even a
 * pane talking flat out matches its tile within a quarter of a second.
 */
export const RESIZE_PARSE_WAIT_MS = 250;

/**
 * How often a pane may refit while its boundary is still being dragged.
 *
 * The middle ground between two rejected extremes. Refitting on every observer
 * tick (up to 120/s) was the original slideshow: every fit reflows xterm's
 * buffer and makes the agent repaint its whole screen, on the thread that owes
 * the drag its next frame. Refitting never — holding everything for one pass
 * at release — kept the drag fast but froze the text mid-gesture, and the
 * release then re-wrapped it in one visible, hard snap (maintainer,
 * 2026-08-11: the text does not move smoothly with the drag).
 *
 * At this pace the text follows the seam in a few honest steps — an agent
 * repaint costs tens of milliseconds, so about five per second is work it can
 * absorb without stuttering — while the pointer keeps every frame in between.
 * The exact final size still lands through the `layoutBusy` release effect,
 * so letting go looks the same as before, just from much closer by.
 */
export const DRAG_REFIT_MS = 200;

/**
 * How long a call-sign may be — the same cap the backend enforces
 * (`MAX_TERMINAL_NAME`), so the field stops where the save would have failed
 * rather than letting somebody type a name that comes back rejected.
 */
const MAX_TERMINAL_NAME = 40;

/**
 * The floor under a pane's geometry — a CRASH GUARD, not a layout opinion.
 *
 * ## The rule this serves
 *
 * A terminal is exactly as wide as the tile it is shown in, and every
 * character inside that tile is visible. Nothing this pane draws may reach
 * past its own edge. That is the maintainer's rule for this screen
 * (2026-08-11), and it settles a run of attempts that each broke it:
 *
 * * **Clipping** (until 2026-08-11) rendered a fixed 60-column grid whatever
 *   the tile could show and cut the remainder off at the edge. Six terminals
 *   then each showed about two thirds of themselves, and the maintainer read
 *   the result — correctly — as terminals shoved behind one another. Adding a
 *   sideways scrollbar and two scroll shadows did not change what it was.
 * * **Auto-shrinking** (the same day) walked the pane's text size down until
 *   that 60-column grid fitted. It silently overrode the toolbar's size on
 *   every narrow pane — nine columns at ~5 px glyphs while the control read 20
 *   — so the size controls looked dead. Do not reintroduce it.
 *
 * So the fit is honest in both directions: xterm is given what the tile
 * measures, and the agent behind it is told the SAME number. The two must
 * never disagree — an agent laying lines out for 60 columns into an xterm
 * showing 33 re-wraps every one of them, and the TUI's cursor moves then land
 * on rows that no longer hold what they held when it drew them, which came
 * back as shredded one-word fragments (2026-08-10).
 *
 * ## What is left for this floor to do
 *
 * Only the absurd. A tile mid-layout measures 0, a hidden one measures
 * nothing at all, and a PTY resized to zero columns permanently wrecks the
 * agent's drawing. 10x4 is far below any arrangement a person builds on
 * purpose — with a workspace opening two panes deep (`WIZARD_COLUMN_HEIGHT`)
 * even twenty terminals leave roughly twice this — so in practice it is only
 * ever met by a measurement that is not real.
 *
 * It is deliberately NOT a floor on what a coding CLI needs. That question is
 * answered where it belongs: the launcher warns from twenty terminals up
 * (`CROWDED_TERMINAL_COUNT`) and opens as many as the user confirms. How many
 * agents fit on their display is theirs to decide, and a pane too narrow to be
 * useful is a pane they can see is too narrow — which is exactly what clipping
 * hid.
 *
 * The backend holds the same floor (`jarvis/agentic_ide/session.py`), so a
 * stale or older client cannot resize a PTY into nothing either.
 */
const MIN_REAL_COLS = 10;
const MIN_REAL_ROWS = 4;

/*
 * The width below which a coding CLI stops drawing a frame anyone can read
 * ({@link WORKABLE_COLS}) now lives in ./layout, because the wizard has to
 * quote the same number before anything opens and must not import a terminal
 * to do it. What it MEANS here has changed twice, and both are worth keeping:
 *
 * * It began as a floor the backend enforced, which kept agents alive by
 *   drawing every narrow pane wider than the window showing it. The maintainer
 *   read that as terminals shoved behind one another (2026-08-11) and it was
 *   removed, along with two other attempts on the same day — shrinking the text
 *   until 60 fitted made the size controls look dead, and widening a pane on
 *   hover shuffled the workspace under the cursor.
 * * It then became a NOTICE and nothing else, on the rule that survived all
 *   three: a pane is exactly as wide as its tile. That rule still stands for
 *   every pane that HAS a terminal in it.
 *
 * What the notice could not fix is that the wreckage stayed on screen. Below
 * this width a coding CLI repaints over rows that no longer hold what it drew,
 * and panes that had been working for an hour came back blank (2026-08-13). So
 * a pane below it now holds its agent's columns and shows a card instead — see
 * `PaneTooNarrowCard`, which is where the whole argument is written down.
 */

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
  /*
   * Is this agent still working, and since when — the backend's own reading of
   * the pane's screen (`jarvis/agentic_ide/activity.py`).
   *
   * Only read by `PaneTooNarrowCard`, which is the one place in this component
   * that has to SAY the state rather than badge it: a pane whose terminal is
   * held back has nothing else on it to look at. Optional throughout, so every
   * standalone use of this component keeps working without them.
   */
  activity?: PaneActivity;
  /** When the pane entered that state (epoch seconds); 0 when unknown. */
  activitySince?: number;
  /** Has anything ever been asked of this pane? Separates "done" from "idle". */
  worked?: boolean;
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
   * The pane then refits itself on a leash instead of freely: a fit reflows
   * xterm's buffer AND tells the agent its new size, and an agent answers that
   * by redrawing its whole screen. Sixty of those a second, across every pane
   * a seam touches, is what turned dragging a boundary into a slideshow — so
   * mid-drag the pane takes at most one fit per DRAG_REFIT_MS, enough for the
   * text to follow the seam in steps instead of freezing until release. The
   * exact final size is still taken in a single pass the moment this goes
   * false, which is what stops the terminal's contents from trailing the frame
   * around them for longer than they must.
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
  activity = "",
  activitySince = 0,
  worked = false,
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
  const visibilityRef = useRef<{
    show: (afterFlush?: () => void) => void;
    park: () => void;
  } | null>(null);
  const statusRef = useRef<PaneStatus>("connecting");
  // Mirrored into state purely so the header can show/hide the restart button;
  // it transitions a handful of times per pane, never per output chunk.
  const [visibleStatus, setVisibleStatus] = useState<PaneStatus>("connecting");
  /*
   * What the socket said ABOUT that status, in the user's words.
   *
   * Kept here as well as handed to `onStatus` because the two readers need it
   * at different times. The grid shows it once, in a tooltip; the pane has to
   * keep saying it — an exit reason written into the terminal scrolls away the
   * moment anything else is drawn, and the trouble line is written at most once
   * per KIND of trouble (see `troubleShown`), so a user who looked away has no
   * way back to it at all. The notice below is that way back.
   */
  const [statusDetail, setStatusDetail] = useState<string>("");
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
  /*
   * How many columns this pane last fitted to, or null before it has measured.
   *
   * Mirrored into state for one reader — the width notice below. It changes
   * when the workspace is re-laid out, never per chunk of output, and React
   * bails out on an unchanged value, so a pane that keeps measuring the same
   * tile re-renders nothing.
   */
  const [paneCols, setPaneCols] = useState<number | null>(null);
  /*
   * Has the reader waved the width notice away for this pane?
   *
   * A notice that cannot be dismissed becomes furniture, and this one would be
   * on every pane of a crowded workspace at once — a row of identical warnings
   * about a trade the user may well have made on purpose. One click retires it.
   *
   * Reset below once the pane has room again, so a workspace that is widened
   * and later crowded a second time is told a second time rather than staying
   * quiet about a screen that has started shredding again.
   */
  const [widthNoticeDismissed, setWidthNoticeDismissed] = useState(false);
  /*
   * Has the reader asked to see the terminal anyway, at whatever width there is?
   *
   * The way out of the card below (see `PaneTooNarrowCard`). It restores exactly
   * the behaviour a narrow pane had before the card existed — the tile's own
   * width, handed to the agent, plus the notice saying so — for this one pane.
   *
   * Reset alongside `widthNoticeDismissed` once the pane has room again: the
   * override answers "show me this pane as it is now", not "never hold this
   * pane's columns again for the rest of its life".
   */
  const [narrowOverride, setNarrowOverride] = useState(false);
  /*
   * Is this pane's tile too narrow for its agent to draw in RIGHT NOW?
   *
   * Written by the fit (`applyResize`), which is the only thing that measures.
   * Distinct from `paneCols < WORKABLE_COLS` on purpose: that comparison is what
   * the fit decides FROM, and this is what it decided — including the case where
   * a pane has been measured but the override is holding the terminal open.
   */
  const [tooNarrow, setTooNarrow] = useState(false);
  // A parked chat pane may have a large asynchronous xterm write to parse when
  // it takes the stage again. Keep its terminal surface out of the paint until
  // that write and the final viewport restoration have both landed; otherwise xterm
  // briefly shows the pane's old viewport (often its first prompt) and visibly
  // jumps to the live prompt one frame later.
  const [tailReady, setTailReady] = useState(active);
  // The terminal itself stays in a ref. This small epoch tells appearance and
  // font effects that the ref now points at a new instance after a restart.
  const [terminalEpoch, setTerminalEpoch] = useState(0);
  /*
   * Is a replay currently rebuilding this pane behind the curtain?
   *
   * The replay path and the chat-stage switch both hide the surface until the
   * viewport restoration has landed, and they can interleave: a replay arriving within
   * a frame of a stage switch (or of the mount) would otherwise have the
   * OTHER sequence's settle step lift the curtain while the replay is still
   * printing its history — which is exactly the top-to-bottom scroll the
   * curtain exists to hide. While this is true, only the replay's own
   * completion may reveal the pane.
   */
  const replayCurtainRef = useRef(false);
  // A stage switch must preserve a reader who deliberately scrolled back.
  // Kept through replay parsing because reset() temporarily destroys xterm's
  // own viewport, making it impossible to recover afterwards.
  const preservedViewportRef = useRef<PreservedTerminalViewport | null>(null);
  // Every replay and active-stage transition invalidates callbacks/frames from
  // the one before it. A boolean alone cannot distinguish "A finished" from
  // "B is still rebuilding", so an old callback could otherwise reveal B.
  const replayGenerationRef = useRef(0);
  const replayRevealFrameRef = useRef<number | undefined>(undefined);
  /**
   * Hold a finished rebuild back until the pane has stopped being redrawn.
   *
   * Owned by the connect effect, because only that scope sees every byte this
   * pane draws. Reached from the stage-switch effect below as well: taking the
   * stage also announces a new size, and an agent answers a size the same way
   * it answers the server's nudge — by painting its whole screen again.
   *
   * Null before the terminal exists; the caller then reveals straight away,
   * which is right for a pane that has nothing to rebuild yet.
   */
  const settleRebuildRef = useRef<
    ((generation: number, reveal: () => void) => void) | null
  >(null);
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
  /** The pane's recorded conversation, opened from the header book button. */
  const [historyOpen, setHistoryOpen] = useState(false);
  // Latest callbacks/appearance without re-running the connect effect.
  const onStatusRef = useRef(onStatus);
  const onAttachErrorRef = useRef(onAttachError);
  /*
   * The text size this pane draws at, as of NOW.
   *
   * A ref because the connect effect must not re-run when the user changes the
   * size — rebuilding the terminal would drop the agent's screen and reconnect
   * its socket for what is a one-line restyle. But it is written on EVERY
   * render, never frozen at mount: the effect below rebuilds the terminal for
   * reasons of its own (`geometryReady` flipping when the grid is re-measured,
   * a pane restart, a rename), and a frozen value hands every one of those
   * rebuilds the size this pane opened with rather than the size the user is
   * looking at. That is the reported bug — the toolbar reads 20, the pane the
   * user last touched is 20, and every pane rebuilt since the change is back
   * at the 13 the grid started with, with no further size change coming to
   * correct it (see the `fontSize` effect: it fires on CHANGES).
   */
  const fontSizeRef = useRef(fontSize);
  // The ground this pane draws on, as of NOW — the socket tells the backend, so
  // that the agent's CLI is answered with the colours it is actually drawing on
  // when it asks. Read at connect time, hence a ref rather than the prop: the
  // connect effect must not re-run when the user flips the theme. Same rule as
  // the size above: current on every render, so a rebuild cannot resurrect the
  // theme this pane happened to open with.
  const appearanceRef = useRef(appearance);
  const activeRef = useRef(active);
  const focusedRef = useRef(focused);
  // Read by the connect effect's resize scheduler, which is built once and
  // therefore cannot see the prop change.
  const layoutBusyRef = useRef(layoutBusy);
  /*
   * The last width this pane could honestly give its agent, in COLUMNS.
   *
   * The whole mechanism behind {@link PaneTooNarrowCard}. While a tile is too
   * narrow to draw in, the pane keeps its terminal at this width instead of
   * following the tile down — so the agent goes on formatting for a screen it
   * can lay out, and the card is shown over the top rather than the wreckage.
   *
   * A ref because the fit reads it, and the fit is built once per terminal and
   * cannot see state. Seeded with WORKABLE_COLS so a pane that OPENS into a
   * narrow tile — the crowded workspace, which is the whole reported case — has
   * a workable width to hold from its very first measurement.
   */
  const heldColsRef = useRef(WORKABLE_COLS);
  // Read by the fit, which is built once per terminal — see `heldColsRef`.
  const narrowOverrideRef = useRef(narrowOverride);
  onStatusRef.current = onStatus;
  onAttachErrorRef.current = onAttachError;
  fontSizeRef.current = fontSize;
  appearanceRef.current = appearance;
  activeRef.current = active;
  focusedRef.current = focused;
  layoutBusyRef.current = layoutBusy;
  narrowOverrideRef.current = narrowOverride;

  useEffect(() => {
    const region = terminalRegionRef.current;
    if (!region) return;
    return bindTerminalScrollRegion(region);
  }, []);

  useEffect(() => {
    if (!geometryReady) return;
    const container = containerRef.current;
    if (!container) return;

    // A restart builds a brand-new terminal on a blank screen, so the pane owes
    // the user the same "it is coming up" answer it owed on its first mount.
    setPainted(false);
    // And it must not inherit a curtain: a rebuild can tear the old terminal
    // down between a replay dropping the curtain and its write callback (see
    // `replayToPane`), which would leave the new terminal invisible with
    // nothing left to lift it.
    replayGenerationRef.current += 1;
    replayCurtainRef.current = false;
    if (replayRevealFrameRef.current !== undefined) {
      cancelAnimationFrame(replayRevealFrameRef.current);
      replayRevealFrameRef.current = undefined;
    }
    setTailReady(activeRef.current);

    const linkOptions = {
      workspaceId,
      onError: (message: string) => onAttachErrorRef.current?.(message),
    };
    const activateLink = createTerminalLinkActivator(linkOptions);
    const term = new Terminal({
      convertEol: false,
      // The pane shell supplies the shared section glass. xterm otherwise
      // paints an opaque canvas over it, hiding both that glass and the desktop
      // artwork even when the surrounding React container is translucent.
      allowTransparency: true,
      // A CLI configured for the other ground paints truecolor a palette can
      // never remap — dark-theme white text into a light pane. This floor
      // nudges any unreadable foreground toward legibility; the theme's
      // transparent background carries the ground RGB it measures against.
      minimumContrastRatio: MINIMUM_CONTRAST_RATIO,
      // Shared with the measurement in ./../../lib/terminalFont: a pane that
      // measured a different stack from the one it draws with is the bug that
      // module exists to prevent.
      fontFamily: TERMINAL_FONT_STACK,
      fontSize: fontSizeRef.current,
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
      linkHandler: createTerminalOscLinkHandler(linkOptions),
      // Windows only. ConPTY re-emits and re-wraps lines in a way a POSIX pty
      // never does; without telling xterm which backend it is talking to, those
      // re-emitted lines pile up as duplicated, half-overwritten rows. Harmless
      // to declare on other platforms — the pty there simply never triggers it,
      // and the value is derived from the browser rather than assumed.
      windowsPty: /windows/i.test(navigator.userAgent)
        ? { backend: "conpty" as const }
        : undefined,
      theme: themeFor(appearanceRef.current),
    });
    // Before anything is written to this terminal: the agent's CLI asks what
    // its terminal is and which colours it draws on within milliseconds of
    // starting, and an answer produced HERE has to cross the socket twice
    // before reaching it — too late, and then visible as junk in its prompt.
    // The backend answers those instead. See ./terminalQueries.
    const disposeQuerySuppression = installQuerySuppression(term.parser);
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon(activateLink));
    if (workspaceId) {
      term.loadAddon(new TerminalPathLinksAddon(activateLink));
    }
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
    // The wheel always moves xterm's own history, even while a normal-buffer
    // CLI has negotiated mouse tracking — otherwise scrolling only "works"
    // when the CLI feels like it, which is the per-provider inconsistency the
    // scroll rebuild removed. See captureWheelForTerminalHistory.
    term.attachCustomWheelEventHandler(captureWheelForTerminalHistory(term));
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
    // The keyboard bridges below claim keystrokes, and xterm holds exactly ONE
    // custom key handler — attaching them directly would leave only the last
    // one working, silently. See ./terminalKeyChain.
    const keys = createKeyEventChain(term);
    const isMac = /mac|iphone|ipad/i.test(navigator.userAgent);
    // The desktop IDE reserves its platform copy chord for copying. In
    // particular, an unselected Ctrl+C on Windows/Linux must not reach Codex
    // as `^C`, where it cancels the current turn or exits the pane.
    const disposeCopyBridge = installCopyBridge(
      {
        attachCustomKeyEventHandler: keys.add,
        getSelection: () => term.getSelection(),
        focus: () => term.focus(),
      },
      {
        copy: robustCopy,
        isMac,
        onUnavailable: () =>
          onAttachErrorRef.current?.(
            "Could not copy the terminal selection on this machine.",
          ),
      },
    );
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
        isMac,
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
      // Taken BEFORE the handshake below reads the terminal's geometry — so
      // the grid this pane draws on and the size its agent is spawned at are
      // the same thing from the very first byte.
      const proposed = fit.proposeDimensions();
      if (
        proposed &&
        Number.isFinite(proposed.cols) &&
        Number.isFinite(proposed.rows)
      ) {
        // The tile's own measurement, and only the absurd is refused (see
        // MIN_REAL_COLS). `fit()` rather than a resize to the same numbers:
        // it is what xterm offers and it keeps the renderer's own bookkeeping
        // in step.
        if (proposed.cols >= MIN_REAL_COLS && proposed.rows >= MIN_REAL_ROWS) {
          fit.fit();
        } else {
          term.resize(
            Math.max(proposed.cols, MIN_REAL_COLS),
            Math.max(proposed.rows, MIN_REAL_ROWS),
          );
        }
      }
    } catch {
      /* not measured yet — the ResizeObserver below will fit */
    }

    const report = (status: PaneStatus, detail?: string) => {
      statusRef.current = status;
      setVisibleStatus(status);
      setStatusDetail(detail ?? "");
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
    // And at most one line per WORDING, which is a separate limit. A refused
    // attach reaches this twice — once as the reason the server sent, then
    // again as the verdict on the close that immediately follows it — and the
    // two are now the same sentence, because the second one no longer invents
    // its own. Printing it again in red under itself in yellow reads like two
    // problems.
    let troubleText = "";

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

    /*
     * How many writes this pane has handed xterm that have not finished
     * parsing.
     *
     * The one thing a resize has to know. xterm parses a write in time slices,
     * so "the socket delivered it" and "the screen has it" are different
     * moments, and reflowing the buffer in between moves the rows the rest of
     * that stream is addressing (see RESIZE_PARSE_WAIT_MS). Counted rather
     * than asked, because xterm exposes no such question — every byte this
     * pane draws goes through `writeToTerminal` below, which is what makes the
     * count complete.
     */
    let parsing = 0;
    /**
     * Run the geometry change this pane is holding back, if it is holding one.
     *
     * Assigned once the resize path below exists; the writes above are defined
     * first because everything else in this scope draws through them.
     */
    let resumeResize: (() => void) | null = null;

    /** Hand bytes to xterm, keeping the count of what is still being parsed. */
    const writeToTerminal = (text: string, afterWrite?: () => void) => {
      parsing += 1;
      // A TUI that paints its theme ground on every cell would hide the
      // glass this pane sits on. Default-background those fills here, on
      // the way into xterm — see ./terminalGlass.
      term.write(clearTuiCanvasFill(text), () => {
        parsing = Math.max(0, parsing - 1);
        afterWrite?.();
        // The parser is between chunks — the one safe moment to reflow.
        if (parsing === 0) resumeResize?.();
      });
    };

    /**
     * Write what is held, whether or not this pane believes it is watched.
     *
     * The deadline half of parking (see ./offscreenBuffer): being wrong about
     * visibility must cost a coalesced write, never a screen that stopped.
     */
    const flushHeld = (afterFlush?: () => void) => {
      cancelHoldTimer();
      const held = offscreen.drain();
      if (held) {
        writeToTerminal(held, afterFlush);
        return;
      }
      afterFlush?.();
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

    const showPane = (afterFlush?: () => void) => {
      if (!activeRef.current) return;
      if (paneVisible) {
        afterFlush?.();
        return;
      }
      paneVisible = true;
      flushHeld(afterFlush);
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

    /*
     * Reveal a rebuilt pane on QUIET, not on one write having finished.
     *
     * Two timers, and the difference between them is the whole contract. The
     * quiet timer is restarted by every chunk that arrives while the curtain is
     * down, so the repaint the server asks for after a replay plays out behind
     * it. The deadline is armed once and NEVER restarted by output — it is the
     * promise that the pane comes back even if the agent never stops talking.
     */
    let quietTimer: number | undefined;
    let deadlineTimer: number | undefined;
    /** What to run once the rebuild has settled; null while none is pending. */
    let settleReveal: (() => void) | null = null;
    /** The rebuild being waited out, so a superseded one cannot reveal. */
    let settlingFor = 0;

    const clearSettleTimers = () => {
      if (quietTimer !== undefined) window.clearTimeout(quietTimer);
      if (deadlineTimer !== undefined) window.clearTimeout(deadlineTimer);
      quietTimer = undefined;
      deadlineTimer = undefined;
    };

    const finishSettle = () => {
      const reveal = settleReveal;
      const generation = settlingFor;
      clearSettleTimers();
      settlingFor = 0;
      settleReveal = null;
      // Before the guards, deliberately: a settle abandoned because the pane
      // went away must not leave the curtain flag raised, or the next stage
      // switch would refuse to lift a curtain nobody owns any more.
      replayCurtainRef.current = false;
      if (
        disposed ||
        !activeRef.current ||
        generation !== replayGenerationRef.current
      ) {
        return;
      }
      reveal?.();
    };

    /** Something is still being drawn — push the reveal back one quiet window. */
    const noteRebuildOutput = () => {
      if (settlingFor === 0) return;
      if (quietTimer !== undefined) window.clearTimeout(quietTimer);
      quietTimer = window.setTimeout(finishSettle, REBUILD_QUIET_MS);
    };

    const settleRebuild = (generation: number, reveal: () => void) => {
      if (disposed || generation !== replayGenerationRef.current) return;
      clearSettleTimers();
      settlingFor = generation;
      settleReveal = reveal;
      deadlineTimer = window.setTimeout(finishSettle, REBUILD_SETTLE_MAX_MS);
      noteRebuildOutput();
    };
    settleRebuildRef.current = settleRebuild;

    // Everything this pane draws goes through here, not just the agent's
    // stream: an exit banner written straight to xterm while output is parked
    // would appear ABOVE the output it is supposed to follow.
    const writeToPane = (text: string, afterWrite?: () => void) => {
      if (!text) return;
      // Anything drawn while a rebuild settles belongs to that rebuild. The
      // viewport is deliberately NOT touched here — where the pane opens is
      // the reveal's decision, and it restores what the reader was looking at.
      noteRebuildOutput();
      // The first byte is what retires the "starting" overlay — and it is taken
      // HERE rather than at the socket, so a pane whose output is parked
      // offscreen still counts as painted. It has a screen; nobody is looking
      // at it. Cheap to call per chunk: React bails out on an unchanged value.
      setPainted(true);
      if (!paneVisible) recheckParked();
      if (paneVisible) {
        writeToTerminal(text, afterWrite);
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
      const replayViewport =
        preservedViewportRef.current ?? captureTerminalViewport(term);
      if (replayViewport) preservedViewportRef.current = replayViewport;
      const generation = replayGenerationRef.current + 1;
      replayGenerationRef.current = generation;
      if (replayRevealFrameRef.current !== undefined) {
        cancelAnimationFrame(replayRevealFrameRef.current);
        replayRevealFrameRef.current = undefined;
      }
      // Parked output belongs to the screen this replay REPLACES, and it was
      // captured before it. Written afterwards it would paint the older screen
      // over the newer one; written before, it would be reset away regardless.
      // The deadline goes with it — a flush firing after this would draw the
      // screen this replay just replaced.
      cancelHoldTimer();
      offscreen.drain();
      // React state is not a synchronous paint barrier. In a real WebSocket
      // callback, `setTailReady(false)` may not reach the DOM before xterm's
      // write queue starts parsing. Hide the canvas host imperatively BEFORE
      // reset/write; React still mirrors the curtain below for later renders.
      const curtain = paneVisible && activeRef.current;
      if (curtain) {
        container.style.visibility = "hidden";
        replayCurtainRef.current = true;
        setTailReady(false);
      }
      term.reset();
      // A normal-buffer CLI's replay is its whole scrollback — up to the
      // server's 128 KB (see `ReplayBuffer`) — and xterm parses it in time
      // slices. Written onto a VISIBLE surface, the history prints top to
      // bottom with the viewport chasing it for the length of the parse
      // (reported 2026-08-08: every switch onto a fresh Codex pane opened on
      // the top of its history and visibly raced down). Alt-screen replays
      // repaint in place, which is why only normal-buffer CLIs ever showed
      // it. So the surface is hidden for the length of the rebuild — the same
      // curtain a chat-stage switch drops — and lifted one settled frame
      // after the reader's viewport has been restored.
      // Through the ordinary path, so a replay arriving while nobody is looking
      // is parked and un-parked by the same rules as anything else — and so it
      // counts as the pane having painted.
      writeToPane(text, () => {
        if (generation !== replayGenerationRef.current) return;
        if (disposed || !activeRef.current) {
          // A callback that fired while the pane was away must not leave the
          // stage-switch settle step refusing to lift the curtain forever.
          replayCurtainRef.current = false;
          return;
        }
        restoreTerminalViewport(term, replayViewport);
        if (!curtain) {
          replayCurtainRef.current = false;
          if (preservedViewportRef.current === replayViewport) {
            preservedViewportRef.current = null;
          }
          return;
        }
        // The bytes have parsed; the REPAINT they provoke has not arrived yet.
        // Stay hidden until this pane stops being drawn — see REBUILD_QUIET_MS.
        settleRebuild(generation, () => {
          replayRevealFrameRef.current = requestAnimationFrame(() => {
            replayRevealFrameRef.current = undefined;
            if (
              disposed ||
              !activeRef.current ||
              generation !== replayGenerationRef.current
            ) {
              return;
            }
            restoreTerminalViewport(term, replayViewport);
            setTailReady(true);
            container.style.removeProperty("visibility");
            if (preservedViewportRef.current === replayViewport) {
              preservedViewportRef.current = null;
            }
          });
        });
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

    /** Is this pane's tile something that can honestly be measured right now? */
    const measurable = () =>
      container.clientWidth >= 8 && container.clientHeight >= 8;

    /**
     * Fit this pane to its tile and tell the agent behind it — right now.
     *
     * Only ever reached through `sendResize` below, which is what decides
     * WHEN "right now" is safe. Everything here reflows xterm's buffer, and a
     * reflow may not land in the middle of a parse (see RESIZE_PARSE_WAIT_MS).
     */
    const applyResize = (claimOwner: boolean) => {
      // A hidden pane measures 0x0 (maximizing another one hides this one), and
      // fitting to that would resize the PTY to zero columns — which permanently
      // wrecks the agent's full-screen drawing. Skip while not measurable; the
      // ResizeObserver fires again when the pane comes back. Re-checked here as
      // well as at the gate: a deferred fit runs a moment later, and the tile it
      // was asked for may be gone by then.
      if (disposed || !measurable()) return;
      // The pane draws at the READER'S text size, never one it picked itself.
      // An auto-shrink that walked this size down until the floor grid fit the
      // tile shipped and was rejected within hours (2026-08-11): it silently
      // overrode the toolbar's size on every narrow pane, which read as the
      // size controls being dead (see the floors' comment above). A stale size
      // can still linger on the terminal — a rebuild, a pane that shrank under
      // the old build — so the choice is restated before measuring.
      const desired = fontSizeRef.current;
      if (term.options.fontSize !== desired) {
        term.options.fontSize = desired;
        // A new size is a new glyph advance, and so a new floored fraction for
        // the canvas renderer to give back — same order as the fontSize effect.
        alignTerminalCells(term);
        term.clearTextureAtlas?.();
      }
      // Measured WITHOUT being applied yet: what the tile can show is a
      // PROPOSAL, and the floors above have the last word on it.
      let proposed: { cols: number; rows: number } | undefined;
      try {
        proposed = fit.proposeDimensions();
      } catch {
        return;
      }
      if (
        !proposed ||
        !Number.isFinite(proposed.cols) ||
        !Number.isFinite(proposed.rows)
      ) {
        return;
      }
      // The one size everyone gets — the grid here, the agent below, and it is
      // what the TILE measures. The two may never disagree: an agent laying
      // its lines out for a width its xterm does not have re-wraps every one
      // of them, and its cursor moves then land on rows that hold something
      // else (shredded one-word fragments, 2026-08-10). Only the absurd is
      // refused, which is all MIN_REAL_COLS is for now.
      const measured = {
        cols: Math.max(proposed.cols, MIN_REAL_COLS),
        rows: Math.max(proposed.rows, MIN_REAL_ROWS),
      };
      // What the TILE measures — read by the width notice and by the card, which
      // are the two things in this component with an opinion about whether that
      // number is enough (see WORKABLE_COLS). Recorded whatever happens below: a
      // fit that changes nothing still measured the tile.
      setPaneCols(measured.cols);
      /*
       * The decision this pane's card exists for.
       *
       * Everything above is unchanged: the tile is measured honestly and the
       * floors only refuse the absurd. What changes is what happens when the
       * honest answer is a width the agent cannot draw in.
       *
       * Following the tile down there was the bug reported on 2026-08-13 and
       * measured once before on 2026-08-09: opening five more terminals re-fits
       * every pane already open (`append_pane` gives each new one a full-height
       * column), and at thirteen columns a coding CLI does not merely look
       * cramped — it repaints by erasing the rows it last drew, its own line
       * count no longer matches the screen, and the repaint wipes more than it
       * rewrites. Panes that had been working for an hour came back BLANK, and
       * no later output brought them back.
       *
       * So below the workable width the columns are HELD instead. The agent goes
       * on formatting for the last width it could lay out in, its screen is
       * never wrecked, and the pane shows a card saying what it is doing rather
       * than a rectangle of shredded fragments. Nothing is drawn past the tile's
       * edge, because nothing is drawn in the tile at all — which is what
       * separates this from the clipping design rejected on 2026-08-11.
       *
       * ROWS still follow the tile. Height is not the axis that breaks a TUI —
       * a resize on that axis repaints in place — and honouring it keeps xterm
       * and the PTY in agreement, which is the rule this whole file is built on.
       */
      const narrow = measured.cols < WORKABLE_COLS && !narrowOverrideRef.current;
      setTooNarrow(narrow);
      // Only a width the agent could really draw in is worth holding on to.
      // Recorded even while the override is on: the reader chose to watch a
      // narrow pane, they did not choose what it should fall back to.
      if (measured.cols >= WORKABLE_COLS) heldColsRef.current = measured.cols;
      const size = narrow ? { cols: heldColsRef.current, rows: measured.rows } : measured;
      try {
        if (!narrow && size.cols === proposed.cols && size.rows === proposed.rows) {
          fit.fit();
        } else if (term.cols !== size.cols || term.rows !== size.rows) {
          term.resize(size.cols, size.rows);
        }
      } catch {
        return;
      }
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
      if (socket?.send({ t: claimOwner ? "claim" : "r", ...size }))
        sentSize = size;
    };

    /** A fit this pane is holding back until its parser reaches a gap. */
    let deferredResize: { claimOwner: boolean } | null = null;
    /** The promise that a held-back fit happens even without a gap. */
    let deferredResizeTimer: number | undefined;

    const clearDeferredResize = () => {
      deferredResize = null;
      if (deferredResizeTimer === undefined) return;
      window.clearTimeout(deferredResizeTimer);
      deferredResizeTimer = undefined;
    };

    /**
     * Hold this fit until the parser is between chunks — or until the wait runs
     * out, whichever comes first (see RESIZE_PARSE_WAIT_MS).
     */
    const deferResize = (claimOwner: boolean) => {
      // Ownership is the stronger of the two requests: a claim carries a size
      // as well, so merging keeps it rather than letting an ordinary refit
      // arriving a millisecond later quietly drop the claim.
      deferredResize = {
        claimOwner: (deferredResize?.claimOwner ?? false) || claimOwner,
      };
      if (deferredResizeTimer !== undefined) return;
      deferredResizeTimer = window.setTimeout(() => {
        deferredResizeTimer = undefined;
        const pending = deferredResize;
        deferredResize = null;
        if (disposed || !pending) return;
        applyResize(pending.claimOwner);
      }, RESIZE_PARSE_WAIT_MS);
    };

    resumeResize = () => {
      const pending = deferredResize;
      if (!pending || disposed) return;
      clearDeferredResize();
      applyResize(pending.claimOwner);
    };

    /**
     * Fit this pane to its tile — at a moment when doing so cannot shred it.
     *
     * The gate, and the reason this is not simply `applyResize`. Reflowing
     * xterm's buffer while a write is still being parsed moves the rows the
     * rest of that stream is addressing, and an agent's TUI addresses rows by
     * relative moves — so the frame it was drawing is finished into the wrong
     * ones. See RESIZE_PARSE_WAIT_MS for what that looks like on screen and for
     * why the wait is bounded rather than indefinite.
     */
    const sendResize = (claimOwner = false) => {
      if (!measurable()) return;
      // Resizing a pane is the other half of the un-park story. Maximizing one,
      // dragging a seam, changing the font — all of them are a user opening up
      // a pane to READ it, and the pane it opens must not be a parked one still
      // holding its agent's screen.
      //
      // BEFORE the gate rather than after the fit, and that ordering is the
      // second half of this bug. Parked output was drawn for the size the pane
      // is LEAVING; written once the grid had already moved it painted an old
      // screen into a new geometry — the doubled rules and half-erased answers
      // the gate below exists to stop, arriving through the one path that used
      // to walk straight past it. Un-parked here, those bytes go in first and
      // the fit waits for them like any other write.
      revealIfOnScreen();
      if (parsing > 0) {
        deferResize(claimOwner);
        return;
      }
      applyResize(claimOwner);
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
        // The connect-time size is a best effort. The mount-time fit above
        // already clamped the grid to the floors, so the terminal's own
        // geometry is normally safe to hand over as-is — the guards here are
        // for the one case that fit could not run at all (a grid cell still
        // mid-layout measures as nothing), where the terminal still holds
        // whatever it was constructed with. A size under a floor is treated
        // as "not measured yet"; the real one follows from `onOpen`'s fit as
        // soon as the cell settles.
        cols: term.cols >= MIN_REAL_COLS ? term.cols : 80,
        rows: term.rows >= MIN_REAL_ROWS ? term.rows : 24,
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
         * The agent is in a different size than this pane asked for — follow it.
         *
         * `applyResize` reflows xterm the instant it measures the tile, before
         * anything has agreed to that size, and the server is allowed to say no
         * (a tile under the floor keeps the working geometry, a displaced viewer
         * is ignored). Nothing used to reconcile the two afterwards — the wire
         * had no way to say "not granted" — so a refusal left this grid and the
         * agent's permanently different widths, and the agent's relative cursor
         * moves then finished its repaints into rows holding other text. That is
         * the doubled, character-by-character text a narrow pane showed
         * (2026-08-11): not a renderer fault, two screens in one grid.
         *
         * `sentSize` is deliberately NOT updated. It records what was ASKED,
         * and that is what stops this from oscillating: the same tile measured
         * again matches it and is never re-sent, so a pane whose size is refused
         * asks once and then stays quiet — while a tile that really changes
         * still speaks up. Following the agent also repairs what is already on
         * screen, because that content was drawn for this geometry all along.
         */
        onGeometry: ({ cols, rows }) => {
          if (disposed) return;
          if (term.cols === cols && term.rows === rows) return;
          try {
            term.resize(cols, rows);
          } catch {
            /* the terminal is being torn down — nothing left to reconcile */
          }
        },
        /**
         * A prompt just landed in this pane — make that impossible to miss.
         *
         * Two things, and the order matters. The pane is un-parked FIRST:
         * whatever the observer believes, output held back now is output the
         * user will not see while they are looking straight at the receipt
         * telling them it arrived. Only then does it flash, which is the part
         * that is merely nice.
         *
         * It used to scroll the pane into the viewport as well, because a
         * receipt drawn on a pane two screens down is no better than no
         * receipt. There is no such pane any more — the workspace is one
         * screenful by rule (see the header of ./layout) — and the call would
         * now find the app's own scroller instead and move the whole section
         * under the user.
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
        },
        onReady: ({ resumed, reattached, lastPrompt }) => {
          troubleShown = null;
          troubleText = "";
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
            (document.activeElement === null ||
              document.activeElement === document.body)
          ) {
            term.focus();
          }
        },
        onExit: (code) => {
          report("exited", explainExit(code));
          writeToPane(
            `\r\n\x1b[?25h\x1b[33m${describeExit(displayName, code)}\x1b[0m\r\n`,
          );
        },
        onTrouble: (message, retrying) => {
          if (disposed) return;
          // A scheduled retry means the pane is not dead yet. Calling it "error"
          // there is what left a whole grid painted red over a backend that was
          // merely restarting.
          report(retrying ? "connecting" : "error", message);
          const kind = retrying ? "retrying" : "unreachable";
          const repeats = troubleShown === kind || message === troubleText;
          troubleShown = kind;
          // The status was reported either way — only the terminal write is
          // skipped. The notice row keeps the sentence on screen (see
          // `PaneNotice`), so nothing is lost by not repeating it.
          if (repeats) return;
          troubleText = message;
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
    /** The pending mid-drag refit — a throttle, so it must NOT be reset per tick. */
    let dragRefitTimer: number | undefined;
    // The queued form of `sendResize`, kept as ONE stable function so the queue
    // can recognise this pane's pending reflow — both to skip a duplicate and
    // to drop it when the pane goes away. See ./paneReflowQueue for why panes
    // must not reflow in the same frame as each other.
    const reflow = () => sendResize();
    const scheduleResize = () => {
      // While a seam or the prompt bar is being dragged the pane still
      // follows, but on a leash: at most one refit per DRAG_REFIT_MS, so the
      // text flows toward the seam in steps instead of freezing until release.
      // A throttle rather than the debounce below on purpose — the observer
      // fires continuously for as long as the seam moves, and a debounce would
      // wait for a pause that a moving pointer never offers.
      if (layoutBusyRef.current) {
        if (dragRefitTimer !== undefined) return;
        dragRefitTimer = window.setTimeout(() => {
          dragRefitTimer = undefined;
          // A drag that ended while this window was pending has already taken
          // its exact final size through the `layoutBusy` release effect.
          if (layoutBusyRef.current) queuePaneReflow(reflow);
        }, DRAG_REFIT_MS);
        return;
      }
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
      replayGenerationRef.current += 1;
      replayCurtainRef.current = false;
      if (replayRevealFrameRef.current !== undefined) {
        cancelAnimationFrame(replayRevealFrameRef.current);
        replayRevealFrameRef.current = undefined;
      }
      container.style.removeProperty("visibility");
      // A pending reveal would fire into a disposed terminal, and its timers
      // would outlive the pane that armed them.
      clearSettleTimers();
      settleReveal = null;
      settlingFor = 0;
      if (settleRebuildRef.current === settleRebuild) {
        settleRebuildRef.current = null;
      }
      // Before the terminal is disposed below: a deadline flush one tick later
      // would write into it after it is gone.
      cancelHoldTimer();
      // Same for a fit still waiting for a parse gap — it would reflow a
      // disposed terminal inside a detached element.
      clearDeferredResize();
      resumeResize = null;
      if (resizeTimer !== undefined) window.clearTimeout(resizeTimer);
      if (dragRefitTimer !== undefined) window.clearTimeout(dragRefitTimer);
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
      disposeCopyBridge();
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
  }, [name, workspaceId, restartToken, geometryReady]);

  /*
   * A chat-stage switch changes a pane from `display:none` to the full canvas
   * in one commit. Refit and restore the reader's viewport before that frame
   * paints; a pane already at the tail keeps following it, while hidden
   * siblings stay parked even when a prompt is addressed to them.
   */
  useLayoutEffect(() => {
    const departingViewport = !active
      ? captureTerminalViewport(termRef.current)
      : null;
    if (
      !active &&
      departingViewport &&
      (!replayCurtainRef.current || !preservedViewportRef.current)
    ) {
      preservedViewportRef.current = departingViewport;
    }
    // A stage change supersedes every reveal owned by the stage before it. If
    // a replay is still parsing, the active branch's queue barrier below waits
    // for it and becomes the only path allowed to lift the new curtain.
    replayGenerationRef.current += 1;
    replayCurtainRef.current = false;
    if (replayRevealFrameRef.current !== undefined) {
      cancelAnimationFrame(replayRevealFrameRef.current);
      replayRevealFrameRef.current = undefined;
    }
    if (!active) {
      containerRef.current?.style.setProperty("visibility", "hidden");
      setTailReady(false);
      visibilityRef.current?.park();
      return;
    }
    containerRef.current?.style.setProperty("visibility", "hidden");
    setTailReady(false);
    let cancelled = false;
    let frame: number | undefined;
    const returningViewport = preservedViewportRef.current;
    const restoreViewport = () => {
      resizeRef.current?.();
      claimResizeRef.current?.();
      restoreTerminalViewport(termRef.current, returningViewport);
    };
    // Measure the now-mounted stage before parsing held output. Once xterm has
    // consumed that output, one final frame lets its canvas and viewport settle;
    // only then may the surface paint.
    restoreViewport();
    const settle = () => {
      if (cancelled || !activeRef.current) return;
      restoreViewport();
      frame = requestAnimationFrame(() => {
        if (cancelled || !activeRef.current) return;
        restoreViewport();
        // A replay mid-rebuild keeps the curtain: lifting it here would show
        // the rest of its history printing. Its own completion reveals the
        // pane (see `replayToPane`).
        if (replayCurtainRef.current) return;
        const reveal = () => {
          restoreViewport();
          setTailReady(true);
          containerRef.current?.style.removeProperty("visibility");
          if (preservedViewportRef.current === returningViewport) {
            preservedViewportRef.current = null;
          }
        };
        // Taking the stage announces a new size, and an agent answers a size
        // by painting its whole screen again — the same redraw the replay path
        // waits out, arriving just as late. Reveal once this pane is quiet.
        const settleFn = settleRebuildRef.current;
        if (settleFn) settleFn(replayGenerationRef.current, reveal);
        else reveal();
      });
    };
    const afterFlush = () => {
      if (cancelled || !activeRef.current) return;
      restoreViewport();
      // The held flush joins the END of xterm's write queue, but a write that
      // reached the terminal earlier — a replay flushed while this pane was
      // hidden — may still be mid-parse, and settling now would lift the
      // curtain onto its tail printing. An empty write is a queue barrier:
      // its callback fires only after everything already queued has parsed.
      const term = termRef.current;
      if (term) term.write("", settle);
      else settle();
    };
    const visibility = visibilityRef.current;
    if (visibility) visibility.show(afterFlush);
    else afterFlush();
    return () => {
      cancelled = true;
      if (frame !== undefined) cancelAnimationFrame(frame);
    };
  }, [active]);

  /*
   * Live restyle — no reconnect, so the running agent is untouched. The canvas
   * renderer caches rendered glyphs per colour in a texture atlas, so a theme
   * change has to invalidate it or the old palette keeps being painted.
   *
   * `terminalEpoch` is in here, and in the size effect below, for a reason the
   * appearance prop alone cannot cover: these effects fire on CHANGES, and the
   * terminal underneath them can be replaced without one. Every rebuild bumps
   * the epoch, so the pane restates the current theme and size to the new
   * terminal instead of trusting that it was born with them.
   */
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    term.options.theme = themeFor(appearance);
    term.clearTextureAtlas?.();
  }, [appearance, terminalEpoch]);

  // A pane that has been given room again forgets that its narrowness was
  // acknowledged — see `widthNoticeDismissed` and `narrowOverride`. Both answer
  // a question about the pane as it is NOW; a workspace that is widened and
  // later crowded a second time is told a second time.
  useEffect(() => {
    if (paneCols !== null && paneCols >= WORKABLE_COLS) {
      setWidthNoticeDismissed(false);
      setNarrowOverride(false);
    }
  }, [paneCols]);

  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    // A no-op on a terminal already built at this size (xterm's setter drops a
    // write of the identical value), which is what makes restating it on every
    // rebuild free.
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
  }, [fontSize, terminalEpoch]);

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
        //
        // The border colour is part of the transition, not just the shadow.
        // Every state below changes BOTH, and animating only one made the
        // change arrive twice: the edge snapped to yellow on the frame the
        // click landed, and the ring around it faded in over the next 150 ms.
        // On a grid where the focused pane is the one standing accent, that
        // read as a flicker rather than as a pane taking focus.
        "relative flex h-full w-full flex-col overflow-hidden rounded-lg border backdrop-blur-[4px]",
        "transition-[box-shadow,border-color,opacity] duration-150 ease-out motion-reduce:transition-none",
        focused &&
          "border-primary/60 shadow-[0_0_0_1px_hsl(var(--primary)/0.3)]",
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
        /*
         * The edge, and WHO is allowed to paint it.
         *
         * An inline colour beats every class, so this property decides whether
         * the three accent states above are drawn at all. It used to yield to
         * `focused` alone — which meant a pane being dragged, or one that had
         * just been handed a prompt, kept its plain grey edge and showed only
         * the shadow half of its own highlight. All three yield now.
         *
         * What is left is the resting pane, and its edge is where the pane's
         * lifecycle can be read from across the workspace rather than by
         * landing on it: dimmer once the agent has exited, the terminal's own
         * red when it failed, unchanged while it is connecting or live. See
         * `PANE_CHROME.edge` for why only those two states are marked.
         */
        borderColor:
          focused || dragging || justDelivered
            ? undefined
            : chrome.edge[visibleStatus],
      }}
      data-testid={`agentic-pane-${name}`}
    >
      <PaneHeader
        workspaceId={workspaceId}
        status={visibleStatus}
        statusDetail={statusDetail}
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
        onOpenConversation={() => setHistoryOpen(true)}
      />
      {/*
        What went wrong, kept on screen for as long as it is true — and the one
        way out of it. See PaneStatusNotice for why this is a row of its own
        rather than a second button in the header.
      */}
      <PaneStatusNotice
        name={name}
        displayName={displayName}
        status={visibleStatus}
        detail={statusDetail}
        light={appearance === "light"}
        onRestart={onRestart}
      />
      {/*
        And the other reason a pane can be unreadable: it is simply too narrow
        for the agent inside it. Second, because a pane that has stopped or
        cannot be reached has a more specific answer and only one row to say it
        in. See PaneWidthNotice.

        Only while the terminal is actually being SHOWN at that width — which
        now means only after the reader waved the card away (`narrowOverride`).
        With the card up the same sentence would be on screen twice.
      */}
      {visibleStatus !== "exited" &&
        visibleStatus !== "error" &&
        !tooNarrow &&
        !widthNoticeDismissed && (
          <PaneWidthNotice
            name={name}
            displayName={displayName}
            cols={paneCols}
            light={appearance === "light"}
            onDismiss={() => setWidthNoticeDismissed(true)}
          />
        )}
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
        className={cn(
          "relative min-h-0 flex-1 overflow-hidden px-1.5 pb-0.5 pt-0.5",
          active && !tailReady && "invisible",
        )}
      >
        <div
          ref={containerRef}
          data-testid={`agentic-terminal-host-${name}`}
          // Read by ./index.css, which anchors the contents to the bottom for
          // the length of a drag — see the rule there for why that is the side
          // the unused ground belongs on.
          data-layout-busy={layoutBusy ? "true" : "false"}
          /*
           * Nothing sideways, ever. The terminal is fitted to this element (see
           * MIN_REAL_COLS), so there is no wider surface to scroll to — and a
           * pane that could scroll sideways is a pane drawing past its own
           * edge, which is the thing that read as terminals standing on one
           * another.
           */
          className={cn(
            "agentic-terminal-host h-full min-h-0 w-full overflow-hidden",
            // Out of sight while the card is up, but still LAID OUT — the fit
            // measures this element, and a pane that stopped being measurable
            // could never find out that its tile had grown back. Opacity rather
            // than `visibility`, which the replay curtain writes imperatively
            // on this same element and would fight over.
            tooNarrow && "opacity-0",
          )}
        />
        {/*
          The pane, when there is no room to be a terminal. Ahead of the receipt
          and the starting spinner in the DOM so both still land on TOP of it —
          proof that a prompt arrived is exactly as load-bearing on a pane that
          is showing a card as on one that is showing its agent. See
          PaneTooNarrowCard.
        */}
        {tooNarrow && (
          <PaneTooNarrowCard
            name={name}
            displayName={displayName}
            cols={paneCols}
            status={visibleStatus}
            activity={activity}
            activitySince={activitySince}
            worked={worked}
            recap={recap}
            appearance={appearance}
            maximized={maximized}
            onOpen={onToggleMaximize}
            onShowAnyway={() => {
              // The ref BEFORE the state, and not only for tidiness: the fit
              // below runs in this same tick and reads the ref, which the next
              // render would otherwise be the first to update. Setting only the
              // state here left the pane deciding "still too narrow" one last
              // time and the card never lifting.
              narrowOverrideRef.current = true;
              setNarrowOverride(true);
              // Take the tile's real width immediately rather than waiting for
              // the next layout change — the reader asked to see this pane NOW,
              // and nothing else is going to measure it.
              resizeRef.current?.();
            }}
          />
        )}
        <PaneConversationDialog
          terminal={name}
          open={historyOpen}
          onOpenChange={setHistoryOpen}
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
        {/* Not on a pane showing the card — that card already says "starting",
            in the same vocabulary, and two spinners on one tile is noise. */}
        {!painted && !tooNarrow && visibleStatus === "connecting" && (
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
  status,
  statusDetail,
  onArrangeStart,
  arranging = false,
  onOpenConversation,
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
  /** The socket's own view of this pane — the badge beside the call-sign. */
  status: PaneStatus;
  /** Whatever the socket said about that status, read in the badge's tooltip. */
  statusDetail?: string;
  /** Press on the header picks the pane up; absent leaves it undraggable. */
  onArrangeStart?: (event: React.PointerEvent) => void;
  arranging?: boolean;
  /** Opens the pane's recorded conversation — the mode-proof scroll history. */
  onOpenConversation?: () => void;
}) {
  const t = useT();
  const light = appearance === "light";
  // The title bar's brand voice, keyed to the PANE's ground rather than the
  // app theme — see PANE_BRAND in ./terminalThemes for why the two differ.
  const brand = PANE_BRAND[appearance];
  // What the split menu hangs off. The pane clips everything inside it, so the
  // menu is measured against this bar and drawn in front of the window — see
  // `anchorTo` in ./AgentPicker.
  const headerRef = useRef<HTMLElement | null>(null);
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

  /*
   * The bar's gesture explainer — our own card, not the browser's `title`.
   *
   * It used to be a native tooltip, and a native tooltip is drawn by the OS:
   * always the same white-and-black system box, square-cornered, blind to
   * the app's theme and to the brand. The explainer is the one piece of
   * teaching UI on the bar — it deserves the same design language as the bar
   * it explains. So it is a portal card now (the pane clips its children,
   * the same reason the recap card portals) carrying the SAME single
   * sentence, keyed to the PANE's own appearance so it reads in light and
   * dark alike.
   *
   * It stays strictly a tooltip: pointer-events: none, opened by a settled
   * hover on the bar itself (never over one of the bar's own controls, which
   * keep their small native labels), and dismissed by leaving, pressing, or
   * anything that moves the ground under it (scroll, resize).
   */
  const [tip, setTip] = useState<{
    left: number;
    top?: number;
    bottom?: number;
  } | null>(null);
  const tipTimer = useRef<number | undefined>(undefined);

  const cancelTip = () => {
    if (tipTimer.current !== undefined) {
      window.clearTimeout(tipTimer.current);
      tipTimer.current = undefined;
    }
  };
  const hideTip = () => {
    cancelTip();
    setTip(null);
  };
  const scheduleTip = () => {
    if (tip || tipTimer.current !== undefined) return;
    tipTimer.current = window.setTimeout(() => {
      tipTimer.current = undefined;
      const rect = headerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const left = Math.max(
        8,
        Math.min(rect.left + 8, window.innerWidth - 308),
      );
      const below = window.innerHeight - rect.bottom;
      // Flips above the bar when the pane sits at the bottom of the window —
      // an explainer that opens off-screen explains nothing.
      setTip(
        below < 150 && rect.top > below
          ? { left, bottom: window.innerHeight - rect.top + 6 }
          : { left, top: rect.bottom + 6 },
      );
    }, 500);
  };

  // The timer must not outlive the pane, and an open tip is positioned in
  // viewport coordinates — anything that moves the pane retires it.
  useEffect(() => cancelTip, []);
  useEffect(() => {
    if (!tip) return;
    const hide = () => setTip(null);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, [tip]);

  // The explainer's words: ONE plain sentence, the same one the native
  // tooltip carried — by the maintainer's explicit choice. A first redesign
  // broke it into chip-led rows, and that was more furniture than the answer
  // needed; the card is branded, not busy. Only wired gestures make it in —
  // a maximized pane cannot be dragged, so its sentence never claims it can.
  const tipText = [
    onArrangeStart
      ? `Drag ${name} by this bar to move it — drop it on another terminal to swap, or near an edge to place it there.`
      : "",
    onToggleMaximize
      ? onArrangeStart
        ? maximized
          ? "Double-click to put it back."
          : "Double-click to fill the workspace."
        : maximized
          ? `Double-click to put ${name} back in the grid.`
          : `Double-click to make ${name} fill the workspace.`
      : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <header
      ref={headerRef}
      data-testid={`pane-header-${name}`}
      // The grip. It is the header itself rather than a separate handle icon,
      // because that is where the gesture is already expected — every window on
      // every desktop is dragged by its title bar.
      //
      // The press is ignored when it lands on one of the header's own controls:
      // the buttons stop `mousedown`, which says nothing about `pointerdown`, so
      // without this check pressing Close would also pick the pane up and a
      // twitchy hand would move a pane it meant to shut.
      onPointerDown={(event) => {
        // A press is an answer, not a question — the explainer leaves.
        hideTip();
        if (!onArrangeStart) return;
        const target = event.target as HTMLElement | null;
        if (target?.closest("button, a, input, [role='menuitem']")) return;
        onArrangeStart(event);
      }}
      // A settled hover on the bar asks what the bar can do. The card yields
      // only to controls that carry their OWN native label (`[title]` — the
      // action cluster, the pencil, the seat chip), so two explanations never
      // stack. Everything else on the bar belongs to the bar — including the
      // recap line, which is a button but spans nearly the whole width: the
      // first version suppressed the card there, and "hover the title" is
      // exactly where people ask.
      onPointerOver={(event) => {
        const target = event.target as HTMLElement | null;
        if (target?.closest("[title], input, [role='menuitem']")) hideTip();
        else if (tipText) scheduleTip();
      }}
      onPointerLeave={hideTip}
      /*
       * Double-click the title bar to fill the workspace, and again to go back.
       *
       * The same gesture every window manager on every desktop already binds,
       * and the pane header is already the title bar — it is dragged like one.
       * The button beside it stays, because a gesture nothing on screen
       * advertises cannot be the only way in (the same rule the rename pencil
       * follows); this is the way people who never look for a button get there.
       *
       * Safe beside the drag above: `paneArrange` only lifts a pane once the
       * pointer has travelled `DRAG_THRESHOLD_PX`, so two clicks in one place
       * are two clicks and never a move. The control check is the same one — a
       * double-click on Close is a close, twice, not a maximize — and the
       * call-sign stops the event itself, because a double-click there already
       * means rename.
       */
      onDoubleClick={
        onToggleMaximize
          ? (event) => {
              hideTip();
              const target = event.target as HTMLElement | null;
              if (target?.closest("button, a, input, [role='menuitem']"))
                return;
              onToggleMaximize();
            }
          : undefined
      }
      className={cn(
        // No tinted strip of its own: the header shares the terminal's ground
        // and the border underneath is enough to say where the output begins.
        // Twelve tinted bands across the workspace were twelve horizontal
        // stripes the eye had to skip on the way to the text that matters.
        //
        // `min-h-7` rather than height by content: the bar holds a 24 px action
        // cluster, a 20 px rename field and a 16 px badge, and each of them
        // comes and goes on its own schedule (hover, rename, a status change).
        // Sized by whatever is in it, the header grew and shrank by a couple of
        // pixels under the pointer — and every one of those pixels is a row the
        // terminal underneath has to be refitted for. A floor holds it still.
        //
        // `overflow-hidden` is the other half of that promise: the left group
        // truncates and the action cluster keeps its width, so a narrow pane
        // ends in an ellipsis rather than pushing its own buttons off the edge.
        "group/header relative flex min-h-7 items-center justify-between gap-1.5 overflow-hidden border-b px-2 py-0.5",
        onArrangeStart && (arranging ? "cursor-grabbing" : "cursor-grab"),
      )}
      style={
        {
          borderColor: PANE_CHROME[appearance].border,
          // The focused pane's bar gets a whisper of the accent — one wash on
          // one pane, never twelve tinted bands (see the class note above).
          backgroundImage: focused
            ? `linear-gradient(180deg, ${brand.accentWash}, transparent)`
            : undefined,
          // Claims the touch gesture for the drag. Without it a touch that
          // starts on the header scrolls the workspace instead of lifting the
          // pane, and the drag never begins at all.
          touchAction: onArrangeStart ? "none" : undefined,
          // The bar's brand, published as variables so hover/focus states can
          // be CLASSES. An inline `color` beats every class (see PaneAction's
          // note), so anything that changes on interaction reads these instead
          // of receiving an inline style.
          "--pane-accent": brand.accent,
          "--pane-accent-soft": brand.accentSoft,
          "--pane-ink": brand.ink,
          "--pane-ink-muted": brand.inkMuted,
        } as React.CSSProperties
      }
    >
      {/* The brand hairline: a signal-yellow (gold, on paper) rule that sweeps
          from under the call-sign of THE focused pane. It is the workspace's
          "you are here" mark — drawn on one pane at a time, so it stays a mark
          rather than a uniform. Opacity, not mounting, so focus changes glide. */}
      <span
        aria-hidden="true"
        data-testid={`pane-header-accent-${name}`}
        className={cn(
          "pointer-events-none absolute inset-x-0 bottom-0 h-[2px] transition-opacity duration-200",
          focused ? "opacity-100" : "opacity-0",
        )}
        style={{
          background: `linear-gradient(90deg, ${brand.accent}, ${brand.accentSoft} 55%, transparent 92%)`,
        }}
      />
      <div className="flex min-w-0 flex-1 items-center gap-2">
        {draft !== null ? (
          /*
           * The call-sign, being typed.
           *
           * A form rather than a bare input so Enter saves the way it does in
           * every other name field in the app, and Escape closes it — the two
           * keys somebody renaming a pane will reach for without looking.
           *
           * It SHRINKS. A fixed 128 px field plus its two buttons is wider than
           * a pane in a twelve-pane wall has to spare, and beside an action
           * cluster that keeps its own width the surplus went somewhere: the
           * save and cancel buttons were pushed under the cluster, so the one
           * control the user needed next was the one they could not reach.
           */
          <form
            className="flex min-w-0 flex-1 items-center gap-1"
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
                "w-full min-w-0 max-w-[9rem] rounded-md px-2 py-0.5 font-display text-[13px] font-semibold tracking-tight outline-none",
                // The pane's own accent, not the app's: the vars are set on the
                // header, so a light pane in a dark app still edits in gold.
                "border border-[color:var(--pane-accent-soft)] transition-colors focus:border-[color:var(--pane-accent)] disabled:opacity-60",
              )}
              style={{ color: brand.ink, background: brand.chip }}
            />
            <button
              type="submit"
              disabled={saving || !draft.trim()}
              aria-label={`Save name for ${name}`}
              data-testid={`pane-rename-save-${name}`}
              className={cn(
                "flex h-5 w-5 shrink-0 items-center justify-center rounded text-[color:var(--pane-accent)]",
                "transition-colors duration-150 hover:bg-[color:var(--pane-accent-soft)] disabled:opacity-40",
                "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--pane-accent)]",
              )}
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
              className={cn(
                "flex h-5 w-5 shrink-0 items-center justify-center rounded",
                "transition-colors duration-150",
                "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--pane-accent)]",
                // On the pane's ground, so the pane's ink — not the app's
                // muted-foreground, which disagrees exactly in mixed mode.
                light
                  ? "text-[#6b6b73] hover:bg-scrim/10 hover:text-[#2b2b33]"
                  : "text-[#9a9aa5] hover:bg-sheen/10 hover:text-[#e8e8ec]",
              )}
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
              //
              // The event stops here: the bar underneath reads a double-click as
              // "fill the workspace", and one gesture must not do two things.
              // The call-sign is the more specific target, so it wins.
              onDoubleClick={
                onRename
                  ? (event) => {
                      event.stopPropagation();
                      setDraft(name);
                    }
                  : undefined
              }
              // No native `title` here: the rename gesture is a row in the
              // bar's own explainer card, which replaced the system tooltip.
              // The focused pane's call-sign wears the brand plate — filled
              // signal-yellow with black type on dark panes, gold with white
              // type on light ones. Every other name sits on a quiet chip so
              // the plate stays the workspace's one standing accent: a filled
              // badge on all twelve panes marked nothing, because a marker
              // everyone wears is a uniform. The colours come from PANE_BRAND
              // (the pane's own ground), never the app theme.
              className="shrink-0 rounded-md px-1.5 py-0.5 font-display text-[13px] font-semibold leading-none tracking-tight transition-[background-color,color,box-shadow] duration-150"
              style={
                focused
                  ? {
                      background: brand.accent,
                      color: brand.onAccent,
                      boxShadow: `0 1px 8px ${brand.accentSoft}`,
                    }
                  : { background: brand.chip, color: brand.ink }
              }
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
                    ? "text-[#6b6b73] hover:bg-scrim/10"
                    : "text-[#9a9aa5] hover:bg-sheen/10",
                )}
              >
                <Pencil className="h-3 w-3" />
              </button>
            )}
          </>
        )}
        {/*
          The pane's own state, in the colour language the rest of the section
          already speaks (see ./PaneActivityPill, which owns the vocabulary and
          is the same badge the chat rail shows).

          Only three of the four states are news. `live` is a property of the
          PIPE — true for nearly every pane nearly all the time — so a standing
          dot on all twelve headers would mark nothing, which is exactly the
          badge the activity pill was written to replace. It is kept in the DOM
          and fades in with the rest of the header's controls, so the answer is
          one hover away for anyone who wants it, while `connecting`, `exited`
          and `error` announce themselves whether or not anyone is pointing.
        */}
        <span
          data-testid={`pane-status-${name}`}
          data-status={status}
          className={cn(
            "flex shrink-0 items-center transition-opacity duration-200",
            status === "live"
              ? "opacity-0 group-hover/header:opacity-60"
              : "opacity-100",
          )}
        >
          <PaneActivityPill status={status} detail={statusDetail} />
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
            // Allowed to give way (`min-w-0`, no `shrink-0`): which seat a pane
            // bills is worth a badge, but never worth pushing the call-sign or
            // the pane's own state off a narrow header to say it.
            className="min-w-0 max-w-[8rem] truncate rounded-full px-2 py-px font-display text-[10px] font-medium tracking-wide"
            style={{
              color: brand.inkFaint,
              backgroundColor: brand.chip,
              boxShadow: `inset 0 0 0 1px ${PANE_CHROME[appearance].border}`,
            }}
            title={`Running on ${accountLabel}`}
            data-testid={`pane-account-${name}`}
          >
            {accountLabel}
          </span>
        )}
      </div>

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
        {/* The one scroll-history entry point that cannot flicker away: the
            CLI may flip its scroll-owner mode mid-session (Claude Code does),
            but the pane's recorded conversation is always openable. */}
        <PaneAction
          label={t("agentic_grid.conversation.open").replace("{0}", name)}
          testId={`pane-conversation-${name}`}
          light={light}
          onClick={onOpenConversation}
        >
          <BookOpenText className="h-3.5 w-3.5" aria-hidden="true" />
        </PaneAction>
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
          <SplitRightIcon className="h-3.5 w-3.5" />
        </PaneAction>
        <PaneAction
          label={`Split ${name} and open a terminal below it`}
          testId={`pane-split-down-${name}`}
          light={light}
          disabled={splitDisabled}
          expanded={offersChoice ? picking === "down" : undefined}
          onClick={onSplit ? () => startSplit("down") : undefined}
        >
          <SplitBelowIcon className="h-3.5 w-3.5" />
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
          // Measured against this bar and drawn in front of the window. A pane
          // is `overflow-hidden` by necessity, so a menu positioned inside one
          // is cut off at its edge — in a twelve-pane wall that left a sliver
          // of the first entry and nothing to pick from. It also flips above
          // the header when the pane sits at the bottom of the screen.
          anchorTo={headerRef.current}
          onDismiss={() => setPicking(null)}
          onPick={(agent) => {
            setPicking(null);
            onSplit?.(picking, agent);
          }}
        />
      )}

      {/* Suppressed while the bar is doing something else: mid-drag, with the
          split menu open, or while the call-sign is being renamed — a card
          that teaches gestures must never sit on top of one in progress. */}
      {tip &&
        tipText !== "" &&
        !arranging &&
        picking === null &&
        draft === null &&
        typeof document !== "undefined" &&
        createPortal(
          <PaneHeaderTip
            name={name}
            appearance={appearance}
            pos={tip}
            text={tipText}
          />,
          document.body,
        )}
    </header>
  );
}

/**
 * The title bar's gesture explainer — the branded card that replaced the
 * native `title` tooltip (see the long note in PaneHeader for why).
 *
 * Deliberately the SAME one sentence the system tooltip carried, redressed:
 * the maintainer liked the words and rejected a chip-and-row redesign of
 * them — the ask was brand, not furniture. So the card is exactly a rounded
 * corner, the brand hairline, and the sentence, coloured off PANE_BRAND /
 * PANE_CHROME — the PANE's own ground — so it reads in light mode, dark
 * mode, and the mixed configurations. `pointer-events: none` keeps it a
 * label, never a thing the pointer can land on.
 */
function PaneHeaderTip({
  name,
  appearance,
  pos,
  text,
}: {
  name: string;
  appearance: TerminalAppearance;
  pos: { left: number; top?: number; bottom?: number };
  text: string;
}) {
  const brand = PANE_BRAND[appearance];
  const light = appearance === "light";
  return (
    <div
      role="tooltip"
      data-testid={`pane-header-tip-${name}`}
      className={cn(
        "pointer-events-none fixed z-[70] w-max max-w-[360px] overflow-hidden rounded-xl border",
        "animate-in fade-in-0 duration-150",
        pos.top !== undefined ? "slide-in-from-top-1" : "slide-in-from-bottom-1",
      )}
      style={{
        left: pos.left,
        ...(pos.top !== undefined ? { top: pos.top } : { bottom: pos.bottom }),
        // Nearly opaque, unlike the pane's glass: the card can land on top of
        // another pane's text, and an explainer must not be read THROUGH.
        background: light ? "rgba(252,251,248,0.97)" : "rgba(13,14,18,0.96)",
        borderColor: PANE_CHROME[appearance].border,
        boxShadow: "0 14px 36px -14px rgba(0,0,0,0.6)",
      }}
    >
      {/* The same brand hairline the focused pane wears under its bar. */}
      <span
        aria-hidden="true"
        className="block h-[2px]"
        style={{
          background: `linear-gradient(90deg, ${brand.accent}, ${brand.accentSoft} 60%, transparent 95%)`,
        }}
      />
      <p
        className="px-3.5 py-2.5 text-[11.5px] leading-relaxed"
        style={{ color: brand.ink }}
      >
        {text}
      </p>
    </div>
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
        "flex h-6 w-6 shrink-0 items-center justify-center rounded",
        // Colour AND background, over the same 150 ms: hovering used to change
        // only the ground behind the glyph, which on a translucent pane is a
        // very small amount of contrast to confirm the pointer is on target.
        "transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-30",
        // Reachable by keyboard and visibly so. The cluster is revealed by
        // `focus-within`, which is worth nothing if the focused button then
        // looks identical to its four neighbours. The ring reads the header's
        // brand variable, so it is yellow on dark panes and gold on light ones.
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--pane-accent)]",
        // The resting colour is a CLASS rather than an inline style, and that is
        // load-bearing rather than tidiness: an inline `color` beats every
        // class, so the hover colour below would simply never take effect.
        light ? "text-[#55555e]" : "text-[#a8a8b2]",
        danger
          ? "hover:bg-destructive/20 hover:text-destructive"
          : light
            ? "hover:bg-scrim/10 hover:text-[#2b2b33]"
            : "hover:bg-sheen/10 hover:text-[#e8e8ec]",
      )}
    >
      {children}
    </button>
  );
}

/**
 * The two tones a pane notice comes in, resolved against the PANE's own ground.
 *
 * Not the app's `--destructive` / amber tokens, and that is the whole reason
 * this table exists rather than a call to the section's shared `Notice`: the
 * terminal appearance is a separate setting from the app theme (plenty of
 * people run dark panes in a light app, and the reverse — see the appearance
 * note in ./AgenticGrid). A token picked for the app would land on the wrong
 * ground in exactly those two configurations, which is where a warning is least
 * affordable. The hues are the ones the pane's own palette already uses for
 * yellow and red (./terminalThemes), so a notice reads as part of the terminal
 * rather than as the app leaning in over it.
 */
const NOTICE_TONE: Record<
  "warning" | "error",
  Record<TerminalAppearance, { border: string; text: string }>
> = {
  warning: {
    light: { border: "rgba(154,103,0,0.65)", text: "#8a5a00" },
    dark: { border: "rgba(255,214,10,0.55)", text: "#ffd479" },
  },
  error: {
    light: { border: "rgba(192,57,43,0.7)", text: "#b3261e" },
    dark: { border: "rgba(255,107,94,0.6)", text: "#ff8b80" },
  },
};

/**
 * What went wrong in this pane, and the one way out of it.
 *
 * ## Why a row of its own rather than a badge
 *
 * Both halves of this used to be somewhere else, and neither survived being
 * there. The reason was written INTO the terminal — an exit banner, or one
 * trouble line per kind of trouble (see `troubleShown`) — where the next thing
 * the pane draws scrolls it away and nothing brings it back; a user returning
 * to a dead pane found a still screen and no sentence explaining it. The way
 * out was a small button in the header, in the cluster that hides until the
 * pane is hovered or focused, competing for width with five others.
 *
 * A dead pane can afford the height. Its terminal is not being drawn into any
 * more, so the ~24 px this costs comes out of a static screen — and it buys the
 * two things that pane owes the user: what happened, and what to press.
 *
 * ## Why the live states show nothing
 *
 * `connecting` and `live` are answered better elsewhere and answered already:
 * the starting overlay covers a pane that has not painted yet, and the badge in
 * the header carries the state for anyone who looks. A standing strip for them
 * would take a row off every healthy pane in the workspace to say "fine".
 */
function PaneStatusNotice({
  name,
  displayName,
  status,
  detail,
  light,
  onRestart,
}: {
  name: string;
  displayName: string;
  status: PaneStatus;
  detail?: string;
  light: boolean;
  onRestart?: () => void;
}) {
  if (status !== "exited" && status !== "error") return null;
  const tone = NOTICE_TONE[status === "error" ? "error" : "warning"][
    light ? "light" : "dark"
  ];
  /*
   * The two details are written for different sentences and cannot be shown the
   * same way. A trouble message is already one ("This terminal is no longer part
   * of the open workspace."); an exit reason is a CLAUSE — `explainExit` returns
   * "stopped", or "stopped unexpectedly (exit code 2) — use Restart to bring it
   * back" — which needs the agent's name in front of it to be a sentence at all.
   * Shown raw, a dead pane said nothing but "stopped".
   */
  const message =
    status === "error"
      ? detail || `${name} could not be reached.`
      : detail
        ? `${displayName} ${detail}`
        : `${displayName} is no longer running in ${name}.`;
  return (
    <div
      data-testid={`pane-notice-${name}`}
      data-tone={status === "error" ? "error" : "warning"}
      // Announced once, quietly. `polite` rather than `assertive`: a pane going
      // quiet is news, but it is never more urgent than the sentence a screen
      // reader is in the middle of.
      role="status"
      aria-live="polite"
      // The shared section's notice shape — a rule down the left edge, no fill,
      // no icon — one size down for a pane header's scale. A filled box here
      // would read as a second, louder terminal sitting on top of the first.
      className="flex shrink-0 items-center gap-2 border-l-2 px-2 py-1 text-[11px] leading-tight"
      style={{ borderColor: tone.border, color: tone.text }}
    >
      <span className="min-w-0 flex-1 truncate" title={message}>
        {message}
      </span>
      {onRestart && (
        <button
          type="button"
          aria-label={`Restart ${name}`}
          title={`Start a fresh ${displayName} in ${name}`}
          data-testid={`pane-restart-${name}`}
          onClick={(e) => {
            e.stopPropagation();
            onRestart();
          }}
          onMouseDown={(e) => e.stopPropagation()}
          className={cn(
            "flex shrink-0 items-center gap-1 rounded bg-primary/20 px-2 py-0.5",
            "text-[11px] font-medium text-primary",
            "transition-colors duration-150 hover:bg-primary/30",
            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/70",
          )}
        >
          <RotateCcw className="h-3 w-3" aria-hidden="true" />
          Restart
        </button>
      )}
    </div>
  );
}

/**
 * What a pane shows when its tile is too narrow for the agent to draw in.
 *
 * ## The failure this replaces
 *
 * Opening more terminals re-fits every pane already open — the backend gives
 * each new one a full-height column (`layout_tree.append_pane`), so seven panes
 * become twelve and each one loses half its width. Below roughly
 * {@link WORKABLE_COLS} a coding CLI does not degrade into a small tidy
 * interface. It repaints by erasing the rows it last drew, and once its own
 * line count stops matching the screen the repaint erases more than it rewrites:
 * panes that had been working for an hour came back BLANK and nothing later
 * brought them back (reported 2026-08-13 with two screenshots; the same pair of
 * symptoms — one pane printing one character per line, the rest silently stuck
 * — was measured on 2026-08-09 at thirteen panes).
 *
 * It is not a Claude Code bug to route around. Every full-screen TUI that
 * repaints relatively has the same failure, which is why this is keyed on the
 * measured width alone and names no product: Codex and any CLI added next year
 * are covered with no code here and none there.
 *
 * ## Why a card instead of the four things tried before
 *
 * The rule this must not break is the maintainer's (2026-08-11): a terminal is
 * exactly as wide as its tile, and every character in that tile is visible.
 * Clipping broke it, auto-shrinking the font silently overrode the toolbar,
 * widening on hover shuffled the workspace under the cursor, and simply telling
 * the user (`PaneWidthNotice`) left the wreckage on screen.
 *
 * A card breaks none of it, because there is no terminal in the tile to be
 * wrong about. The agent keeps the last width it could lay out in (see
 * `heldColsRef`), so it is not squeezed, not repainted into a corner, and not
 * wrecked — it is simply not SHOWN until there is room. Maximize the pane or
 * close a few and the terminal comes back exactly as it was, mid-sentence.
 *
 * ## What it says
 *
 * The state, in a word, at a size that reads from across a wall of twelve
 * panes — the header's own pill is eight pixels and fades out at rest. Then the
 * pane's recap if it has one, then the arithmetic, then the two ways out. The
 * second of those, "Show it anyway", restores precisely the old behaviour for
 * this one pane: a reader who wants to watch a 40-column terminal is not
 * somebody this should argue with.
 */
function PaneTooNarrowCard({
  name,
  displayName,
  cols,
  status,
  activity,
  activitySince,
  worked,
  recap,
  appearance,
  maximized,
  onOpen,
  onShowAnyway,
}: {
  name: string;
  displayName: string;
  cols: number | null;
  status: PaneStatus;
  activity: PaneActivity;
  activitySince: number;
  worked: boolean;
  recap?: string;
  appearance: TerminalAppearance;
  maximized: boolean;
  onOpen?: () => void;
  onShowAnyway: () => void;
}) {
  const chrome = PANE_CHROME[appearance];
  const brand = PANE_BRAND[appearance];
  const state = paneActivityLabel(status, activity, worked);
  return (
    <div
      data-testid={`pane-too-narrow-${name}`}
      data-cols={cols ?? ""}
      data-activity={activity || status}
      role="status"
      aria-live="polite"
      /*
       * Opaque, and covering the whole terminal region. The terminal underneath
       * is still mounted, still parsing its agent's output and still being
       * measured — it is only out of sight (see the `opacity-0` on its host) —
       * so anything translucent here would show the very fragments this exists
       * to keep off the screen.
       */
      className="absolute inset-0 flex flex-col items-start gap-1.5 overflow-hidden px-2 py-2 text-left"
      // The terminal's OWN ground, from the same per-appearance table the pane
      // is drawn with — never a hardcoded colour, and never one mode's.
      style={{ background: themeFor(appearance).background }}
    >
      <div className="flex w-full min-w-0 items-center gap-1.5">
        <PaneActivityPill
          status={status}
          activity={activity}
          since={activitySince}
          worked={worked}
        />
        <span
          className="min-w-0 flex-1 truncate font-display text-[13px] font-medium"
          style={{ color: brand.ink }}
        >
          {state}
        </span>
      </div>
      {/*
        The sentence the header already carries — repeated here because the
        header truncates it to a single narrow line and this is the only surface
        in the pane with room to let it wrap.
      */}
      {recap && (
        <p
          className="line-clamp-4 w-full text-[11px] leading-snug"
          style={{ color: brand.inkMuted }}
          title={recap}
        >
          {recap}
        </p>
      )}
      <div className="flex-1" />
      <p className="w-full text-[10px] leading-tight" style={{ color: brand.inkFaint }}>
        {cols === null
          ? `Too narrow for ${displayName} to draw in.`
          : `${cols} columns — ${displayName} needs about ${WORKABLE_COLS}.`}
      </p>
      <div className="flex w-full flex-wrap items-center gap-1">
        {/*
          Not rendered on a pane that is already maximized: there is no bigger
          it can be, and a button that cannot change anything is worse than no
          button on a surface this small.
        */}
        {onOpen && !maximized && (
          <button
            type="button"
            data-testid={`pane-too-narrow-open-${name}`}
            onClick={(e) => {
              e.stopPropagation();
              onOpen();
            }}
            onMouseDown={(e) => e.stopPropagation()}
            className={cn(
              "rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors duration-150",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/70",
            )}
            style={{ background: brand.accent, color: brand.onAccent }}
          >
            Open it
          </button>
        )}
        <button
          type="button"
          data-testid={`pane-too-narrow-anyway-${name}`}
          title={`Draw ${name} at whatever width it has`}
          onClick={(e) => {
            e.stopPropagation();
            onShowAnyway();
          }}
          onMouseDown={(e) => e.stopPropagation()}
          className={cn(
            "rounded px-1.5 py-0.5 text-[10px] transition-colors duration-150 hover:bg-foreground/10",
            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/70",
          )}
          style={{ color: brand.inkFaint, boxShadow: `inset 0 0 0 1px ${chrome.border}` }}
        >
          Show it anyway
        </button>
      </div>
    </div>
  );
}

/**
 * Say that this pane is too narrow for its agent, rather than letting the agent
 * prove it in one-character columns.
 *
 * The gap this closes is between what is TRUE and what is VISIBLE. Everything
 * about a narrow pane is already working exactly as designed — the tile is
 * measured honestly, the agent is told that measurement, and it draws the best
 * frame it can into it (see WORKABLE_COLS for the three attempts to do
 * something else and why none of them survived). But a coding CLI below its
 * usable width does not produce a small, tidy interface. It reserves its gutter
 * out of what little there is and lays the rest out one and two characters
 * wide, and the result is indistinguishable from this app rendering the
 * terminal wrong — which is what it was reported as (2026-08-11), twice, by
 * someone reading a workspace that was doing precisely what it was told.
 *
 * So the pane answers the question the screen raises: it states its own width
 * against the number the agent needs, and nothing more. Every way out —
 * maximizing the pane, a smaller text size, fewer panes across — belongs to
 * controls the header and toolbar already carry. The notice once offered a
 * "Widen" button of its own; the maintainer removed it (2026-08-11), so this
 * row informs and never acts.
 *
 * Deliberately NOT an error tone. Nothing has failed, and nothing needs
 * restarting; the workspace is simply asking more of the window than it has.
 */
function PaneWidthNotice({
  name,
  displayName,
  cols,
  light,
  onDismiss,
}: {
  name: string;
  displayName: string;
  cols: number | null;
  light: boolean;
  onDismiss: () => void;
}) {
  // Nothing measured yet, or a tile that gives its agent room to work.
  if (cols === null || cols >= WORKABLE_COLS) return null;
  const tone = NOTICE_TONE.warning[light ? "light" : "dark"];
  const message =
    `${cols} columns — ${displayName} needs about ${WORKABLE_COLS} to draw ` +
    `its interface. Below that it wraps into fragments.`;
  return (
    <div
      data-testid={`pane-width-notice-${name}`}
      data-tone="warning"
      data-cols={cols}
      role="status"
      aria-live="polite"
      // The same shape as the status notice above it — a rule down the left
      // edge, no fill, no icon. A second, louder box in a pane header would
      // cost more of the terminal than the sentence is worth.
      className="flex shrink-0 items-center gap-2 border-l-2 px-2 py-1 text-[11px] leading-tight"
      style={{ borderColor: tone.border, color: tone.text }}
    >
      <span className="min-w-0 flex-1 truncate" title={message}>
        {message}
      </span>
      {/*
        Retire it. A crowded workspace shows this on every pane at once, and
        the trade may well be one the user made on purpose — a warning they
        cannot switch off would be the next thing reported.
      */}
      <button
        type="button"
        aria-label={`Stop telling me that ${name} is narrow`}
        title="I know — leave it"
        data-testid={`pane-width-dismiss-${name}`}
        onClick={(e) => {
          e.stopPropagation();
          onDismiss();
        }}
        onMouseDown={(e) => e.stopPropagation()}
        className={cn(
          "flex h-4 w-4 shrink-0 items-center justify-center rounded",
          "transition-colors duration-150 hover:bg-foreground/10",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/70",
        )}
      >
        <X className="h-3 w-3" aria-hidden="true" />
      </button>
    </div>
  );
}

/*
 * The pane badge used to live here and reported the SOCKET: "live" for any pane
 * with a working pipe, which is nearly all of them nearly all of the time. It
 * moved to ./PaneActivityPill, which answers the question people were actually
 * reading it for — is this agent still working — and still reports the pipe in
 * the three cases where the pipe is the news.
 */
