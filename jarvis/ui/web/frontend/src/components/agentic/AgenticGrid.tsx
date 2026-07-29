/**
 * The running Agentic-IDE workspace: a grid of named agent terminals, a toolbar
 * (focus mode, appearance, font size, close), and one prompt bar that types into
 * whichever terminal is selected.
 *
 * The prompt bar is deliberately the same channel Jarvis uses by voice — it
 * POSTs to `/terminals/{name}/prompt` rather than writing into xterm locally.
 * So "click Mika, type 'run the tests'" and saying "tell Mika to run the tests"
 * take the identical path through the app, and the two can never behave
 * differently.
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  ArrowUp,
  Brain,
  Check,
  ChevronUp,
  FileText,
  FolderGit2,
  GripVertical,
  Image as ImageIcon,
  ListChecks,
  Loader2,
  Minus,
  Moon,
  Plus,
  Power,
  Sun,
  Trash2,
  Type,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useThemeValue } from "@/hooks/useTheme";
import { useDocumentVisible } from "@/hooks/useDocumentVisible";
import { useResizablePane } from "@/hooks/useResizablePane";
import { PaneResizer } from "@/components/layout/PaneResizer";
import { useEventStore } from "@/store/events";
import {
  AgenticTerminal,
  PaneStatusPill,
  type PaneStatus,
  type SplitAgentChoice,
  type SplitDirection,
} from "./AgenticTerminal";
import type { TerminalAppearance } from "./terminalThemes";
import {
  bandCapacityFor,
  GRID_HORIZONTAL_PADDING_PX,
  MIN_PANE_HEIGHT_PX,
} from "./layout";
import {
  paneLayout,
  remapColumnWeights,
  weightsAfterSplit,
  type PaneBox,
  type PaneSeam,
  type PaneWeights,
} from "./paneLayout";
import { usePaneWeights } from "./usePaneWeights";
import { ContinueInterrupted } from "./ContinueInterrupted";
import { PaneNotifications } from "./PaneNotifications";
import { PromptPreview } from "./PromptPreview";
import { WorkspaceSettings } from "./WorkspaceSettings";
import { usePaneFileDrag } from "./paneFileDrag";
import {
  extractPaneDrop,
  extractPasteFiles,
  isEmptyPayload,
  nameClipboardFile,
  type PaneDropPayload,
} from "./paneDrop";
import { usePaneArrange, type DropZone } from "./paneArrange";
import {
  addTerminal,
  attachToTerminal,
  closeTerminal,
  closeTerminals,
  composePrompt,
  moveTerminal,
  clearTerminalRecap,
  fetchTerminalRecaps,
  refreshTerminalRecap,
  setTerminalRecap,
  promptTerminal,
  type ComposedPreview,
  type DropAttachment,
  type IdeAccountState,
  type IdeState,
  type PaneNotification,
  type SessionState,
  type TerminalRecap,
} from "@/lib/agenticIdeApi";

interface AgenticGridProps {
  session: SessionState;
  focusMode: boolean;
  onToggleFocus: (enabled: boolean) => void;
  onClose: () => void;
  busy?: boolean;
  /** Hard cap on panes, so the split buttons can disable themselves. */
  maxTerminals?: number;
  /** Coding CLIs a split may start — the pane split menus offer these. */
  agents?: SplitAgentChoice[];
  /** Adding or closing a pane changes the workspace — the owner re-reads it. */
  onSessionChanged?: (session: SessionState) => void;
  /**
   * Which subscription new terminals open on, per coding CLI. Drives the
   * settings panel; an empty list simply leaves it with nothing to show.
   */
  accounts?: IdeAccountState[];
  /** The settings panel changed the workspace state — the owner applies it. */
  onStateChanged?: (state: IdeState) => void;
  /**
   * Is the section holding this grid the one on screen?
   *
   * The Agentic IDE is hidden rather than unmounted when the user goes to
   * another section, so this grid stays alive behind whatever they are looking
   * at — and its polling would otherwise go on asking the backend what a dozen
   * panes are doing for nobody. Defaults to true, which is what a grid rendered
   * on its own has always been.
   */
  onScreen?: boolean;
  /**
   * The row of open workspaces, rendered INSIDE this workspace's toolbar.
   *
   * It arrives as a node rather than being rendered above the grid because the
   * two belonged on one line all along: the tabs say which workspace you are
   * in, the toolbar acts on it, and neither fills a row on its own. Kept apart
   * they cost two lines of a view whose whole point is terminal output — with a
   * third above them for the app's own bar, the chrome was taller than a pane's
   * usable header area on a laptop screen.
   *
   * Left out (the wizard, tests) the toolbar simply names the project itself.
   */
  workspaceBar?: React.ReactNode;
  /**
   * The app's own chrome actions (Restart, and Update when one is offered),
   * pinned to the far right of this same row.
   *
   * They belong to the shell, not to this workspace — but the shell's bar does
   * not render in this section (see TopBar), because a full-width strip holding
   * two buttons above a wall of terminals was the third horizontal band in a
   * row. Passing them in keeps them on screen, which is the part that matters:
   * a frontend change reaches the user through that Restart button.
   */
  appActions?: React.ReactNode;
  /**
   * Take the user to a pane in ANOTHER workspace, on behalf of the header bell.
   *
   * A notification list spans every open tab — a pane in a background workspace
   * is precisely the one whose finishing nobody would otherwise notice — so
   * "jump to pane" sometimes means switching tab first. This grid cannot do
   * that: it is keyed by workspace and is replaced when one is switched to. The
   * view above owns both halves and hands the pane back through `jumpTo`.
   *
   * Left out, an entry from another workspace still lists; pressing its jump
   * does nothing rather than lying about where it went.
   */
  onJumpToWorkspace?: (workspaceId: string, pane: string) => void;
  /**
   * "Maximize this pane and scroll to it" — set by the view after a cross-
   * workspace jump has landed.
   *
   * Carries a `nonce` because the same pane may be jumped to twice in a row,
   * and a value that has not changed cannot re-fire an effect. It is not a
   * state the grid holds: acting on it is a one-shot, and what stays afterwards
   * is an ordinary maximized pane the user can restore themselves.
   */
  jumpTo?: { pane: string; nonce: number } | null;
}

const FONT_MIN = 10;
const FONT_MAX = 20;

/**
 * The part of the hovered pane a drop would take, drawn as it will look.
 *
 * A label alone ("Right of Mika") is a sentence to read mid-gesture; the filled
 * half is the answer at a glance, and it is the same shape the pane will
 * actually have afterwards. Swap fills the whole pane because that is exactly
 * what it takes over.
 */
const ZONE_BOX: Record<DropZone, string> = {
  swap: "inset-0",
  left: "inset-y-0 left-0 w-1/2",
  right: "inset-y-0 right-0 w-1/2",
  above: "inset-x-0 top-0 h-1/2",
  below: "inset-x-0 bottom-0 h-1/2",
};

/**
 * How often the pane headers re-read what their agents are doing.
 *
 * A recap is a glanceable label, not a live log — five seconds is well below
 * the time it takes to look across a grid, and slow enough that a dozen panes
 * cost one cheap request per pane per minute. The read is deliberately the
 * small `/recaps` one rather than the full workspace state (see the API
 * client), and it is skipped entirely while the window is in the background:
 * nobody is glancing at a header they cannot see.
 */
const RECAP_POLL_MS = 5000;

/*
 * How tall the prompt bar is — dragged by its top edge, and remembered.
 *
 * The split between "watch the agents" and "write to them" is not the same for
 * everyone or even for the same person an hour later: dictating a long brief
 * wants a tall input, watching eight agents build wants none of it. So the seam
 * is draggable rather than a value someone guessed once.
 *
 * Pulled all the way down the bar COLLAPSES to a thin strip instead of
 * vanishing. A control that can be dragged out of existence has no way back —
 * the strip keeps both the seam and a one-click reopen on screen.
 *
 * It now STARTS collapsed (see `defaultSize` below), which is the honest default
 * for what this view is: a wall of running terminals you mostly watch. A 176 px
 * writing surface held open under twelve panes cost each of them a sixth of
 * their height for an input box that is empty most of the time — and everything
 * typed there can be said out loud instead. One drag, one double-click on the
 * seam, or the strip's own button opens it, and that choice is remembered.
 */
const COMPOSER_DEFAULT_PX = 176;
/** Height of the collapsed strip: its reopen button and nothing else. */
const COMPOSER_COLLAPSED_PX = 28;
/** Below this dragged height the bar snaps shut rather than half-showing. */
const COMPOSER_COLLAPSE_AT_PX = 96;
/** Below this it is too short for the "you can also say this out loud" note. */
const COMPOSER_HINT_AT_PX = 190;
/**
 * Room the toolbar and a still-usable grid keep for themselves.
 *
 * Without it a tall prompt bar plus a short window squeezes the grid to zero,
 * every pane measures 0×0, and the panes' fit logic refuses to resize a
 * terminal to no columns — the workspace would look frozen rather than small.
 */
const GRID_RESERVED_PX = 200;

/*
 * How tightly the panes are packed.
 *
 * The first version used `gap-3 p-3`, which put 12 px between neighbours and
 * another 12 px around the outside. On a workspace of a dozen panes that is
 * ~60 px of window spent on nothing at all, and — the actual complaint — it
 * makes the grid read as a scattered set of cards rather than one wall of
 * terminals, which is what a tiling terminal looks like everywhere else.
 *
 * 4 px is enough to keep each pane's own border legible as its edge, and no
 * more. It is stated as a constant because the horizontal half of it also has
 * to reach `layout.ts`: the column count is computed from the grid's CONTENT
 * width, so a padding change the layout module does not know about would make
 * the wizard's preview and the running grid disagree about how many panes fit.
 */
const GRID_GAP_PX = 4;

/** Half of it — what each pane gives up on the sides it shares with a neighbour. */
const HALF_GAP_PX = GRID_GAP_PX / 2;

/** A maximized pane simply fills the workspace. */
const MAXIMIZED_BOX: React.CSSProperties = {
  position: "absolute",
  inset: 0,
};

/** True when a fraction is at an edge of the workspace, allowing for float drift. */
function atEdge(value: number): boolean {
  return value <= 0.0001 || value >= 0.9999;
}

/**
 * Where one pane is drawn.
 *
 * The percentage is the pane's share; the pixels are the gap around it. A pane
 * gives up half a gap on each side it SHARES with a neighbour and nothing on a
 * side that faces the workspace edge — so neighbours end up one full gap apart
 * while the outer margin stays the container's own padding, whichever pane
 * happens to be on the outside.
 */
function paneBoxStyle(box: PaneBox | undefined): React.CSSProperties {
  if (!box) return MAXIMIZED_BOX;
  const left = atEdge(box.x) ? 0 : HALF_GAP_PX;
  const right = atEdge(box.x + box.w) ? 0 : HALF_GAP_PX;
  const top = atEdge(box.y) ? 0 : HALF_GAP_PX;
  const bottom = atEdge(box.y + box.h) ? 0 : HALF_GAP_PX;
  // Plain percentages where no gap is subtracted: a pane against two edges is
  // the whole workspace, and `calc(100% - 0px)` only makes that harder to read
  // in the inspector.
  const span = (fraction: number, trim: number) =>
    trim === 0 ? `${fraction * 100}%` : `calc(${fraction * 100}% - ${trim}px)`;
  const start = (fraction: number, shift: number) =>
    shift === 0 ? `${fraction * 100}%` : `calc(${fraction * 100}% + ${shift}px)`;
  return {
    position: "absolute",
    left: start(box.x, left),
    top: start(box.y, top),
    width: span(box.w, left + right),
    height: span(box.h, top + bottom),
  };
}

/**
 * Where one seam is drawn — centred on the boundary, spanning what it divides.
 *
 * The grip is 6 px wide against a 4 px gap, so it deliberately overlaps its two
 * panes by a pixel each. A seam narrower than the gap it sits in would be a
 * coordination test rather than a control.
 */
function seamStyle(seam: PaneSeam): React.CSSProperties {
  const half = 3;
  return seam.orientation === "vertical"
    ? {
        position: "absolute",
        left: `calc(${seam.x * 100}% - ${half}px)`,
        top: `${seam.y * 100}%`,
        height: `${seam.h * 100}%`,
      }
    : {
        position: "absolute",
        top: `calc(${seam.y * 100}% - ${half}px)`,
        left: `${seam.x * 100}%`,
        width: `${seam.w * 100}%`,
      };
}

/**
 * The style properties a box or a seam is positioned by, and nothing else.
 *
 * Written straight onto the element while a seam is being dragged, so the
 * gesture never goes through React (see `paintDraggedLayout` and the header of
 * `usePaneWeights`). Kept to the four that `paneBoxStyle` and `seamStyle`
 * actually set, and blanking a missing one is what lets the same helper place
 * both: a vertical seam has a height and no width, a horizontal one the
 * reverse.
 */
const POSITION_KEYS = ["left", "top", "width", "height"] as const;

function writePosition(node: HTMLElement, style: React.CSSProperties): void {
  for (const key of POSITION_KEYS) {
    const value = style[key];
    node.style[key] = value === undefined ? "" : String(value);
  }
}

/*
 * Terminal appearance and text size are remembered, and the appearance follows
 * the app's own theme until the user says otherwise.
 *
 * The first version hardcoded a light default. In a dark app that reads as a
 * bug — you open the workspace, get a wall of white, and reach for the toggle
 * every single time. Following the app theme is the honest default, and once
 * someone deliberately picks the other one for their terminals (plenty of
 * people want dark panes in a light app, and the reverse), that choice sticks
 * instead of being re-decided on every visit.
 *
 * localStorage rather than config: this is a per-screen display preference of
 * this browser profile, not something worth a round-trip and a config write.
 */
const APPEARANCE_KEY = "jarvis.agenticIde.terminalAppearance";
const FONT_KEY = "jarvis.agenticIde.terminalFontSize";

function readStored<T>(key: string, parse: (raw: string) => T | null): T | null {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? null : parse(raw);
  } catch {
    // Private mode / storage disabled — fall back to the defaults rather than
    // taking the whole workspace down over a preference.
    return null;
  }
}

function writeStored(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* nothing to do — the preference just will not survive this session */
  }
}

/**
 * May Jarvis type into this pane?
 *
 * False for a plain terminal: that pane is a live SHELL prompt, so a line typed
 * into it from the outside would not be read by an agent — it would run as a
 * command. The prompt bar therefore never offers one as a target, and clicking
 * one does not steal the target from the agent you were writing to.
 *
 * Undefined means a backend that predates the flag, where every pane was an
 * agent — so absent reads as "yes", never as "no".
 */
function takesPrompts(term: { accepts_prompts?: boolean }): boolean {
  return term.accepts_prompts !== false;
}

function storedAppearance(): TerminalAppearance | null {
  return readStored(APPEARANCE_KEY, (raw) =>
    raw === "light" || raw === "dark" ? raw : null,
  );
}

function storedFontSize(): number | null {
  return readStored(FONT_KEY, (raw) => {
    const n = Number.parseInt(raw, 10);
    return Number.isFinite(n) && n >= FONT_MIN && n <= FONT_MAX ? n : null;
  });
}

export function AgenticGrid({
  session,
  focusMode,
  onToggleFocus,
  onClose,
  busy = false,
  maxTerminals = 12,
  agents,
  onSessionChanged,
  accounts = [],
  onStateChanged,
  workspaceBar,
  appActions,
  onJumpToWorkspace,
  jumpTo = null,
  onScreen = true,
}: AgenticGridProps) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const theme = useThemeValue();
  // No stored choice → follow the app. A stored one wins and keeps winning.
  const [appearance, setAppearanceState] = useState<TerminalAppearance>(
    () => storedAppearance() ?? theme,
  );
  const [fontSize, setFontSizeState] = useState(() => storedFontSize() ?? 13);

  // Track the app theme while the user has expressed no preference of their own,
  // so flipping the whole app to light does not leave black panes behind.
  useEffect(() => {
    if (storedAppearance() === null) setAppearanceState(theme);
  }, [theme]);

  const setAppearance = useCallback((next: TerminalAppearance) => {
    setAppearanceState(next);
    writeStored(APPEARANCE_KEY, next);
  }, []);

  const setFontSize = useCallback((next: number) => {
    setFontSizeState(next);
    writeStored(FONT_KEY, String(next));
  }, []);
  const [target, setTarget] = useState(
    session.terminals.find(takesPrompts)?.name ?? "",
  );
  const [statuses, setStatuses] = useState<Record<string, { status: PaneStatus; detail?: string }>>({});
  // What each pane is doing, by call-sign, as the header shows it. Kept beside
  // the session rather than inside it because it changes on a completely
  // different clock: the layout changes when a pane is opened or closed, a
  // recap whenever an agent prints a line.
  const [recaps, setRecaps] = useState<Record<string, TerminalRecap>>({});
  const [prompt, setPrompt] = useState("");
  const [sending, setSending] = useState(false);
  // The composed prompt waiting for the user's approval, and the wording they
  // originally typed. Both are kept so "Send verbatim" and Escape can hand the
  // original back — nothing typed here is ever lost behind the preview.
  const [preview, setPreview] = useState<ComposedPreview | null>(null);
  const [previewSource, setPreviewSource] = useState("");

  // Bumping a pane's token reconnects just that pane, which respawns its agent.
  // Keyed by call-sign so closing or splitting never disturbs the others.
  const [restartTokens, setRestartTokens] = useState<Record<string, number>>({});
  const restartPane = useCallback((name: string) => {
    setRestartTokens((prev) => ({ ...prev, [name]: (prev[name] ?? 0) + 1 }));
  }, []);

  const [maximized, setMaximized] = useState<string | null>(null);
  const [pendingClose, setPendingClose] = useState<string | null>(null);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedTerminals, setSelectedTerminals] = useState<Set<string>>(
    () => new Set(),
  );
  const [pendingSelectionClose, setPendingSelectionClose] = useState<
    string[] | null
  >(null);
  const selectionToggleRef = useRef<HTMLButtonElement | null>(null);
  const [workspaceCloseRequested, setWorkspaceCloseRequested] = useState(false);
  const [working, setWorking] = useState(false);

  const enterSelectionMode = useCallback(() => {
    // A maximized pane hides its neighbours, which makes multi-selection
    // impossible to understand. Restore the grid as selection begins.
    setMaximized(null);
    setSelectionMode(true);
  }, []);

  const leaveSelectionMode = useCallback(() => {
    setSelectionMode(false);
    setSelectedTerminals(new Set());
  }, []);

  const toggleTerminalSelection = useCallback((name: string) => {
    setSelectedTerminals((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  /*
   * Keep the pane headers current.
   *
   * Bound to the workspace ID, so switching tabs starts a fresh poll for the
   * workspace now on screen instead of continuing to describe the one behind
   * it. A failed read keeps whatever the headers already say: the backend
   * warming up, or a workspace closed in another window, is not a reason to
   * blank eight labels — and the recap is a convenience, never the pane.
   *
   * Also bound to whether anyone can SEE the headers, which is two independent
   * questions with one answer: the window may be minimized or behind another
   * (`visible`), and the user may be in another section of the app while this
   * grid stays mounted behind it (`onScreen` — see MainView). A poll that runs
   * regardless is not free: `/recaps` walks every pane's replay buffer through
   * the summarizer, on the same event loop that carries the wake microphone.
   *
   * Re-mounting the effect on the way back is what catches the headers up: a
   * grid that spent five minutes hidden skipped every tick, and its first act
   * on return is a fresh read rather than a five-second-old sentence.
   */
  const documentVisible = useDocumentVisible();
  const pollRecaps = onScreen && documentVisible;
  useEffect(() => {
    if (!pollRecaps) return;
    let cancelled = false;
    const pull = async () => {
      try {
        const answer = await fetchTerminalRecaps(session.id);
        if (cancelled) return;
        setRecaps(
          Object.fromEntries(answer.terminals.map((term) => [term.name, term])),
        );
      } catch {
        /* keep the last recaps — a stale sentence beats an empty header */
      }
    };
    void pull();
    const timer = window.setInterval(() => void pull(), RECAP_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [session.id, pollRecaps]);

  /*
   * The three things a user may do about a pane's recap.
   *
   * Each answers with the pane's new recap, and it is written into the polled
   * map straight away rather than waited for on the next tick: the poll runs
   * every five seconds, and a header that keeps the old sentence for four of
   * them after you pressed Save reads as a save that did not work.
   */
  const applyRecap = useCallback((row: TerminalRecap) => {
    setRecaps((current) => ({ ...current, [row.name]: row }));
  }, []);

  const recapActionsFor = useCallback(
    (name: string) => ({
      onSave: async (headline: string, detail: string) => {
        applyRecap(await setTerminalRecap(name, headline, detail, session.id));
      },
      onClear: async () => {
        applyRecap(await clearTerminalRecap(name, session.id));
      },
      onRefresh: async () => {
        applyRecap(await refreshTerminalRecap(name, session.id));
      },
    }),
    [applyRecap, session.id],
  );

  // A terminal can disappear because another client closed it. Keep the local
  // selection honest instead of leaving an invisible name selected.
  useEffect(() => {
    const live = new Set(session.terminals.map((terminal) => terminal.name));
    setSelectedTerminals((current) => {
      if (current.size === 0) return current;
      const next = new Set([...current].filter((name) => live.has(name)));
      return next.size === current.size ? current : next;
    });
  }, [session.terminals]);

  // Where each pane sits in the one grid below — coordinates, not nested
  // lists, so a layout change never re-parents a pane (see ./layout).
  // How many panes may share one band is a question about WIDTH, not a constant:
  // eight side by side leave ~18 characters each and the agent's output becomes
  // unreadable. So the grid measures itself and wraps once panes would starve.
  /*
   * CONTENT width — the box the grid tracks actually occupy, padding excluded.
   *
   * Which of the two widths this holds matters, and getting it wrong was a real
   * defect. `contentRect.width` already excludes this element's 12 px of padding
   * a side, but the value was then passed to `workspaceBandCapacityFor`, which
   * subtracts that same padding AGAIN — so the grid laid itself out 24 px
   * narrower than it is. The wizard preview measures an UNPADDED element, where
   * that helper is right, so the two flipped to a new column count at different
   * window widths and the preview appeared to lie. The seed value made it worse:
   * it came from `clientWidth`, which INCLUDES padding, so the grid could
   * silently re-column itself on the first resize after opening.
   *
   * Now each side states which width it holds: this one is already content, so
   * it calls `bandCapacityFor` directly; the wizard, holding an outer width,
   * subtracts the padding the grid will have via `workspaceBandCapacityFor`.
   */
  const gridRef = useRef<HTMLDivElement | null>(null);
  const [gridWidth, setGridWidth] = useState(0);
  // How tall the workspace's visible area is. Panes are sized as FRACTIONS of
  // it now rather than by grid tracks, so unlike the width this is not only a
  // wrapping question — it is the number a percentage height resolves against,
  // and the floor under it (below) is what makes the workspace scroll instead
  // of squeezing every pane to three unreadable rows.
  const [gridHeight, setGridHeight] = useState(0);
  useEffect(() => {
    const node = gridRef.current;
    if (!node) return;
    const fallback = () =>
      Math.max(0, node.clientWidth - GRID_HORIZONTAL_PADDING_PX);
    setGridWidth(fallback());
    setGridHeight(node.clientHeight);
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? fallback();
      // Round to a step so a one-pixel drift cannot churn the layout (and with
      // it every pane's resize) on window animations.
      setGridWidth(Math.round(width / 16) * 16);
      setGridHeight(Math.round(entries[0]?.contentRect.height ?? node.clientHeight));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  /*
   * The prompt bar's height, and the ceiling it may not cross.
   *
   * The ceiling is measured rather than guessed: on a 1440-tall window a 520 px
   * prompt bar is comfortable, on a 700-tall laptop the same value leaves the
   * panes unreadable. So the frame reports its height and the bar may take
   * everything except what the toolbar and a minimum grid need.
   */
  const frameRef = useRef<HTMLDivElement | null>(null);
  const [frameHeight, setFrameHeight] = useState(0);
  useEffect(() => {
    const node = frameRef.current;
    if (!node) return;
    setFrameHeight(node.clientHeight);
    const observer = new ResizeObserver((entries) => {
      const height = entries[0]?.contentRect.height ?? node.clientHeight;
      setFrameHeight(Math.round(height));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const composerMax = Math.max(
    COMPOSER_COLLAPSED_PX,
    (frameHeight || COMPOSER_DEFAULT_PX + GRID_RESERVED_PX) - GRID_RESERVED_PX,
  );
  const composer = useResizablePane({
    // v2: the default changed from "open at 176 px" to "collapsed", and a key
    // people already have a value under would have kept the old behaviour for
    // every existing install — which is precisely who asked for the change.
    storageKey: "jarvis.agenticIde.composerHeight.v2",
    defaultSize: COMPOSER_COLLAPSED_PX,
    min: COMPOSER_COLLAPSED_PX,
    max: composerMax,
    axis: "y",
    // The grip is the bar's TOP edge: dragging up must make the bar taller.
    handle: "start",
  });
  // Clamped again on render, because the window can shrink under a height that
  // was legal when it was stored — the remembered value stays untouched, so a
  // maximised window gets the tall prompt bar back.
  const requestedComposer = Math.min(composer.size, composerMax);
  const composerCollapsed = requestedComposer < COMPOSER_COLLAPSE_AT_PX;
  const composerHeight = composerCollapsed
    ? COMPOSER_COLLAPSED_PX
    : requestedComposer;

  /*
   * Double-clicking the seam TOGGLES rather than resets.
   *
   * `reset` means "back to the default", and the default is now the closed
   * strip — so wiring the seam to it would give a shut bar a double-click that
   * visibly does nothing, which reads as a dead control. Toggling keeps one
   * gesture for both directions, which is what a double-click on a collapsed
   * splitter does in every editor.
   */
  const { resize: resizeComposer } = composer;
  const toggleComposer = useCallback(() => {
    resizeComposer(composerCollapsed ? COMPOSER_DEFAULT_PX : COMPOSER_COLLAPSED_PX);
  }, [composerCollapsed, resizeComposer]);

  const perBand = useMemo(() => bandCapacityFor(gridWidth), [gridWidth]);

  /*
   * The sizes the panes are drawn at, and the seams between them.
   *
   * Kept apart from the arrangement on purpose: `session.terminals` says which
   * column and slot each pane is in and only the backend may change that, while
   * the weights say how much room each one gets and only this browser does. A
   * drag therefore never waits on a request, and a workspace opened on a second
   * machine is the same arrangement at that machine's own sizes.
   */
  const canvasRef = useRef<HTMLDivElement | null>(null);

  /*
   * The panes and seams as ELEMENTS, so a drag can move them itself.
   *
   * A drag re-lays the workspace out sixty times a second, and doing that
   * through React means re-rendering every terminal in it that often — which is
   * what made dragging a seam in a full workspace run at a few frames a second
   * (see `usePaneWeights`). With the nodes in hand a frame costs one layout
   * computation and a handful of style writes.
   *
   * The ref callbacks are memoised per pane and per seam for the reason
   * `usePaneArrange` memoises its own: a fresh closure each render makes React
   * detach and re-attach every node, so a drag could lose the element it is
   * halfway through moving.
   */
  const paneNodes = useRef(new Map<string, HTMLElement>());
  const seamNodes = useRef(new Map<string, HTMLElement>());
  const paneRefs = useRef(new Map<string, (element: HTMLElement | null) => void>());
  const seamRefs = useRef(new Map<string, (element: HTMLDivElement | null) => void>());

  const registerSeam = useCallback((id: string) => {
    const existing = seamRefs.current.get(id);
    if (existing) return existing;
    const callback = (element: HTMLDivElement | null) => {
      if (element) seamNodes.current.set(id, element);
      else seamNodes.current.delete(id);
    };
    seamRefs.current.set(id, callback);
    return callback;
  }, []);

  /**
   * Put every pane and seam where ``next`` says, without telling React.
   *
   * Used for the frames of a drag only. The last frame of a gesture writes the
   * same numbers that are then committed to state, so React's next render
   * agrees with what is already on screen and nothing flickers on release.
   */
  const paintDraggedLayout = useCallback(
    (next: PaneWeights) => {
      const live = paneLayout(session.terminals, perBand, next);
      session.terminals.forEach((term, index) => {
        const node = paneNodes.current.get(term.name);
        const box = live.boxes[index];
        // A pane with no box of its own is a maximized workspace, and a
        // maximized workspace has no seams to drag in the first place.
        if (node && box) writePosition(node, paneBoxStyle(box));
      });
      for (const seam of live.seams) {
        const node = seamNodes.current.get(seam.id);
        if (node) writePosition(node, seamStyle(seam));
      }
    },
    [session.terminals, perBand],
  );

  const sizes = usePaneWeights(
    session.id,
    useCallback(
      () => ({
        width: canvasRef.current?.clientWidth ?? gridWidth,
        height: canvasRef.current?.clientHeight ?? gridHeight,
      }),
      [gridWidth, gridHeight],
    ),
    paintDraggedLayout,
  );
  const layout = useMemo(
    () => paneLayout(session.terminals, perBand, sizes.weights),
    [session.terminals, perBand, sizes.weights],
  );

  /*
   * Hold the dragged sizes against anything else that renders mid-gesture.
   *
   * A drag deliberately leaves state alone until the pointer is released, so
   * any OTHER render in that window — the five-second recap poll, a pane
   * reporting that it went live — would repaint every box at the sizes the
   * workspace had when the drag started, and the panes would jump back under
   * the cursor. Re-applying the in-flight layout right after such a commit is
   * what keeps that from being visible.
   *
   * Deliberately without a dependency list, and deliberately cheap: outside a
   * drag it is one null check per commit and nothing else. It must not MEASURE
   * anything here — reading geometry in a commit is what forces the browser to
   * recompute the whole grid, which is half of what this change removes.
   */
  useLayoutEffect(() => {
    if (sizes.dragging === null) return;
    const inFlight = sizes.liveWeights.current;
    if (inFlight) paintDraggedLayout(inFlight);
  });

  /*
   * Is the workspace's own geometry in motion right now?
   *
   * The panes are told, and the reason is the second half of the resize
   * problem. A pane that notices it has changed size refits its terminal and
   * announces the new size to the agent behind it, which redraws its entire
   * screen in response. During a drag that is the wrong trade twice over: the
   * agent's redraw lands on the same thread that owes the user the next frame,
   * and it is thrown away by the next pixel of movement anyway. So the panes
   * hold still while the seam moves and take their new size in one pass when it
   * stops — which is also when the terminal's contents stop lagging behind the
   * frame around them.
   *
   * The prompt bar counts: dragging it changes the height of every pane above
   * it just as a seam does.
   */
  const layoutBusy = sizes.dragging !== null || composer.isResizing;

  /*
   * How tall the workspace is drawn, which is not always how tall it looks.
   *
   * Panes take a percentage of this, so it is also the floor that keeps them
   * readable: once the bands' tallest stacks cannot fit at `MIN_PANE_HEIGHT_PX`
   * each, the canvas grows past the window and the workspace scrolls rather
   * than shrinking every pane further. A maximized pane is exactly the window,
   * so it can never end up taller than the area it is being read in.
   */
  const canvasHeight =
    maximized !== null
      ? gridHeight
      : Math.max(gridHeight, layout.minHeightUnits * MIN_PANE_HEIGHT_PX);

  const atLimit = session.terminals.length >= maxTerminals;

  const split = async (
    anchor: string | null,
    direction: SplitDirection,
    agent?: string,
  ) => {
    setWorking(true);
    try {
      const next = await addTerminal({ anchor: anchor ?? undefined, direction, agent });
      onSessionChanged?.(next);
      // A fresh pane should receive the next prompt — that is why it was opened.
      const known = new Set(session.terminals.map((t) => t.name));
      const added = next.terminals.find((t) => !known.has(t.name));
      /*
       * A split HALVES the pane it was asked of, and touches nothing else.
       *
       * This is the behaviour people expect from every tiling terminal and
       * editor, and the one the old workspace did not have: a new pane became a
       * new full-height column, so every other pane on the line lost width to
       * make room for it. Splitting the pane you clicked should be a local
       * event — see `weightsAfterSplit`.
       */
      sizes.setWeights((current) =>
        anchor && added
          ? weightsAfterSplit(
              current,
              session.terminals,
              next.terminals,
              anchor,
              added.name,
              perBand,
            )
          : remapColumnWeights(current, session.terminals, next.terminals),
      );
      // ...unless it is a plain terminal — that one is typed into by hand, and
      // stealing the target would silently redirect the next prompt into a pane
      // that refuses it.
      if (added && takesPrompts(added)) setTarget(added.name);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setWorking(false);
    }
  };

  /*
   * A pane was dragged onto another one.
   *
   * The move is asked of the backend rather than applied here first. An
   * optimistic reorder would be a second implementation of the placement
   * arithmetic living in the browser, and the two would drift — the workspace on
   * disk is what a restart brings back, so the layout the user sees has to be
   * the layout the backend actually recorded.
   */
  const movePane = useCallback(
    async (moved: string, target: string, zone: DropZone) => {
      setWorking(true);
      try {
        const next = await moveTerminal(moved, target, zone);
        // Column widths follow the panes that carried them, so a pane dropped
        // into a wide column does not drag that column's width away with it.
        sizes.setWeights((current) =>
          remapColumnWeights(current, session.terminals, next.terminals),
        );
        onSessionChanged?.(next);
      } catch (error) {
        pushToast("error", (error as Error).message);
      } finally {
        setWorking(false);
      }
    },
    [onSessionChanged, pushToast, session.terminals, sizes.setWeights],
  );

  const arrange = usePaneArrange(
    useCallback(
      (moved: string, target: string, zone: DropZone) => {
        void movePane(moved, target, zone);
      },
      [movePane],
    ),
  );

  /**
   * One ref per pane cell, serving both things that need the element.
   *
   * Rearranging measures it as a drop target, resizing moves it; they are
   * separate gestures over the same node, and an element carries one ref. The
   * callback is memoised per pane so neither of them can be handed a node that
   * React detached and re-attached in between (see `usePaneArrange`).
   */
  const { registerCell } = arrange;
  const registerPaneCell = useCallback(
    (name: string) => {
      const existing = paneRefs.current.get(name);
      if (existing) return existing;
      const asDropTarget = registerCell(name);
      const callback = (element: HTMLElement | null) => {
        if (element) paneNodes.current.set(name, element);
        else paneNodes.current.delete(name);
        asDropTarget(element);
      };
      paneRefs.current.set(name, callback);
      return callback;
    },
    [registerCell],
  );

  /*
   * A pane that appears has to be SEEN appearing.
   *
   * Panes arrive from outside this grid — "open two more Claude Code terminals"
   * spoken across the room, or the CLI — and the user is not the one who
   * pressed anything, so nothing draws their eye to the change. Worse, a
   * workspace taller than its viewport puts the new pane BELOW the fold, where
   * the honest answer to "did that work?" is a screen that looks untouched.
   * Reported 2026-07-28 as terminals that "just don't load": they had loaded,
   * off-screen and unannounced.
   *
   * Two things, both brief: the newest pane is scrolled to, and every pane that
   * just arrived wears a ring for a moment. The ring is on the CELL rather than
   * inside the terminal so it cannot be mistaken for the focus border, and it
   * expires on its own — a permanent marker would become furniture.
   */
  const [justOpened, setJustOpened] = useState<Set<string>>(() => new Set());
  // Null until the first render has been seen: on mount every pane is "new",
  // and announcing a restored workspace's eight panes would be a light show.
  const knownPanes = useRef<Set<string> | null>(null);
  useEffect(() => {
    const names = session.terminals.map((term) => term.name);
    const known = knownPanes.current;
    knownPanes.current = new Set(names);
    if (known === null) return;
    const fresh = names.filter((name) => !known.has(name));
    if (fresh.length === 0) return;
    setJustOpened(new Set(fresh));
    // The LAST one: panes are appended, so the newest is the one whose arrival
    // could have pushed the grid past its viewport. Probed rather than assumed —
    // `scrollIntoView` is absent in jsdom and in some embedded WebViews, and the
    // ring below is the half of this that matters most anyway.
    const newest = paneNodes.current.get(fresh[fresh.length - 1]);
    if (typeof newest?.scrollIntoView === "function") {
      newest.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    const timer = window.setTimeout(() => setJustOpened(new Set()), 2600);
    return () => window.clearTimeout(timer);
  }, [session.terminals]);

  /*
   * "Jump to pane" — from the header bell, or from the view after a tab switch.
   *
   * Three things, in this order: the pane is maximized, it is scrolled to, and
   * it wears the same arrival ring a freshly opened pane does. Maximizing is
   * what the user asked for — a notification about a pane is a request to READ
   * that pane, and reading a postcard-sized terminal in a grid of twelve is the
   * problem, not the answer. The ring covers the case where the pane was
   * already the maximized one and nothing visibly moved.
   *
   * Silently ignored for a pane that is no longer here: an entry outlives the
   * terminal it came from by up to one poll, and a stray maximize of "whatever
   * is called T4 now" would be worse than nothing happening.
   */
  const [jump, setJump] = useState<{ pane: string; nonce: number } | null>(null);
  // One request from two sources: the bell in this header (same workspace) and
  // the view above it (after a tab switch). Both land in one place so the
  // acting effect below has a single trigger to watch.
  useEffect(() => {
    if (jumpTo) setJump(jumpTo);
  }, [jumpTo]);

  useEffect(() => {
    const wanted = jump?.pane;
    if (!wanted) return;
    if (!session.terminals.some((term) => term.name === wanted)) {
      // The entry outlives the terminal it came from by up to one poll. Say so
      // rather than maximizing whatever is called T4 now.
      pushToast("warning", t("agentic_grid.notifications.gone").replace("{0}", wanted));
      return;
    }
    setMaximized(wanted);
    setJustOpened(new Set([wanted]));
    const node = paneNodes.current.get(wanted);
    // Probed rather than assumed — `scrollIntoView` is absent in jsdom and in
    // some embedded WebViews, and a maximized pane fills the grid anyway.
    if (typeof node?.scrollIntoView === "function") {
      node.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    const timer = window.setTimeout(() => setJustOpened(new Set()), 2600);
    return () => window.clearTimeout(timer);
    // `session.terminals` deliberately absent: this fires on a JUMP, not every
    // time a pane opens or closes underneath one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jump]);

  /** Where the bell's "jump to pane" goes — here, or via the view for a tab. */
  const jumpToNotification = useCallback(
    (entry: PaneNotification) => {
      if (entry.workspace_id && entry.workspace_id !== session.id) {
        onJumpToWorkspace?.(entry.workspace_id, entry.pane);
        return;
      }
      setJump({ pane: entry.pane, nonce: Date.now() });
    },
    [onJumpToWorkspace, session.id],
  );

  /*
   * When a pane may be picked up at all.
   *
   * Not while a pane is maximized (the others are hidden with CSS, so there is
   * nothing on screen to drop onto), not in selection mode (its overlay owns
   * every click), and not while another workspace change is still in flight —
   * two layout writes racing would leave the grid describing neither.
   */
  const canArrange =
    !selectionMode &&
    maximized === null &&
    !busy &&
    !working &&
    session.terminals.length > 1;

  const closeOne = async (name: string) => {
    setWorking(true);
    try {
      const next = await closeTerminal(name);
      setPendingClose(null);
      if (maximized === name) setMaximized(null);
      // The surviving columns keep the widths they were dragged to; the closed
      // pane's own height weight goes with it.
      sizes.setWeights((current) =>
        remapColumnWeights(current, session.terminals, next.terminals),
      );
      onSessionChanged?.(next);
      if (target === name) setTarget(next.terminals.find(takesPrompts)?.name ?? "");
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setWorking(false);
    }
  };

  const closeSelection = async (names: string[]) => {
    setWorking(true);
    try {
      const result = await closeTerminals(names);
      setPendingSelectionClose(null);
      setSelectedTerminals(new Set(result.failed.map((item) => item.name)));
      sizes.setWeights((current) =>
        remapColumnWeights(current, session.terminals, result.session.terminals),
      );
      onSessionChanged?.(result.session);
      const remaining = new Set(result.session.terminals.map((term) => term.name));
      if (maximized && !remaining.has(maximized)) setMaximized(null);
      if (target && !remaining.has(target)) {
        setTarget(result.session.terminals.find(takesPrompts)?.name ?? "");
      }
      if (result.failed.length === 0) setSelectionMode(false);
      else {
        pushToast(
          "error",
          t("agentic_grid.selection.close_failed").replace(
            "{0}",
            result.failed.map((item) => `${item.name}: ${item.detail}`).join("; "),
          ),
        );
      }
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setWorking(false);
    }
  };

  const setStatus = useCallback((name: string, status: PaneStatus, detail?: string) => {
    setStatuses((prev) => ({ ...prev, [name]: { status, detail } }));
  }, []);

  /*
   * Files dropped onto the PROMPT BAR, and what is in them.
   *
   * A drop on a pane and a drop here mean different things. The pane types a
   * path, which is right for "read this file" — the agent opens it. But a
   * screenshot is the case where that breaks down: several of the coding CLIs
   * cannot open an image at all, so the user drops a picture of a broken
   * layout, types "fix this", and the agent receives a path and a pronoun.
   *
   * So a drop here is READ first — described if it is an image, extracted if it
   * is a document — and the result rides along into the composition. The file
   * is still saved and still referenced; the description is the floor under it,
   * not a replacement for it.
   */
  const [attachments, setAttachments] = useState<DropAttachment[]>([]);
  const [analyzing, setAnalyzing] = useState(0);

  const attach = useCallback(
    async (payload: PaneDropPayload) => {
      if (isEmptyPayload(payload)) return;
      if (!target) {
        pushToast("warning", "Pick a terminal first — a dropped file belongs to one.");
        return;
      }
      setAnalyzing((n) => n + 1);
      try {
        const result = await attachToTerminal(target, {
          ...payload,
          analyze: true,
          // Held, not typed: the user is still writing the sentence that says
          // what to do with it, and it goes in with that sentence.
          deliver: false,
        });
        const found = result.analysis ?? [];
        if (found.length === 0) {
          pushToast("warning", "That drop carried nothing this prompt could use.");
          return;
        }
        setAttachments((prev) => [...prev, ...found]);
      } catch (e) {
        pushToast("error", (e as Error).message);
      } finally {
        setAnalyzing((n) => Math.max(0, n - 1));
      }
    },
    [target],
  );

  const { dragging, handlers: dragHandlers } = usePaneFileDrag(
    useCallback(
      (dt: DataTransfer) => {
        // Read BEFORE any await — a DataTransfer empties the moment this
        // handler returns (see ./paneDrop).
        void attach(extractPaneDrop(dt));
      },
      [attach],
    ),
  );

  const dropAttachment = useCallback((name: string) => {
    setAttachments((prev) => prev.filter((a) => a.name !== name));
  }, []);

  /** Type `text` into `target` and report honestly if it was not accepted. */
  const deliver = async (text: string, carry: DropAttachment[] = []) => {
    setSending(true);
    try {
      const result = await promptTerminal(target, text, { attachments: carry });
      setPreview(null);
      setPreviewSource("");
      setPrompt("");
      setAttachments([]);
      // The text is typed either way — but if the agent did not accept it, say
      // so instead of leaving the user to wonder why nothing happened.
      if (result && result.submitted === false) {
        pushToast(
          "warning",
          result.detail ||
            `${target} did not accept the prompt — the text is waiting in its input box.`,
        );
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSending(false);
    }
  };

  /**
   * Compose first, then ask.
   *
   * Jarvis turns a rough instruction into a briefed task with the relevant
   * files attached, which is a real improvement — but replacing someone's own
   * wording without showing them would be the wrong kind of helpful. So the
   * rewrite is offered, not imposed: send the brief, send what you typed, or
   * back out. If composition fails outright the original goes straight through
   * rather than blocking on a nicety.
   */
  const send = async () => {
    const text = prompt.trim();
    if (!text || !target) return;
    setSending(true);
    try {
      const composed = await composePrompt(target, text, attachments);
      if (composed.composed && composed.composed !== text) {
        setPreviewSource(text);
        setPreview(composed);
        return;
      }
      // The composed text already contains the attachments, so it carries none
      // of its own; this branch did not compose, so they still have to travel.
      await deliver(text, attachments);
    } catch (e) {
      pushToast("warning", `Could not prepare the prompt: ${(e as Error).message}`);
      await deliver(text, attachments);
    } finally {
      setSending(false);
    }
  };

  const project = session.project;
  const branchLabel = useMemo(
    () => (project.is_repo && project.branch ? `on ${project.branch}` : ""),
    [project],
  );

  return (
    <div ref={frameRef} className="relative flex h-full flex-col">
      {/* ---------------------------------------------------------- toolbar */}
      {/*
        ONE row: the workspace tabs on the left, this workspace's controls on
        the right. See the `workspaceBar` prop for why they were merged.

        `flex-nowrap` rather than the old `flex-wrap` is deliberate. Wrapping
        was what made this bar able to become two or three lines tall on a
        narrow window — silently taking the height back off the panes. Now the
        labels drop away at narrow widths (the icons and their hover text stay)
        and the row keeps its single line whatever happens.
      */}
      <div
        data-testid="agentic-toolbar"
        className="flex shrink-0 flex-nowrap items-center gap-2 border-b border-border px-2 py-1"
      >
        {workspaceBar ?? (
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <FolderGit2 className="h-4 w-4 shrink-0 text-primary" />
            <span className="truncate font-display text-sm font-semibold">
              {project.name}
            </span>
          </div>
        )}

        {/* Which branch the agents are on. It stays even in the merged row —
            the workspace tab names the folder, not the checkout, and "which
            branch am I about to let a dozen agents commit to" is not a
            question worth answering from memory. */}
        {branchLabel && (
          <span
            className="chip hidden shrink-0 text-[10px] uppercase tracking-wide xl:inline-flex"
            title={session.folder}
          >
            {branchLabel}
          </span>
        )}

        {/* Focus mode — the explicit switch into "Jarvis codes with me here". */}
        <button
          type="button"
          onClick={() => onToggleFocus(!focusMode)}
          aria-pressed={focusMode}
          data-testid="agentic-focus-toggle"
          title={
            focusMode
              ? "Focused coding mode is on — Jarvis answers inside this workspace. Click to leave."
              : "Turn on focused coding mode — Jarvis answers inside this workspace until you switch back."
          }
          className={cn(
            "flex shrink-0 items-center gap-1.5 rounded-lg border px-2 py-1 text-xs font-medium transition-colors",
            focusMode
              ? "border-primary/60 bg-primary/15 text-primary"
              : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground",
          )}
        >
          <Brain className="h-4 w-4 shrink-0" />
          <span className="hidden lg:inline">
            {focusMode ? "Coding mode ON" : "Coding mode OFF"}
          </span>
        </button>

        <div className="flex shrink-0 items-center gap-0.5 rounded-lg border border-border p-0.5">
          <button
            type="button"
            aria-label="Light terminals"
            aria-pressed={appearance === "light"}
            onClick={() => setAppearance("light")}
            className={cn(
              "flex h-6 w-6 items-center justify-center rounded-md transition-colors",
              appearance === "light" ? "bg-primary/20 text-primary" : "text-muted-foreground",
            )}
          >
            <Sun className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label="Dark terminals"
            aria-pressed={appearance === "dark"}
            onClick={() => setAppearance("dark")}
            className={cn(
              "flex h-6 w-6 items-center justify-center rounded-md transition-colors",
              appearance === "dark" ? "bg-primary/20 text-primary" : "text-muted-foreground",
            )}
          >
            <Moon className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="hidden shrink-0 items-center gap-0.5 rounded-lg border border-border p-0.5 md:flex">
          <button
            type="button"
            aria-label="Smaller text"
            onClick={() => setFontSize(Math.max(FONT_MIN, fontSize - 1))}
            className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:text-foreground"
          >
            <Minus className="h-3.5 w-3.5" />
          </button>
          <Type className="h-3.5 w-3.5 text-muted-foreground" />
          <button
            type="button"
            aria-label="Larger text"
            onClick={() => setFontSize(Math.min(FONT_MAX, fontSize + 1))}
            className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:text-foreground"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* Which terminals stopped while you were looking at another one.
            Before "Continue" rather than after it, because the two answer the
            same question at different scales — this one is "what happened",
            that one is "what should start again" — and reading them in that
            order is how somebody decides they need the second at all. */}
        <PaneNotifications onJump={jumpToNotification} onScreen={onScreen} />

        {/* Work a restart stopped: which panes came back holding a conversation
            and were never told to carry on, and the one click that tells them.
            The pane headers catch up on their own — a continued agent starts
            printing, and the recap poll above is already watching for that. */}
        <ContinueInterrupted busy={busy || working} onScreen={onScreen} />

        {/* Which subscription the next terminal spends, and the way to change
            it without leaving the workspace. */}
        <WorkspaceSettings
          accounts={accounts}
          onStateChanged={onStateChanged}
          busy={busy || working}
        />

        <button
          ref={selectionToggleRef}
          type="button"
          data-testid="terminal-selection-toggle"
          aria-pressed={selectionMode}
          onClick={selectionMode ? leaveSelectionMode : enterSelectionMode}
          title={t("agentic_grid.selection.hint")}
          className={cn(
            "flex shrink-0 items-center gap-1.5 rounded-lg border px-2 py-1 text-xs font-medium transition-colors",
            selectionMode
              ? "border-primary/60 bg-primary/15 text-primary"
              : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground",
          )}
        >
          <ListChecks className="h-4 w-4 shrink-0" />
          <span className="hidden xl:inline">
            {selectionMode
              ? t("agentic_grid.selection.finish")
              : t("agentic_grid.selection.start")}
          </span>
        </button>

        {selectionMode && (
          <div
            data-testid="terminal-selection-actions"
            className="flex shrink-0 items-center gap-1 rounded-lg border border-primary/35 bg-primary/5 p-1"
          >
            <span
              role="status"
              aria-live="polite"
              className="px-2 text-xs font-semibold text-primary"
            >
              {t("agentic_grid.selection.selected_count").replace(
                "{0}",
                String(selectedTerminals.size),
              )}
            </span>
            <button
              type="button"
              className="rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              onClick={() =>
                setSelectedTerminals(
                  new Set(session.terminals.map((terminal) => terminal.name)),
                )
              }
              disabled={session.terminals.length === 0}
            >
              {t("agentic_grid.selection.select_all")}
            </button>
            <button
              type="button"
              className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
              aria-label={t("agentic_grid.selection.clear_all")}
              title={t("agentic_grid.selection.clear_all")}
              onClick={() => setSelectedTerminals(new Set())}
              disabled={selectedTerminals.size === 0}
            >
              <X className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              data-testid="close-selected-terminals"
              className="flex items-center gap-1.5 rounded-md bg-destructive px-2.5 py-1.5 text-xs font-medium text-destructive-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
              disabled={selectedTerminals.size === 0 || busy || working}
              onClick={() => setPendingSelectionClose([...selectedTerminals])}
            >
              <Trash2 className="h-3.5 w-3.5" />
              {t("agentic_grid.selection.close_selected")}
            </button>
          </div>
        )}

        <Dialog.Root
          open={workspaceCloseRequested}
          onOpenChange={(open) => {
            if (!busy) setWorkspaceCloseRequested(open);
          }}
        >
          <Dialog.Trigger asChild>
            <button
              type="button"
              className="flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:border-destructive/50 hover:text-destructive disabled:opacity-40"
              disabled={busy}
              title="Close the workspace and stop every agent in it"
            >
              <Power className="h-4 w-4 shrink-0" />
              <span className="hidden xl:inline">Close</span>
            </button>
          </Dialog.Trigger>
          <ConfirmWorkspaceClose
            terminalCount={session.terminals.length}
            busy={busy}
            onConfirm={onClose}
          />
        </Dialog.Root>

        {/* The shell's own actions, last in the row and separated from the
            workspace's — closing a workspace and restarting the whole app are
            not neighbours you want to confuse at a glance. */}
        {appActions && (
          <div
            data-testid="agentic-app-actions"
            className="ml-1 flex shrink-0 items-center gap-2 border-l border-border pl-2"
          >
            {appActions}
          </div>
        )}
      </div>

      {/* ------------------------------------------------------------- grid */}
      {/*
        ONE container, and every pane is a direct child of it — placed by the
        fractions `paneLayout` computed rather than by where it sits in a tree
        of row and column elements.

        That is not a style choice. Every pane must stay MOUNTED for its whole
        life, because unmounting one tears down its WebSocket and kills the
        coding agent behind it. React re-parents children whenever the element
        tree changes shape, so nesting a container per column would remount
        panes on every split, close, and wrap. With one flat container the
        layout only ever changes numbers, and nothing moves in the DOM.

        It used to be a CSS grid, which gave that property for free — but a grid
        has ONE `grid-template-columns` shared by all of its rows, so two bands
        could never have different column widths, and every pane in a band was
        the same width whatever the user wanted. Fractional positioning inside
        one container keeps the mounting guarantee and drops that ceiling.

        The same reasoning covers maximizing: the other panes are HIDDEN with
        CSS, never removed, and the maximized one is told to fill the container
        instead of keeping its own rectangle.
      */}
      <div
        ref={gridRef}
        data-testid="agentic-grid"
        className={cn(
          "relative min-h-0 flex-1",
          // Scrolls only once the panes would be squeezed below a readable
          // height. With a workspace that fits, this is `overflow-hidden`
          // behaviour and nothing moves.
          maximized !== null ? "overflow-hidden" : "overflow-y-auto scrollbar-jarvis",
          // A drag across the grid would otherwise sweep a text selection over
          // every header and label it crosses.
          arrange.held !== null && "select-none",
        )}
        // Inline rather than a utility class so ONE number drives both the
        // rendered outer margin and the width the column count is computed from.
        style={{ padding: GRID_GAP_PX }}
      >
      {/*
        The surface the fractions resolve against.

        Separate from the scroller because the two have different heights on
        purpose: the scroller is the window, this is the workspace, and a
        workspace whose panes would fall below `MIN_PANE_HEIGHT_PX` grows past
        the window and scrolls rather than shrinking them further.
      */}
      <div
        ref={canvasRef}
        data-testid="agentic-grid-canvas"
        className="relative w-full"
        style={{ height: canvasHeight || undefined }}
      >
        {session.terminals.map((term, index) => {
          const box = layout.boxes[index];
          const isMaximized = maximized === term.name;
          return (
            <div
              key={term.key}
              ref={registerPaneCell(term.name)}
              data-testid={`pane-cell-${term.name}`}
              className={cn(
                "absolute min-h-0 min-w-0 rounded-lg",
                selectedTerminals.has(term.name) &&
                  "ring-2 ring-primary ring-offset-2 ring-offset-background",
                // Just arrived — see `justOpened`. Second to the selection ring
                // deliberately: selection is a thing the user is DOING, and it
                // must keep its own answer while panes come and go.
                justOpened.has(term.name) &&
                  !selectedTerminals.has(term.name) &&
                  "ring-2 ring-primary/70 ring-offset-2 ring-offset-background",
                maximized !== null && !isMaximized && "hidden",
              )}
              style={isMaximized ? MAXIMIZED_BOX : paneBoxStyle(box)}
            >
              <AgenticTerminal
                name={term.name}
                workspaceId={session.id}
                displayName={term.display_name}
                // The polled recap when one has arrived, the one the workspace
                // state carried until then — so a pane opens with a sentence in
                // its header rather than with a blank that fills in later.
                recap={recaps[term.name]?.recap ?? term.recap}
                recapDetail={
                  recaps[term.name]?.recap_detail ?? term.recap_detail
                }
                // Who wrote it and why — only the polled read knows, so a pane
                // still on its opening recap gets an empty meta and a card that
                // simply says less rather than one that guesses.
                recapMeta={{
                  source: recaps[term.name]?.source,
                  reason: recaps[term.name]?.reason,
                  writer: recaps[term.name]?.writer,
                  note: recaps[term.name]?.note,
                  generatedAt: recaps[term.name]?.generated_at,
                }}
                recapActions={recapActionsFor(term.name)}
                // Only the panes that are NOT on the default login carry a
                // badge. Labelling every pane "Default Claude Code login" would
                // be noise for the many; labelling the odd one out is the whole
                // signal for the few running two seats at once.
                accountLabel={
                  term.account && !term.account.endsWith(":default")
                    ? term.account_label
                    : null
                }
                appearance={appearance}
                fontSize={fontSize}
                focused={target === term.name}
                maximized={isMaximized}
                layoutBusy={layoutBusy}
                splitDisabled={atLimit || busy || working}
                agents={agents}
                onFocus={() => {
                  if (takesPrompts(term)) setTarget(term.name);
                }}
                onStatus={(status, detail) => setStatus(term.name, status, detail)}
                onToggleMaximize={() =>
                  setMaximized((current) => (current === term.name ? null : term.name))
                }
                onSplit={(direction, agent) => void split(term.name, direction, agent)}
                onClose={() => setPendingClose(term.name)}
                onAttachError={(message) => pushToast("error", message)}
                // Picked up by its header, put down on another pane. Undefined
                // rather than a disabled flag when rearranging is off, so the
                // header goes back to being a plain header — no grab cursor
                // promising a gesture that would do nothing.
                onArrangeStart={
                  canArrange
                    ? (event) => arrange.start(term.name, event)
                    : undefined
                }
                arranging={arrange.held === term.name}
                restartToken={restartTokens[term.name] ?? 0}
                onRestart={() => restartPane(term.name)}
              />
              {/* Where a drop on THIS pane would put the pane in hand. Every
                  other pane is outlined the moment a drag starts, so the grid
                  says "these are the places" before the cursor gets there, and
                  the one under the cursor fills in the half it would take. */}
              {arrange.held !== null && arrange.held !== term.name && (
                <div
                  data-testid={`pane-dropzone-${term.name}`}
                  data-zone={
                    arrange.hover?.target === term.name ? arrange.hover.zone : ""
                  }
                  className="pointer-events-none absolute inset-0 z-30 rounded-lg border-2 border-dashed border-primary/30"
                >
                  {arrange.hover?.target === term.name && (
                    <>
                      <div
                        className={cn(
                          "absolute rounded-md bg-primary/25 ring-2 ring-primary transition-all",
                          ZONE_BOX[arrange.hover.zone],
                        )}
                      />
                      <span className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 whitespace-nowrap rounded-md bg-primary px-2 py-1 text-[11px] font-semibold text-primary-foreground shadow-lg">
                        {t(`agentic_grid.arrange.${arrange.hover.zone}`).replace(
                          "{0}",
                          term.name,
                        )}
                      </span>
                    </>
                  )}
                </div>
              )}
              {selectionMode && (
                <button
                  type="button"
                  data-testid={`select-terminal-${term.name}`}
                  aria-pressed={selectedTerminals.has(term.name)}
                  aria-label={
                    selectedTerminals.has(term.name)
                      ? t("agentic_grid.selection.deselect_terminal").replace(
                          "{0}",
                          term.name,
                        )
                      : t("agentic_grid.selection.select_terminal").replace(
                          "{0}",
                          term.name,
                        )
                  }
                  onClick={() => toggleTerminalSelection(term.name)}
                  // Selection mode owns the right mouse button and does nothing
                  // with it. Marking a pane on right-click was too easy to
                  // trigger by accident, and letting the event through would
                  // open the app-wide Cut/Copy/Paste menu over an overlay that
                  // has no text to copy. Left-click marks; right-click is inert.
                  onContextMenu={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                  }}
                  className={cn(
                    "absolute inset-0 z-20 cursor-pointer rounded-lg transition-colors",
                    selectedTerminals.has(term.name)
                      ? "bg-primary/10"
                      : "bg-transparent hover:bg-primary/5",
                  )}
                >
                  <span
                    className={cn(
                      "absolute left-1/2 top-1/2 flex h-8 w-8 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-lg border shadow-md transition-colors",
                      selectedTerminals.has(term.name)
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border bg-card/90 text-transparent",
                    )}
                    aria-hidden="true"
                  >
                    <Check className="h-5 w-5" />
                  </span>
                </button>
              )}
            </div>
          );
        })}
        {/*
          The boundaries, and the whole reason they are their own elements.

          A seam sits BETWEEN two panes rather than on the edge of one, because
          that is the thing being moved: dragging it right widens the pane on
          the left and narrows the one on the right by exactly as much, and no
          other pane in the workspace changes at all. Three kinds — between two
          columns, between two rows of columns, and between two panes stacked in
          one column — all behave identically, which is the point.

          Hidden while a pane is maximized (there is nothing to divide), while
          panes are being selected or dragged (those gestures own the pointer),
          and, naturally, when there is only one pane.
        */}
        {maximized === null &&
          !selectionMode &&
          arrange.held === null &&
          layout.seams.map((seam) => (
            <PaneResizer
              key={seam.id}
              ref={registerSeam(seam.id)}
              testId={`pane-seam-${seam.id}`}
              orientation={seam.orientation}
              title={seam.label}
              active={sizes.dragging === seam.id}
              onPointerDown={(event) => sizes.startDrag(seam, event)}
              onDoubleClick={() => sizes.even(seam)}
              // An arrow key moves the seam the way it points, which for the
              // vertical axis is the opposite of `PaneResizer`'s own sign: its
              // default is written for a grip on a pane's own edge, where "up"
              // means "give that pane more room".
              onNudge={(delta) =>
                sizes.nudge(seam, seam.orientation === "horizontal" ? -delta : delta)
              }
              style={seamStyle(seam)}
            />
          ))}
        {session.terminals.length === 0 && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
            <span>Every terminal in this workspace is closed.</span>
            <button
              type="button"
              className="btn-primary"
              disabled={busy || working}
              onClick={() => void split(null, "right")}
            >
              <Plus className="h-4 w-4" />
              Open a terminal
            </button>
          </div>
        )}
      </div>
      </div>

      {/* Closing a terminal kills a working agent, so it always asks first. */}
      {pendingClose && (
        <ConfirmClose
          name={pendingClose}
          busy={busy || working}
          onCancel={() => setPendingClose(null)}
          onConfirm={() => void closeOne(pendingClose)}
        />
      )}

      <Dialog.Root
        open={pendingSelectionClose !== null}
        onOpenChange={(open) => {
          if (!open && !busy && !working) setPendingSelectionClose(null);
        }}
      >
        {pendingSelectionClose && (
          <ConfirmSelectionClose
            names={pendingSelectionClose}
            busy={busy || working}
            onCancel={() => setPendingSelectionClose(null)}
            onConfirm={() => void closeSelection(pendingSelectionClose)}
            restoreFocus={() => selectionToggleRef.current?.focus()}
          />
        )}
      </Dialog.Root>

      {/* ------------------------------------------------- prompt bar + seam */}
      {/*
        The seam replaces the bar's top border, so there is exactly one line
        there — and that line is the control. Hovering it lights it up; dragging
        it up opens the bar to whatever height you pull, and a double-click
        toggles between shut and the designed 176 px.
      */}
      <PaneResizer
        orientation="horizontal"
        onPointerDown={composer.startResize}
        onDoubleClick={toggleComposer}
        onNudge={composer.nudge}
        active={composer.isResizing}
        title={
          composerCollapsed
            ? "Drag up to open the prompt bar — double-click to open it fully"
            : "Drag to resize the prompt bar — double-click to close it, drag all the way down to collapse"
        }
      />

      {composerCollapsed ? (
        <div
          data-testid="agentic-composer-collapsed"
          style={{ height: COMPOSER_COLLAPSED_PX }}
          className="flex shrink-0 items-center justify-between gap-2 px-3"
        >
          <span className="truncate text-[11px] text-muted-foreground">
            {target || "No terminal"} hears whatever you say out loud.
          </span>
          <button
            type="button"
            data-testid="agentic-composer-reopen"
            onClick={() => composer.resize(COMPOSER_DEFAULT_PX)}
            className="flex shrink-0 items-center gap-1.5 rounded-md px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <ChevronUp className="h-3.5 w-3.5" />
            Write instead
          </button>
        </div>
      ) : (
        <div
          data-testid="agentic-composer"
          style={{ height: composerHeight }}
          {...dragHandlers}
          onPaste={(event) => {
            // Clipboard IMAGES only. Pasted text belongs to the textarea, and
            // intercepting it would break ordinary paste into the prompt.
            const images = extractPasteFiles(event.clipboardData).map((f) =>
              nameClipboardFile(f, target || "prompt"),
            );
            if (images.length === 0) return;
            event.preventDefault();
            void attach({ paths: [], files: images });
          }}
          className={cn(
            "relative flex shrink-0 flex-col overflow-hidden px-3 py-2 transition-colors",
            dragging && "bg-primary/5 ring-1 ring-inset ring-primary/50",
          )}
        >
          {dragging && (
            <div
              data-testid="agentic-composer-dropzone"
              className="pointer-events-none absolute inset-2 z-10 flex items-center justify-center rounded-lg border-2 border-dashed border-primary/60 bg-background/80 text-xs font-medium text-primary"
            >
              Drop a screenshot or document — {target || "the agent"} gets what is in it
            </div>
          )}
          <div className="mb-1.5 flex max-h-16 shrink-0 flex-wrap items-center gap-1 overflow-y-auto scrollbar-jarvis">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Send to
            </span>
            {/* Agent panes only. A plain terminal is a shell prompt — it is
                typed into by hand, so listing it here would offer a target that
                refuses every instruction sent to it. */}
            {session.terminals.filter(takesPrompts).map((term) => {
              const state = statuses[term.name];
              return (
                <button
                  key={term.key}
                  type="button"
                  onClick={() => setTarget(term.name)}
                  aria-pressed={target === term.name}
                  className={cn(
                    "flex items-center gap-1.5 rounded-lg border px-2 py-0.5 text-xs transition-colors",
                    target === term.name
                      ? "border-primary/60 bg-primary/10 text-primary"
                      : "border-border text-muted-foreground hover:border-primary/40",
                  )}
                >
                  <span className="font-medium">{term.name}</span>
                  <PaneStatusPill status={state?.status ?? "connecting"} detail={state?.detail} />
                </button>
              );
            })}
          </div>
          {(attachments.length > 0 || analyzing > 0) && (
            <AttachmentStrip
              attachments={attachments}
              analyzing={analyzing}
              onRemove={dropAttachment}
            />
          )}
          {preview && (
            <div className="mb-2 shrink-0 overflow-y-auto scrollbar-jarvis">
              <PromptPreview
                terminal={target}
                composed={preview.composed}
                files={preview.files}
                composedBy={preview.composed_by}
                attachments={attachments}
                // The composed text already contains the attachments; the
                // verbatim path does not, so only that one carries them.
                onSend={() => void deliver(preview.composed)}
                onSendVerbatim={() => void deliver(previewSource, attachments)}
                onCancel={() => {
                  // Give the user their own words back rather than dropping
                  // them. The attachments stay too — backing out of a rewrite
                  // is not a reason to lose the file they dropped.
                  setPrompt(previewSource);
                  setPreview(null);
                  setPreviewSource("");
                }}
              />
            </div>
          )}
          {/*
            The input takes whatever the seam left over, which is the point of
            dragging it: pull the bar up and you get a real writing surface, not a
            two-line box with empty space under it. Its own resize grip is gone —
            two ways to change one height fight each other.
          */}
          <div className="flex min-h-0 flex-1 items-end gap-2">
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
              placeholder={
                target
                  ? `Type an instruction for ${target} — Enter sends, Shift+Enter adds a line`
                  : "Pick a terminal first"
              }
              className="h-full min-h-[44px] flex-1 resize-none rounded-lg border border-border bg-background/60 px-3 py-2 text-sm outline-none focus:border-primary/50"
            />
            <button
              type="button"
              className="btn-primary h-[52px] shrink-0"
              disabled={sending || !prompt.trim() || !target}
              onClick={() => void send()}
            >
              <ArrowUp className="h-4 w-4" />
              {sending ? "Preparing…" : "Send"}
            </button>
          </div>
          {composerHeight >= COMPOSER_HINT_AT_PX && (
            <p className="mt-2 shrink-0 text-[11px] text-muted-foreground">
              Anything you can type here, you can also say out loud — for example
              “what is {session.terminals[0]?.name ?? "Mika"} doing?” or “tell{" "}
              {session.terminals[0]?.name ?? "Mika"} to run the tests”.
            </p>
          )}
        </div>
      )}

      {/* The pane in hand, following the cursor.
          A label rather than a copy of the terminal: an xterm canvas cannot be
          cloned without a second WebSocket, and what the user needs to see mid-
          drag is which pane they are carrying, not its output. */}
      {arrange.held !== null && arrange.point !== null && (
        <div
          data-testid="agentic-arrange-ghost"
          className="pointer-events-none fixed z-50 flex items-center gap-1.5 rounded-lg border border-primary/60 bg-card px-2.5 py-1.5 text-xs font-semibold shadow-xl"
          style={{ left: arrange.point.x + 14, top: arrange.point.y + 14 }}
        >
          <GripVertical className="h-3.5 w-3.5 text-primary" />
          {arrange.held}
          <span className="font-normal text-muted-foreground">
            {arrange.hover === null
              ? t("agentic_grid.arrange.carrying")
              : t(`agentic_grid.arrange.${arrange.hover.zone}`).replace(
                  "{0}",
                  arrange.hover.target,
                )}
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * The files waiting to go in with the next prompt.
 *
 * Each chip says what was actually LEARNED from the file, not just that one was
 * attached. That distinction is the whole feature: "screenshot.png" tells the
 * user nothing about whether the agent will be able to see it, while "described"
 * and "not described" are the two outcomes they need to be able to tell apart
 * before they press Send — and the second one happens for real, on any install
 * whose providers cannot see images.
 */
function AttachmentStrip({
  attachments,
  analyzing,
  onRemove,
}: {
  attachments: DropAttachment[];
  analyzing: number;
  onRemove: (name: string) => void;
}) {
  return (
    <div
      data-testid="agentic-attachments"
      className="mb-2 flex max-h-20 shrink-0 flex-wrap items-center gap-1.5 overflow-y-auto scrollbar-jarvis"
    >
      {attachments.map((item) => {
        const read = item.described_by !== "none" && item.detail.length > 0;
        return (
          <span
            key={item.name}
            data-testid={`agentic-attachment-${item.name}`}
            title={
              read
                ? `${item.detail.slice(0, 400)}${item.detail.length > 400 ? "…" : ""}`
                : item.note || "Attached as a file."
            }
            className={cn(
              "flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px]",
              read
                ? "border-primary/40 bg-primary/10 text-foreground"
                : "border-border text-muted-foreground",
            )}
          >
            {item.kind === "image" ? (
              <ImageIcon className="h-3 w-3 shrink-0" />
            ) : (
              <FileText className="h-3 w-3 shrink-0" />
            )}
            <span className="max-w-[12rem] truncate font-mono">{item.name}</span>
            <span className="shrink-0 text-[10px] text-muted-foreground">
              {read
                ? item.described_by === "vision"
                  ? "described"
                  : "text read"
                : "not described"}
            </span>
            <button
              type="button"
              aria-label={`Remove ${item.name}`}
              data-testid={`agentic-attachment-remove-${item.name}`}
              onClick={() => onRemove(item.name)}
              className="shrink-0 rounded text-muted-foreground hover:text-destructive"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        );
      })}
      {analyzing > 0 && (
        <span
          data-testid="agentic-attachment-working"
          className="flex items-center gap-1.5 rounded-md border border-dashed border-border px-2 py-1 text-[11px] text-muted-foreground"
        >
          <Loader2 className="h-3 w-3 animate-spin" />
          Reading {analyzing === 1 ? "the dropped file" : `${analyzing} dropped files`}…
        </span>
      )}
    </div>
  );
}

function ConfirmSelectionClose({
  names,
  busy,
  onCancel,
  onConfirm,
  restoreFocus,
}: {
  names: string[];
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  restoreFocus: () => void;
}) {
  const t = useT();
  return (
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-50 bg-background/70 backdrop-blur-sm" />
      <Dialog.Content
        data-testid="confirm-close-selection"
        className="fixed left-1/2 top-1/2 z-50 w-[min(26rem,calc(100vw-3rem))] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border bg-card p-5 shadow-xl"
        onEscapeKeyDown={(event) => {
          if (busy) event.preventDefault();
        }}
        onPointerDownOutside={(event) => {
          if (busy) event.preventDefault();
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          // Radix removes the modal's inert/aria-hidden state after this event.
          // Focusing synchronously would therefore be ignored by the browser.
          window.setTimeout(restoreFocus, 0);
        }}
      >
        <Dialog.Title className="font-display text-base font-semibold">
          {t("agentic_grid.selection.confirm_title")}
        </Dialog.Title>
        <Dialog.Description className="mt-2 text-sm text-muted-foreground">
          {t("agentic_grid.selection.confirm_description")}
        </Dialog.Description>
        <p className="mt-3 text-xs font-semibold text-foreground">
          {t("agentic_grid.selection.will_close").replace("{0}", String(names.length))}
        </p>
        <div className="mt-2 flex max-h-28 flex-wrap gap-1.5 overflow-y-auto scrollbar-jarvis">
          {names.map((name) => (
            <span key={name} className="chip text-xs">
              {name}
            </span>
          ))}
        </div>
        <div className="mt-5 flex items-center justify-end gap-2">
          <Dialog.Close asChild>
            <button
              type="button"
              className="btn-ghost"
              autoFocus
              disabled={busy}
              onClick={onCancel}
            >
              {t("agentic_grid.selection.cancel")}
            </button>
          </Dialog.Close>
          <button
            type="button"
            data-testid="confirm-close-selection-confirm"
            className="rounded-lg bg-destructive px-3 py-2 text-sm font-medium text-destructive-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
            disabled={busy}
            onClick={onConfirm}
          >
            {t("agentic_grid.selection.confirm")}
          </button>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  );
}

/**
 * Confirmation before the whole workspace is closed.
 *
 * This is intentionally separate from the per-terminal confirmation: the
 * toolbar action stops every coding agent at once, so a stray click must never
 * reach the session shutdown endpoint. The safe action receives initial focus.
 */
function ConfirmWorkspaceClose({
  terminalCount,
  busy,
  onConfirm,
}: {
  terminalCount: number;
  busy: boolean;
  onConfirm: () => void;
}) {
  const agentLabel = terminalCount === 1 ? "coding agent" : "coding agents";

  return (
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-50 bg-background/70 backdrop-blur-sm" />
      <Dialog.Content
        data-testid="confirm-close-workspace"
        className="fixed left-1/2 top-1/2 z-50 w-[min(24rem,calc(100vw-3rem))] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border bg-card p-5 shadow-xl"
        onEscapeKeyDown={(event) => {
          if (busy) event.preventDefault();
        }}
        onPointerDownOutside={(event) => {
          if (busy) event.preventDefault();
        }}
      >
        <Dialog.Title className="font-display text-base font-semibold">
          Close this workspace?
        </Dialog.Title>
        <Dialog.Description className="mt-2 text-sm text-muted-foreground">
          This stops all {terminalCount} {agentLabel} and closes every terminal
          session. Anything already written to disk stays.
        </Dialog.Description>
        <div className="mt-5 flex items-center justify-end gap-2">
          <Dialog.Close asChild>
            <button
              type="button"
              className="btn-ghost"
              autoFocus
              disabled={busy}
            >
              Keep workspace open
            </button>
          </Dialog.Close>
          <button
            type="button"
            data-testid="confirm-close-workspace-confirm"
            className="rounded-lg bg-destructive px-3 py-2 text-sm font-medium text-destructive-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
            disabled={busy}
            onClick={onConfirm}
          >
            Close workspace
          </button>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  );
}

/**
 * Confirmation before a pane is closed.
 *
 * Closing a terminal terminates a coding agent that may be mid-task, and there
 * is no undo — so it always asks, and the dialog says what is actually lost.
 * Escape cancels and the destructive button is not the default focus.
 */
function ConfirmClose({
  name,
  busy,
  onCancel,
  onConfirm,
}: {
  name: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Close ${name}`}
      data-testid="confirm-close-terminal"
      className="absolute inset-0 z-30 flex items-center justify-center bg-background/70 p-6 backdrop-blur-sm"
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
    >
      <div className="w-full max-w-sm rounded-xl border border-border bg-card p-5 shadow-xl">
        <h3 className="font-display text-base font-semibold">Close {name}?</h3>
        <p className="mt-2 text-sm text-muted-foreground">
          The coding agent running in this terminal is stopped and its session is
          gone. Anything it already wrote to disk stays.
        </p>
        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            className="btn-ghost"
            autoFocus
            disabled={busy}
            onClick={onCancel}
          >
            Keep it open
          </button>
          <button
            type="button"
            data-testid="confirm-close-terminal-confirm"
            className="rounded-lg bg-destructive px-3 py-2 text-sm font-medium text-destructive-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
            disabled={busy}
            onClick={onConfirm}
          >
            Close {name}
          </button>
        </div>
      </div>
    </div>
  );
}
