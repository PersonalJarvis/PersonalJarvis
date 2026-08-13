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
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  AlignHorizontalDistributeCenter,
  AudioLines,
  Brain,
  Check,
  ChevronUp,
  FileText,
  Files,
  FolderGit2,
  FolderPlus,
  GripVertical,
  Image as ImageIcon,
  LayoutGrid,
  ListChecks,
  Loader2,
  MessagesSquare,
  Minus,
  MoveHorizontal,
  Moon,
  MoreHorizontal,
  Plus,
  Power,
  Search,
  SlidersHorizontal,
  SquarePen,
  Sun,
  Trash2,
  Type,
  X,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useThemeValue } from "@/hooks/useTheme";
import { useDocumentVisible } from "@/hooks/useDocumentVisible";
import { useResizablePane } from "@/hooks/useResizablePane";
import { PaneResizer } from "@/components/layout/PaneResizer";
import { QuickTooltip } from "@/components/ui/tooltip";
import { useEventStore, type VoiceState } from "@/store/events";
import { AgenticTerminal, type PaneStatus, type SplitDirection } from "./AgenticTerminal";
import { AgentMark } from "./AgentMark";
import { PaneActivityPill } from "./PaneActivityPill";
import { AgentPickerMenu, offersAgentChoice, type SplitAgentChoice } from "./AgentPicker";
import type { TerminalAppearance } from "./terminalThemes";
import { installZoomKeyBridge, type ZoomIntent } from "./terminalZoom";
import {
  FONT_DEFAULT,
  FONT_KEY,
  FONT_MAX,
  FONT_MIN,
  storedFontSize,
} from "./paneFont";
import {
  isEvenTree,
  treeLayout,
  type LayoutNode,
  type PaneBox,
  type PaneSeam,
} from "./treeLayout";
import { useTreeSizes } from "./useTreeSizes";
import {
  describeLayoutViolations,
  findLayoutViolations,
  hasLayoutViolations,
  type MeasuredPane,
} from "./paneLayoutGuard";
import { ContinueInterrupted } from "./ContinueInterrupted";
import { PaneNotifications } from "./PaneNotifications";
import { isVoiceActive } from "./useVoiceCall";
import { PromptEditor } from "./PromptEditor";
import { WorkspaceSettings } from "./WorkspaceSettings";
import { WorkspaceExplorer } from "./WorkspaceExplorer";
import { WorkspaceFileViewer } from "./WorkspaceFileViewer";
import { usePaneFileDrag } from "./paneFileDrag";
import {
  chatTerminalIdentity,
  initialChatOrder,
  orderChatTerminals,
  reconcileChatOrder,
  sameRows,
  swapChatOrder,
} from "./chatState";
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
  moveTerminal,
  clearTerminalRecap,
  fetchTerminalActivity,
  fetchTerminalRecaps,
  fetchTerminalUiPreferences,
  refreshTerminalRecap,
  renameTerminal,
  saveTerminalFontSize,
  syncAgenticIdeSurface,
  setTerminalRecap,
  promptTerminal,
  type DropAttachment,
  type IdeAccountState,
  type IdeState,
  type PaneNotification,
  type SessionState,
  type TerminalActivityRow,
  type TerminalRecap,
  type TerminalState,
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
  /** Keep the voice orb aimed at the same pane as the written prompt bar. */
  onPromptTargetChange?: (name: string) => void;
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
   * Open another workspace, choosing its folder — the rail's "Add project".
   *
   * Absent = the row is not offered. Owned by the view above this one, because
   * opening a workspace is a thing that happens BESIDE this one rather than
   * inside it.
   */
  onAddProject?: () => void;
  /**
   * Start a workspace with no project folder chosen — the rail's "New session".
   *
   * A question, a quick script, "what is in this file" are all worth asking
   * without first naming a repository to ask them in. The agent still runs
   * somewhere (the home folder); what the user is spared is the decision.
   */
  onNewSession?: () => void;
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
  /**
   * Is the floating voice bubble on screen, and the toggle that summons it.
   *
   * The bubble itself is NOT rendered here: the conversation belongs to the
   * app, not to a workspace, and this grid is keyed by workspace — mounting
   * the bubble inside it would reset the orb mid-sentence on every tab
   * switch. The view above owns the bubble; the toolbar only carries the
   * button, because the toolbar is where every other control of this screen
   * lives. Left out (tests, embeddings), the toolbar simply has no voice
   * button.
   */
  voiceOpen?: boolean;
  onToggleVoice?: () => void;
}

/*
 * Terminal text size, in pixels. Moved to ./paneFont, which the workspace
 * wizard also reads: it has to say how many terminals this window fits before
 * any pane exists, and that answer depends on the text size.
 */

/** The size one zoom step away from `from`, kept inside the bounds. */
export function zoomedFontSize(from: number, intent: ZoomIntent): number {
  if (intent === "reset") return FONT_DEFAULT;
  const next = from + (intent === "in" ? 1 : -1);
  return Math.min(FONT_MAX, Math.max(FONT_MIN, next));
}

/*
 * The one button style of the workspace toolbar.
 *
 * The row above a wall of terminals used to be a collection of bordered
 * controls in three different shapes — labelled toggles, segmented pairs,
 * outlined groups — and every one of them competed with the panes for the eye.
 * The redesign extends the launcher's rule (see ./controls.tsx) to the running
 * workspace: controls recede to quiet glyphs, colour is reserved for state
 * that is ON, and the terminals are the only thing on screen with weight.
 */
/*
 * `rounded-control` (6 px), not the theme's `md` (10 px). At 28 px square a
 * 10 px radius is a third of the edge, which reads as a lozenge rather than a
 * button and put a row of soft blobs across the top of a view whose subject is
 * a wall of right-angled terminals. Six is the section's control radius — see
 * ./controls.tsx for the closed set and why the theme's own steps did not fit.
 */
const TOOLBAR_BTN =
  "flex h-7 w-7 shrink-0 items-center justify-center rounded-control text-muted-foreground " +
  "transition-colors hover:bg-secondary hover:text-foreground " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 " +
  "disabled:cursor-not-allowed disabled:opacity-40";

/** The same button, switched ON — the only yellow the toolbar spends. */
const TOOLBAR_BTN_ON = "bg-primary/15 text-primary hover:bg-primary/20 hover:text-primary";

/**
 * The reading-mode switch, one entry per view.
 *
 * A table rather than two hand-written buttons because the set is a contract
 * (see `WorkspaceView`): a third mode should be one row here, and a mode with
 * no row is a mode the user cannot reach — which is the failure this shape
 * makes obvious instead of silent.
 */
const VIEW_BUTTONS: ReadonlyArray<{
  view: WorkspaceView;
  testId: string;
  icon: LucideIcon;
  title: string;
}> = [
  {
    view: "grid",
    testId: "agentic-view-mode-grid",
    icon: LayoutGrid,
    title: "Terminal grid — every pane on screen at once.",
  },
  {
    view: "chat",
    testId: "agentic-view-mode-toggle",
    icon: MessagesSquare,
    title: "Chat view — read one agent at a time, like a conversation.",
  },
];

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

/**
 * How often the status badges re-read whether each agent is still working.
 *
 * Much faster than the recap poll, and that gap is the point: the recap is a
 * SENTENCE and costs a transcript walk per pane, so five seconds is right for
 * it — but the badge is one word, `/activity` answers it from a stamp without
 * touching the transcript, and a badge that flips five-plus seconds after the
 * pane visibly started reads as broken (maintainer report 2026-08-07). Shares
 * the recap poll's visibility gate: hidden badges are not glanced at either.
 */
const ACTIVITY_POLL_MS = 1500;

/**
 * How long a just-sent prompt is allowed to claim "working" on its own.
 *
 * Only until the backend can answer for itself: the submit grace over there
 * (`SUBMIT_GRACE_S` in `jarvis/agentic_ide/activity.py`) reports a freshly
 * submitted pane as working on the very next poll, so all this bridge covers
 * is the send-to-next-poll gap this window alone can see. Two poll beats
 * rather than one, so a poll already in flight when Send lands cannot carry
 * the answer away. Deliberately NOT longer: past this window the backend's
 * word is the truer one, including its 10-second verdict on a prompt the
 * agent swallowed — a second, longer client policy on top of that would just
 * disagree at the edges.
 */
const SENT_BRIDGE_MS = 2 * ACTIVITY_POLL_MS;

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
/**
 * Room the toolbar and a still-usable grid keep for themselves.
 *
 * Without it a tall prompt bar plus a short window squeezes the grid to zero,
 * every pane measures 0×0, and the panes' fit logic refuses to resize a
 * terminal to no columns — the workspace would look frozen rather than small.
 */
const GRID_RESERVED_PX = 200;

/** The file explorer starts compact, but its left seam can resize it. */
const EXPLORER_WIDTH_KEY = "jarvis.agenticIde.explorerWidth.v1";
const EXPLORER_DEFAULT_PX = 280;
const EXPLORER_MIN_PX = 220;
const EXPLORER_MAX_PX = 640;
/** Terminal canvas kept visible while the right-hand explorer is open. */
const EXPLORER_GRID_RESERVED_PX = 320;

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
 *
 * The text size is the exception and is kept by the BACKEND as well (see
 * `fetchTerminalUiPreferences`). The desktop window is an embedded WebView that
 * starts every run with empty browser storage, so a size kept only here is
 * forgotten on each restart — which reads as the control having stopped
 * working. Its localStorage entry stays as the first-paint cache so the panes
 * open at the remembered size instead of visibly resizing a moment later.
 */
const APPEARANCE_KEY = "jarvis.agenticIde.terminalAppearance";

/**
 * The two ways of looking at one workspace.
 *
 * `grid` is the wall of terminals — every pane visible at once, sized by the
 * dragged seams. `chat` is the same workspace read like a conversation: a rail
 * of agents on the left, ONE terminal on a calm centred stage, and the prompt
 * bar underneath as the composer.
 *
 * Talking to the assistant is not a mode: the floating voice orb is summoned
 * from the toolbar and drawn over whichever of these two is on screen, so
 * reaching for the microphone never costs the user the layout they chose.
 *
 * No mode owns the panes — switching is a pure restyle of the same mounted
 * elements, because unmounting a pane kills the coding agent behind it (see
 * the grid container's comment below). That rule is what makes a further mode
 * cheap to add and is the one thing it must not break.
 *
 * Layer 4 of the enum contract; `jarvis/agentic_ide/workspace_view.py` is the
 * source of truth and the backend asserts the two agree at import.
 */
export type WorkspaceView = "grid" | "chat";

/** Every value, in the order the toolbar and the wizard offer them. */
export const WORKSPACE_VIEWS: readonly WorkspaceView[] = ["grid", "chat"];

function isWorkspaceView(raw: string): raw is WorkspaceView {
  return (WORKSPACE_VIEWS as readonly string[]).includes(raw);
}

const VIEW_KEY = "jarvis.agenticIde.workspaceView";
const CHAT_ORDER_KEY_PREFIX = "jarvis.agenticIde.chatOrder.v1";

export function storedViewMode(): WorkspaceView | null {
  return readStored(VIEW_KEY, (raw) => (isWorkspaceView(raw) ? raw : null));
}

/**
 * Record which way the workspace should be read, ahead of the grid mounting.
 *
 * Exported for the workspace wizard: its last step asks grid-or-chat before
 * anything opens, and the grid then simply reads the answer on mount — the
 * same stored preference the toolbar toggle below keeps, so the wizard's
 * choice and a later toggle can never disagree about where the answer lives.
 */
export function rememberViewMode(next: WorkspaceView): void {
  writeStored(VIEW_KEY, next);
}

function storedChatOrder(workspaceId: string): readonly string[] | null {
  return readStored(`${CHAT_ORDER_KEY_PREFIX}.${workspaceId}`, (raw) => {
    try {
      const parsed: unknown = JSON.parse(raw);
      return Array.isArray(parsed) && parsed.every((item) => typeof item === "string")
        ? parsed
        : null;
    } catch {
      return null;
    }
  });
}

function rememberChatOrder(workspaceId: string, identities: readonly string[]): void {
  writeStored(`${CHAT_ORDER_KEY_PREFIX}.${workspaceId}`, JSON.stringify(identities));
}

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

/**
 * The same map with one pane's entry filed under its new call-sign.
 *
 * This grid keys a pane's UI state (its status, its recap, its restart token)
 * by call-sign, which is the right key right up until a pane is renamed. An
 * untouched map would leave that state stranded under a name nothing looks up
 * any more, and the pane would come back blank for no reason a user could see.
 *
 * Returns the map unchanged when there is nothing filed under `from`, so a
 * rename cannot churn state that was never there.
 */
function rekey<T>(map: Record<string, T>, from: string, to: string): Record<string, T> {
  if (!(from in map)) return map;
  const { [from]: value, ...rest } = map;
  return { ...rest, [to]: value };
}

function storedAppearance(): TerminalAppearance | null {
  return readStored(APPEARANCE_KEY, (raw) => (raw === "light" || raw === "dark" ? raw : null));
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
  onAddProject,
  onNewSession,
  onJumpToWorkspace,
  jumpTo = null,
  onScreen = true,
  onPromptTargetChange,
  voiceOpen = false,
  onToggleVoice,
}: AgenticGridProps) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const theme = useThemeValue();
  // No stored choice → follow the app. A stored one wins and keeps winning.
  const [appearance, setAppearanceState] = useState<TerminalAppearance>(
    () => storedAppearance() ?? theme,
  );
  const [fontSize, setFontSizeState] = useState(() => storedFontSize() ?? FONT_DEFAULT);

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
    // The backend is what makes the choice survive a restart; the line above is
    // only this window's cache. A failed write is reported rather than
    // swallowed — the panes still resize, they just would not remember it.
    void saveTerminalFontSize(next).catch((err) => {
      console.warn("Agentic IDE: terminal text size not remembered:", err);
    });
  }, []);

  /*
   * Ctrl/Cmd + `+` / `-` / `0`, the chord every other application binds to
   * "make this text bigger" — see ./terminalZoom for the chord table and for
   * why this app has to claim the keystroke rather than let the WebView zoom
   * the entire window.
   *
   * The listener is installed ONCE and reads the current size out of a ref: it
   * captures keystrokes for every pane in the workspace, and re-registering it
   * on each step would drop a chord held down to zoom repeatedly.
   */
  const zoomStateRef = useRef({ fontSize, onScreen });
  zoomStateRef.current = { fontSize, onScreen };
  useEffect(
    () =>
      installZoomKeyBridge(window, {
        isMac: /mac|iphone|ipad/i.test(navigator.userAgent),
        // Hidden rather than unmounted when another section is open, so the
        // grid has to be asked whether anyone is looking at it.
        enabled: () => zoomStateRef.current.onScreen,
        apply: (intent) => {
          const current = zoomStateRef.current.fontSize;
          const next = zoomedFontSize(current, intent);
          if (next !== current) setFontSize(next);
        },
      }),
    [setFontSize],
  );

  // Pick the remembered size back up. Runs once per mounted workspace: the size
  // is one person's reading preference, not a property of a project, so it is
  // the same in every workspace and never re-read on a switch.
  useEffect(() => {
    let alive = true;
    fetchTerminalUiPreferences()
      .then((prefs) => {
        if (!alive) return;
        if (prefs.stored) {
          setFontSizeState(prefs.terminal_font_size);
          writeStored(FONT_KEY, String(prefs.terminal_font_size));
          return;
        }
        // Nothing stored yet, but this window still holds a size chosen before
        // the backend kept them. Hand that choice over instead of letting the
        // default silently replace it — otherwise upgrading resets it once.
        const local = storedFontSize();
        if (local === null) return;
        void saveTerminalFontSize(local).catch((err) => {
          console.warn("Agentic IDE: terminal text size not remembered:", err);
        });
      })
      .catch((err) => {
        // Older backend or a request that failed: keep the cached size and say
        // why it may not stick, rather than looking like it worked.
        console.warn("Agentic IDE: could not read the stored terminal text size:", err);
      });
    return () => {
      alive = false;
    };
  }, []);
  const [target, setTarget] = useState(session.terminals.find(takesPrompts)?.name ?? "");
  useEffect(() => {
    onPromptTargetChange?.(target);
  }, [onPromptTargetChange, target]);
  const [statuses, setStatuses] = useState<Record<string, { status: PaneStatus; detail?: string }>>(
    {},
  );
  // What each pane is doing, by call-sign, as the header shows it. Kept beside
  // the session rather than inside it because it changes on a completely
  // different clock: the layout changes when a pane is opened or closed, a
  // recap whenever an agent prints a line.
  const [recapCache, setRecapCache] = useState<{
    workspaceId: string;
    rows: Record<string, TerminalRecap>;
  }>(() => ({ workspaceId: session.id, rows: {} }));
  // Never show T1's status from the workspace that was on screen one render
  // ago. Call-signs repeat between workspaces, so a cache without its owner can
  // claim a fresh pane is working, done, or describing somebody else's task.
  const recaps: Record<string, TerminalRecap> =
    recapCache.workspaceId === session.id ? recapCache.rows : {};
  // The editor owns ordinary keystrokes so typing does not re-render every
  // xterm pane in this very large component. This seed changes only when the
  // parent intentionally replaces the draft (successful send).
  const [promptSeed, setPromptSeed] = useState({ value: "", revision: 0 });
  const replacePrompt = useCallback((value: string) => {
    setPromptSeed((current) => ({ value, revision: current.revision + 1 }));
  }, []);
  const [sending, setSending] = useState(false);
  // The live line about the brief being written for a pane of THIS workspace.
  // Composition is 10-30 s of real model work; without this line the bar
  // showed a silent spinner for all of it, and a working composer and a
  // wedged one looked identical. Fed by the backend's own beats over the app
  // socket, so it also narrates a compose another window of this workspace
  // started; cleared by the delivery event, a failed send, or a stale timer.
  const [composeBeat, setComposeBeat] = useState<{
    terminal: string;
    stage: string;
    message: string;
  } | null>(null);
  useEffect(() => {
    let staleTimer: number | undefined;
    const onBeat = (event: Event) => {
      const detail = (event as CustomEvent).detail as {
        session_id?: string;
        terminal?: string;
        stage?: string;
        message?: string;
      };
      if (detail.session_id && detail.session_id !== session.id) return;
      if (!detail.terminal || !detail.message) return;
      setComposeBeat({
        terminal: detail.terminal,
        stage: detail.stage ?? "",
        message: detail.message,
      });
      window.clearTimeout(staleTimer);
      // A beat never followed by a delivery (a killed backend, a dropped
      // socket) must not leave a "writing…" line standing forever.
      staleTimer = window.setTimeout(() => setComposeBeat(null), 120_000);
    };
    const onDelivered = (event: Event) => {
      const detail = (event as CustomEvent).detail as { terminal?: string };
      setComposeBeat((current) =>
        current && (!detail.terminal || detail.terminal === current.terminal)
          ? null
          : current,
      );
    };
    window.addEventListener("jarvis:agentic-ide-compose", onBeat);
    window.addEventListener("jarvis:agentic-ide-prompt", onDelivered);
    return () => {
      window.clearTimeout(staleTimer);
      window.removeEventListener("jarvis:agentic-ide-compose", onBeat);
      window.removeEventListener("jarvis:agentic-ide-prompt", onDelivered);
    };
  }, [session.id]);

  // Bumping a pane's token reconnects just that pane, which respawns its agent.
  // Keyed by call-sign so closing or splitting never disturbs the others.
  const [restartTokens, setRestartTokens] = useState<Record<string, number>>({});
  const restartPane = useCallback((name: string) => {
    setRestartTokens((prev) => ({ ...prev, [name]: (prev[name] ?? 0) + 1 }));
  }, []);

  const [maximized, setMaximized] = useState<string | null>(null);

  /**
   * Which "open a terminal" button has its CLI picker open, if any.
   *
   * The panes carry their own (see `AgenticTerminal`); these are the two places
   * that open a terminal without a pane to hang it off — the chat view's rail,
   * and the message an emptied workspace shows. Both used to start whatever CLI
   * the backend listed first, so a chat-view workspace could only ever grow more
   * panes of that one agent while the grid's split buttons were asking properly
   * (maintainer report 2026-07-31).
   */
  const [picking, setPicking] = useState<"rail" | "empty" | null>(null);

  /*
   * Grid or chat — remembered per browser profile, like the appearance above:
   * which way someone reads their agents is a display preference of this
   * screen, not workspace state worth a round-trip.
   */
  const [viewMode, setViewModeState] = useState<WorkspaceView>(() => storedViewMode() ?? "grid");
  const [explorerOpen, setExplorerOpen] = useState(false);
  const [openedWorkspaceFile, setOpenedWorkspaceFile] = useState<string | null>(null);
  const openedWorkspaceFileTrigger = useRef<HTMLElement | null>(null);
  const setViewMode = useCallback((next: WorkspaceView) => {
    setViewModeState(next);
    rememberViewMode(next);
    // Chat shows one pane at most; a leftover maximize from the grid would
    // silently pin the stage to a pane the chat rail does not highlight.
    if (next !== "grid") setMaximized(null);
    // An open CLI picker belongs to the button that opened it, and that button
    // just left the screen — it must not be waiting there on the way back.
    setPicking(null);
  }, []);
  const chatView = viewMode === "chat";

  useEffect(() => {
    setOpenedWorkspaceFile(null);
    openedWorkspaceFileTrigger.current = null;
  }, [session.id]);

  const closeWorkspaceFile = useCallback(() => {
    setOpenedWorkspaceFile(null);
    window.requestAnimationFrame(() => openedWorkspaceFileTrigger.current?.focus());
  }, []);

  const openWorkspaceFile = useCallback((path: string, trigger?: HTMLElement) => {
    if (trigger) openedWorkspaceFileTrigger.current = trigger;
    setOpenedWorkspaceFile(path);
  }, []);

  /*
   * Which pane the chat stage shows. Kept apart from `target` (the pane the
   * prompt bar types into) because the two answer different questions: a plain
   * shell pane can be WATCHED on the stage but never prompted, and looking at
   * it must not silently redirect the next instruction into a pane that
   * refuses it. The effective selection is derived rather than repaired in
   * effects, so a pane closed by another client simply falls back.
   */
  const [chatPane, setChatPane] = useState<string | null>(null);
  // Null until the first render has been seen: on mount every pane is "new",
  // and announcing a restored workspace's eight panes would be a light show.
  // Kept beside the stage selection because a newly arrived pane must be
  // selected during render, before its terminal's connection effect measures
  // the otherwise-hidden grid cell.
  const knownPanes = useRef<Set<string> | null>(null);
  const [chatOrder, setChatOrder] = useState<{
    workspaceId: string;
    keys: readonly string[];
  }>(() => ({
    workspaceId: session.id,
    keys: storedChatOrder(session.id) ?? initialChatOrder(session.terminals),
  }));
  const chatRailRef = useRef<HTMLDivElement | null>(null);
  const [pendingClose, setPendingClose] = useState<string | null>(null);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedTerminals, setSelectedTerminals] = useState<Set<string>>(() => new Set());
  const [pendingSelectionClose, setPendingSelectionClose] = useState<string[] | null>(null);
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
    let pulling = false;
    let warned = false;
    const pull = async () => {
      // A slow read must not stack another walk of every terminal on top of
      // itself. One result is enough; the next interval catches up.
      if (pulling) return;
      pulling = true;
      try {
        const answer = await fetchTerminalRecaps(session.id);
        if (cancelled) return;
        const next = Object.fromEntries(answer.terminals.map((term) => [term.name, term]));
        setRecapCache((current) => {
          const currentRows = current.workspaceId === session.id ? current.rows : {};
          return sameRows(currentRows, next) ? current : { workspaceId: session.id, rows: next };
        });
        warned = false;
      } catch (error) {
        // Keep the last recap, but do not turn a dead status feed into a silent
        // failure. Log once until a successful read resets the warning.
        if (!warned) {
          console.warn("Agentic IDE: could not refresh terminal status:", error);
          warned = true;
        }
      } finally {
        pulling = false;
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
   * The badges' own fast feed — whether each pane's agent is still working.
   *
   * Kept apart from the recap poll above for the same reason that one is kept
   * apart from the workspace state: different clocks. `/activity` is one
   * stamped word per pane, so it can run every second and a half and the badge
   * flips within a beat of the pane — where the recap poll left it wrong for
   * up to five seconds, which on a list of a dozen agents reads as a broken
   * indicator rather than a slow one. Same workspace guard as the recap cache:
   * call-signs repeat between workspaces.
   */
  const [activityCache, setActivityCache] = useState<{
    workspaceId: string;
    rows: Record<string, TerminalActivityRow>;
  }>(() => ({ workspaceId: session.id, rows: {} }));
  const liveActivity: Record<string, TerminalActivityRow> =
    activityCache.workspaceId === session.id ? activityCache.rows : {};

  /*
   * When each pane was last handed a prompt from THIS window, epoch ms.
   *
   * The optimistic half of the badge: the moment Send succeeds the pane is
   * shown as working, because the user just watched themselves put work in
   * front of it. The backend knows about the submit too (its own grace covers
   * the seconds before the first paint) — what it cannot cover is the beat
   * until the next poll fetches that answer, and that beat is all this
   * carries. An entry is dropped the moment the backend confirms, and expires
   * after `SENT_BRIDGE_MS` either way.
   */
  const [sentAt, setSentAt] = useState<Record<string, number>>({});

  useEffect(() => {
    if (!pollRecaps) return;
    let cancelled = false;
    let pulling = false;
    const pull = async () => {
      if (pulling) return;
      pulling = true;
      try {
        const answer = await fetchTerminalActivity(session.id);
        if (cancelled) return;
        const next = Object.fromEntries(answer.terminals.map((row) => [row.name, row]));
        // Same keep-identity guard as the recap poll above: most ticks change
        // nothing, and a fresh-but-equal object every 1.5 seconds would
        // re-render this very large component forty times a minute for it.
        setActivityCache((current) => {
          const currentRows = current.workspaceId === session.id ? current.rows : {};
          return sameRows(currentRows, next)
            ? current
            : { workspaceId: session.id, rows: next };
        });
        // The bridge ends here: dropped once the backend confirms the pane
        // working, and expired past its window — inside the poll, not at
        // render time, so an expired entry does not linger until something
        // else happens to redraw the grid.
        const cutoff = Date.now() - SENT_BRIDGE_MS;
        setSentAt((current) => {
          const keep = Object.entries(current).filter(
            ([name, at]) => next[name]?.activity !== "working" && at > cutoff,
          );
          return keep.length === Object.keys(current).length
            ? current
            : Object.fromEntries(keep);
        });
      } catch {
        // The recap poll warns for both feeds; a second warning per tick from
        // the fast poll would only drown it.
      } finally {
        pulling = false;
      }
    };
    void pull();
    const timer = window.setInterval(() => void pull(), ACTIVITY_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [session.id, pollRecaps]);

  /*
   * Is this pane's agent still working, or has it stopped?
   *
   * Three sources, freshest first: the fast `/activity` poll, the recap poll,
   * and the opening value the workspace state carried — so a pane never
   * renders a blank badge, and never renders a stale one for longer than the
   * fast poll's beat. The middle leg looks redundant beside the fast one and
   * is not: `/activity` is the newer route, and against an older backend that
   * does not serve it the fast cache stays empty forever — the recap poll's
   * rows are then what keeps the badges alive, at their old five-second
   * cadence instead of not at all. All three come straight from the pane;
   * nothing is derived here, or two views of one terminal would disagree.
   *
   * On top of that sits the one honest local claim: a prompt THIS window just
   * delivered and the polls have not fetched back yet — see `sentAt`.
   */
  const activityOf = (term: TerminalState) => {
    const live = liveActivity[term.name];
    const row = recaps[term.name];
    const activity = live?.activity ?? row?.activity ?? term.activity ?? "";
    const since = live?.activity_since ?? row?.activity_since ?? term.activity_since ?? 0;
    const worked = live?.worked ?? row?.worked ?? term.worked ?? false;
    const sent = sentAt[term.name];
    // Never over "asking": a CLI that answers a submit with a permission
    // prompt needs the user, and a spinner over that question would promise
    // progress from a pane that is stuck on them.
    const bridging =
      sent !== undefined &&
      Date.now() - sent < SENT_BRIDGE_MS &&
      (activity === "waiting" || activity === "");
    if (bridging) {
      return { activity: "working" as const, since: sent / 1000, worked: true };
    }
    return { activity, since, worked };
  };

  /*
   * The status the badge leans on when this window has no socket view yet.
   *
   * `statuses` is written by each pane's own socket, which exists only after
   * the pane has mounted and connected — so on the first seconds of a
   * workspace (and for panes a view keeps off screen) the rail showed a grey
   * "connecting" spinner for terminals the backend already reported live. The
   * backend's word is the truthful fallback, and one of the two backend
   * sources always has one — every pane in the workspace state carries a
   * status — so this never comes back empty.
   */
  const statusOf = (term: TerminalState) => {
    const socket = statuses[term.name];
    if (socket) return socket;
    return {
      status: (liveActivity[term.name]?.status ?? term.status) as PaneStatus,
      detail: undefined,
    };
  };

  /*
   * The three things a user may do about a pane's recap.
   *
   * Each answers with the pane's new recap, and it is written into the polled
   * map straight away rather than waited for on the next tick: the poll runs
   * every five seconds, and a header that keeps the old sentence for four of
   * them after you pressed Save reads as a save that did not work.
   */
  const applyRecap = useCallback(
    (row: TerminalRecap) => {
      setRecapCache((current) => ({
        workspaceId: session.id,
        rows: {
          ...(current.workspaceId === session.id ? current.rows : {}),
          [row.name]: row,
        },
      }));
    },
    [session.id],
  );

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
  /*
   * The grid is the window, and the workspace is exactly the grid.
   *
   * Its SIZE is nobody's business here any more: the canvas below fills this
   * element on both axes, so every pane's percentage resolves against the
   * visible area and a workspace of twenty panes is twenty smaller panes rather
   * than a wall you scroll along. The one thing that still needs pixels —
   * turning a seam drag into weights — reads the canvas at the moment of the
   * gesture.
   *
   * What survives is the one BOOLEAN the panes need: has this element been laid
   * out at all? A terminal opened during the first, sizeless pass attaches its
   * PTY at one size and is moved to another while its replay is still being
   * parsed (see `geometryReady` in ./AgenticTerminal), which is a real garbled
   * pane rather than a cosmetic flicker.
   */
  const gridRef = useRef<HTMLDivElement | null>(null);
  const [gridMeasured, setGridMeasured] = useState(false);
  useEffect(() => {
    const node = gridRef.current;
    if (!node) return;
    const measured = () => node.clientWidth > 0 && node.clientHeight > 0;
    setGridMeasured(measured());
    const observer = new ResizeObserver(() => setGridMeasured(measured()));
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
  const [frameWidth, setFrameWidth] = useState(0);
  const [frameHeight, setFrameHeight] = useState(0);
  useEffect(() => {
    const node = frameRef.current;
    if (!node) return;
    setFrameWidth(node.clientWidth);
    setFrameHeight(node.clientHeight);
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? node.clientWidth;
      const height = entries[0]?.contentRect.height ?? node.clientHeight;
      setFrameWidth(Math.round(width));
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
  const explorerMax = Math.max(
    EXPLORER_MIN_PX,
    Math.min(
      EXPLORER_MAX_PX,
      frameWidth ? frameWidth - EXPLORER_GRID_RESERVED_PX : EXPLORER_MAX_PX,
    ),
  );
  const explorerPane = useResizablePane({
    storageKey: EXPLORER_WIDTH_KEY,
    defaultSize: EXPLORER_DEFAULT_PX,
    min: EXPLORER_MIN_PX,
    max: explorerMax,
    axis: "x",
    // The explorer is on the right, so its grip is its LEFT edge: dragging
    // left grows the panel and dragging right gives the terminals room back.
    handle: "start",
  });
  // Keep a wider stored preference intact when the window temporarily narrows.
  const requestedExplorer = Math.min(explorerPane.size, explorerMax);
  // Clamped again on render, because the window can shrink under a height that
  // was legal when it was stored — the remembered value stays untouched, so a
  // maximised window gets the tall prompt bar back.
  const requestedComposer = Math.min(composer.size, composerMax);
  const composerCollapsed = requestedComposer < COMPOSER_COLLAPSE_AT_PX;
  const composerHeight = composerCollapsed ? COMPOSER_COLLAPSED_PX : requestedComposer;

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

  /*
   * The sizes the panes are drawn at, and the seams between them.
   *
   * Both live in ONE split tree (`session.layout`): the backend owns its
   * structure — which pane sits where — and a seam drag here edits only its
   * weights, locally first so the gesture never waits on a request, then
   * posted back so the sizes survive a restart and come back with a resumed
   * workspace (see `useTreeSizes`).
   */
  const canvasRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (openedWorkspaceFile) canvas.setAttribute("inert", "");
    else canvas.removeAttribute("inert");
  }, [openedWorkspaceFile]);

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
    (next: LayoutNode) => {
      const live = treeLayout(next, session.terminals);
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
    [session.terminals],
  );

  const sizes = useTreeSizes(
    session.layout ?? null,
    // Measured at the moment of the drag rather than kept in state: the canvas
    // IS the visible workspace now, so its live size is the only truth a
    // pixels-to-weights conversion needs. A canvas that is not there yet
    // reports 0, which `dragSeam` treats as "no room, change nothing".
    useCallback(
      () => ({
        width: canvasRef.current?.clientWidth ?? 0,
        height: canvasRef.current?.clientHeight ?? 0,
      }),
      [],
    ),
    paintDraggedLayout,
  );
  const layout = useMemo(
    () => treeLayout(sizes.tree, session.terminals),
    [session.terminals, sizes.tree],
  );

  /*
   * "Even them out" — every terminal back to the same share of the window.
   *
   * A workspace drifts out of shape one drag at a time: a pane is widened to
   * read a diff, another is squeezed to make room, and an hour later the wall
   * is five different widths for no reason anyone remembers. Straightening it
   * by hand means dragging every seam back and never quite landing on even.
   *
   * It resets the WEIGHTS and nothing else, which is the whole point: the
   * arrangement — which column each terminal is in, which slot down that
   * column — lives in `session.terminals` and only the backend may change it.
   * So no pane is re-ordered, moved to another column or stacked under
   * another one; the boundaries simply even out where they already are. The
   * same act therefore covers every arrangement there is: columns side by
   * side share the width equally, and panes stacked in one column share that
   * column's height equally. "Equal" is measured in TERMINALS, not tree
   * nodes — a nested group of two stacks is two terminals wide and receives
   * two shares, so every pane on screen lands at the same width.
   */
  const evenPanes = useCallback(() => {
    sizes.evenAll();
  }, [sizes.evenAll]);

  /** Would evening out change anything? Answers for the button's own state. */
  const alreadyEven = useMemo(() => isEvenTree(sizes.tree), [sizes.tree]);

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
    const inFlight = sizes.liveTree.current;
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
  const layoutBusy =
    sizes.dragging !== null || composer.isResizing || explorerPane.isResizing;

  const atLimit = session.terminals.length >= maxTerminals;

  /*
   * Chat is a conversation history, so its rail follows ARRIVAL order. The
   * backend's terminal array follows grid coordinates and is deliberately
   * re-sorted after every split or drag; rendering that array directly made a
   * newly spawned Codex session appear in the middle of an existing chat.
   */
  const stableChatKeys = useMemo(
    () =>
      reconcileChatOrder(
        chatOrder.workspaceId === session.id
          ? chatOrder.keys
          : (storedChatOrder(session.id) ?? initialChatOrder(session.terminals)),
        session.terminals,
      ),
    [chatOrder, session.id, session.terminals],
  );
  const chatTerminals = useMemo(
    () => orderChatTerminals(session.terminals, stableChatKeys),
    [session.terminals, stableChatKeys],
  );
  /*
   * The rail's own filter over those panes.
   *
   * Matched against what the row SHOWS — its headline, the prompt behind it,
   * the CLI's name — rather than against the internal call-sign, because
   * "T7" is not what anybody is looking for when they type into a search box.
   * Purely local: no request fires, so it stays instant with a dozen panes.
   */
  const [railFilter, setRailFilter] = useState("");
  const railTerminals = useMemo(() => {
    const needle = railFilter.trim().toLowerCase();
    if (!needle) return chatTerminals;
    return chatTerminals.filter((term) =>
      [term.recap, term.last_prompt, term.display_name, term.agent, term.name]
        .filter(Boolean)
        .some((field) => String(field).toLowerCase().includes(needle)),
    );
  }, [chatTerminals, railFilter]);

  /*
   * The chat rail is a user's reading order, independent of the pane grid.
   * Dropping one row on another exchanges only their stable lifetime keys: no
   * terminal moves, remounts or reconnects, and the existing storage effect
   * below makes the chosen order survive the next visit.
   */
  const suppressRailClickUntil = useRef(0);
  const swapRailTerminals = useCallback(
    (moved: string, targetName: string) => {
      const movedTerminal = session.terminals.find((terminal) => terminal.name === moved);
      const targetTerminal = session.terminals.find(
        (terminal) => terminal.name === targetName,
      );
      if (!movedTerminal || !targetTerminal) return;
      const next = swapChatOrder(
        stableChatKeys,
        chatTerminalIdentity(movedTerminal),
        chatTerminalIdentity(targetTerminal),
      );
      if (next === stableChatKeys) return;
      // Releasing over a row also produces a click in Chromium. Suppress that
      // compatibility click so a reorder does not unexpectedly change stages.
      suppressRailClickUntil.current = Date.now() + 250;
      setChatOrder({ workspaceId: session.id, keys: next });
    },
    [session.id, session.terminals, stableChatKeys],
  );
  const railArrange = usePaneArrange(
    useCallback(
      (moved: string, targetName: string) => swapRailTerminals(moved, targetName),
      [swapRailTerminals],
    ),
  );
  const canReorderRail =
    chatView && !selectionMode && !busy && !working && railTerminals.length > 1;

  useEffect(() => {
    rememberChatOrder(session.id, stableChatKeys);
    if (chatOrder.workspaceId === session.id && chatOrder.keys === stableChatKeys) {
      return;
    }
    setChatOrder({ workspaceId: session.id, keys: stableChatKeys });
  }, [chatOrder, session.id, stableChatKeys]);

  /*
   * A pane can arrive through voice, the CLI, or another client. Effects run
   * after child effects, which is too late to put that pane on the chat stage:
   * its terminal would already have connected from a hidden, grid-sized cell
   * and handed the PTY a tiny column count. Derive the arrival synchronously so
   * the new terminal's first measurement is the full stage.
   */
  const knownBeforeRender = knownPanes.current;
  const arrivingChatPane =
    chatView && knownBeforeRender !== null
      ? (session.terminals
          .filter((term) => !knownBeforeRender.has(term.name))
          .at(-1)?.name ?? null)
      : null;

  /** The pane on the chat stage: the newest arrival, chosen pane, then fallback. */
  const chatSelected = useMemo(() => {
    const names = session.terminals.map((term) => term.name);
    if (arrivingChatPane && names.includes(arrivingChatPane)) return arrivingChatPane;
    if (chatPane && names.includes(chatPane)) return chatPane;
    if (target && names.includes(target)) return target;
    return names[0] ?? null;
  }, [arrivingChatPane, chatPane, target, session.terminals]);

  /*
   * The browser is the only layer that knows what is actually on screen. Hand
   * that fact to the backend so voice and chat can resolve "this terminal"
   * from what the user sees.
   *
   * `stagedPane` is the ONE pane the view puts in front of the user: chat's
   * stage. Grid has no such pane and reports none — guessing among a dozen
   * visible terminals would be less honest than making the user name one.
   */
  const stagedPane = chatView ? chatSelected : null;
  useEffect(() => {
    void syncAgenticIdeSurface({
      workspaceId: session.id,
      view: viewMode,
      onScreen,
      terminal: onScreen ? stagedPane : null,
      promptTarget: onScreen ? target || null : null,
    }).catch((error) => {
      console.warn("Agentic IDE: could not sync the visible terminal:", error);
    });
  }, [onScreen, session.id, stagedPane, target, viewMode]);

  /** Rail click: show the pane, and aim the composer at it when it can listen. */
  const selectChatPane = useCallback((name: string, promptable: boolean) => {
    setChatPane(name);
    if (promptable) setTarget(name);
  }, []);

  const split = async (anchor: string | null, direction: SplitDirection, agent?: string) => {
    setWorking(true);
    try {
      const next = await addTerminal({
        anchor: anchor ?? undefined,
        direction,
        agent,
      });
      onSessionChanged?.(next);
      // A fresh pane should receive the next prompt — that is why it was opened.
      const known = new Set(session.terminals.map((t) => t.name));
      const added = next.terminals.find((t) => !known.has(t.name));
      // A split HALVES the pane it was asked of and touches nothing else —
      // the backend's tree already says so (`layout_tree.split_pane`), and
      // `next.layout` carries the result. Nothing to remap here any more.
      // ...unless it is a plain terminal — that one is typed into by hand, and
      // stealing the target would silently redirect the next prompt into a pane
      // that refuses it.
      if (added && takesPrompts(added)) setTarget(added.name);
      // The chat stage shows what was just opened — in the grid the new pane
      // simply appears, but on a one-pane stage an unshown terminal would read
      // as a split that did nothing.
      if (added) setChatPane(added.name);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setWorking(false);
    }
  };

  const offersChoice = offersAgentChoice(agents);

  /**
   * Open a terminal at the end of the row — asking WHAT first when the machine
   * has more than one CLI installed. With a single one there is nothing to pick,
   * so the click opens it straight away rather than showing a one-entry menu.
   */
  const openTerminal = (surface: "rail" | "empty") => {
    if (offersChoice) setPicking((current) => (current === surface ? null : surface));
    else void split(null, "right");
  };

  /*
   * A pane that MOVED has to be seen landing.
   *
   * Reported 2026-08-07 as "dragging terminals just doesn't work": every drop
   * had in fact been committed and persisted, but among a wall of
   * near-identical panes a column swap repaints in one frame with nothing
   * marking the pane that travelled — indistinguishable from nothing having
   * happened. The panes now glide to their new boxes (see the cell's
   * transition class) and the one that was carried wears the arrival ring.
   * A nonce rather than the bare name, so dropping the same pane twice in a
   * row re-arms the ring instead of the second drop landing unannounced.
   */
  const [justMoved, setJustMoved] = useState<{ name: string; nonce: number } | null>(
    null,
  );
  useEffect(() => {
    if (justMoved === null) return;
    const timer = window.setTimeout(() => setJustMoved(null), 2600);
    return () => window.clearTimeout(timer);
  }, [justMoved]);

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
        /*
         * A drop that changes nothing must SAY so. "Right of T4" is a legal
         * drop for the pane already sitting right of T4, and the backend
         * answers it with the unchanged workspace — correct, and exactly what
         * a user reads as "dragging is broken" when the grid just sits there
         * (they repeat the identical gesture, observed 2026-08-07). Saying
         * "already there" turns a silent nothing into an answer.
         */
        // The tree IS the placement, so "did anything move" is one comparison
        // — including moves the coarse column/slot hints cannot see, like a
        // drop that only changes nesting.
        if (
          JSON.stringify(next.layout ?? null) ===
          JSON.stringify(session.layout ?? null)
        ) {
          pushToast(
            "info",
            t("agentic_grid.arrange.already_there").replace("{0}", moved),
          );
          return;
        }
        onSessionChanged?.(next);
        setJustMoved({ name: moved, nonce: Date.now() });
      } catch (error) {
        pushToast("error", (error as Error).message);
      } finally {
        setWorking(false);
      }
    },
    [onSessionChanged, pushToast, session.layout, t],
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
   * The layout watchdog: the screen has to MATCH the layout, and when it does
   * not, the grid repairs itself instead of standing there looking broken.
   *
   * `paneLayout` cannot produce overlapping boxes, but the boxes reach the
   * screen through several hands — React's style props, the imperative drag
   * painter above, the 300 ms move glide, the maximize style swap — and any of
   * them interrupted at the wrong moment leaves a pane standing on its
   * neighbour while every status the app shows stays green, because the data
   * is right and only the pixels are wrong (maintainer report 2026-08-11: a
   * terminal half-covered by the pane beside it, workspace claiming all well).
   *
   * So, a beat after every layout change and every few seconds after that, the
   * visible cells are measured and judged by `findLayoutViolations`: no two
   * panes may intersect AT ALL, none may reach past the canvas into its
   * overflow clip, and no terminal's rendered screen may be bigger than its
   * pane. A fault is answered by rewriting every cell and seam from the layout
   * React already believes in — the truth is right here, it just has to be put
   * back on screen — and a stale terminal fit by the same resize pass a real
   * window resize triggers. The console says what was found, once per
   * incident, so a repair that did not hold leaves evidence instead of a loop.
   *
   * Held back whenever the pixels are ALLOWED to disagree for a moment: while
   * a seam or the prompt bar is dragged (painted imperatively), while a pane
   * is held for rearranging, under a maximize or a single-pane view (the
   * others measure 0x0 and there is nothing to overlap), and while nobody is
   * looking. The first check waits out the move glide, and a layout change
   * restarts the wait — mid-glide panes genuinely do cross each other.
   */
  const guardActive =
    onScreen &&
    documentVisible &&
    !chatView &&
    maximized === null &&
    arrange.held === null &&
    !layoutBusy;
  useEffect(() => {
    if (!guardActive) return;
    let cancelled = false;
    // One warning per incident, one escalation per failed repair — the guard
    // runs forever, and a console filling with the same line every three
    // seconds would bury the evidence it exists to leave.
    let announced = false;
    let escalated = false;

    const measure = () => {
      const surface = canvasRef.current?.getBoundingClientRect();
      if (!surface || surface.width <= 0 || surface.height <= 0) return null;
      const panes: MeasuredPane[] = [];
      for (const term of session.terminals) {
        const node = paneNodes.current.get(term.name);
        if (!node) continue;
        const rect = node.getBoundingClientRect();
        // The terminal's own drawn surface, checked against the tile that
        // holds it. This used to be skipped for a pane that was DELIBERATELY
        // drawn wider than its tile and clipped at the edge — the arrangement
        // the maintainer read as terminals standing on one another, removed on
        // 2026-08-11. With every pane fitted to its tile there is no exception
        // left to make, and this watchdog is now what proves the rule: a
        // terminal wider than the pane showing it is a fault, always.
        const content = node
          .querySelector(".xterm-screen")
          ?.getBoundingClientRect();
        panes.push({
          name: term.name,
          left: rect.left,
          top: rect.top,
          width: rect.width,
          height: rect.height,
          content: content
            ? {
                left: content.left,
                top: content.top,
                width: content.width,
                height: content.height,
              }
            : undefined,
        });
      }
      return {
        panes,
        canvas: {
          left: surface.left,
          top: surface.top,
          width: surface.width,
          height: surface.height,
        },
      };
    };

    const check = () => {
      if (cancelled) return;
      const measured = measure();
      if (!measured) return;
      const violations = findLayoutViolations(measured.panes, measured.canvas);
      if (!hasLayoutViolations(violations)) {
        announced = false;
        escalated = false;
        return;
      }
      if (!announced) {
        announced = true;
        console.warn(
          "Agentic IDE: the workspace drifted from its layout — repairing:",
          describeLayoutViolations(violations),
        );
      } else if (!escalated) {
        escalated = true;
        console.error(
          "Agentic IDE: the workspace layout fault survived a repair:",
          describeLayoutViolations(violations),
        );
      }
      // Positions come back from the layout React already holds — the same
      // numbers its style props carry, written directly because a stale
      // inline style is invisible to React's diff (an unchanged prop is an
      // unwritten prop).
      session.terminals.forEach((term, index) => {
        const node = paneNodes.current.get(term.name);
        const box = layout.boxes[index];
        if (node && box) writePosition(node, paneBoxStyle(box));
      });
      for (const seam of layout.seams) {
        const node = seamNodes.current.get(seam.id);
        if (node) writePosition(node, seamStyle(seam));
      }
      // A terminal bigger than its pane is a missed refit — and so is one far
      // narrower, drawing its output as a thin strip down a wide tile; every
      // pane listens to window resizes with a debounced, no-op-when-unchanged
      // fit, so this is the ordinary path to a fresh measurement, not a
      // special one.
      if (violations.clipped.length > 0 || violations.underfit.length > 0) {
        window.dispatchEvent(new Event("resize"));
      }
    };

    // Past the 300 ms glide, then a slow patrol — measuring a dozen rects is
    // cheap, but forcing layout more often than this buys nothing.
    const first = window.setTimeout(check, 450);
    const patrol = window.setInterval(check, 3000);
    return () => {
      cancelled = true;
      window.clearTimeout(first);
      window.clearInterval(patrol);
    };
  }, [guardActive, layout, session.terminals]);

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
  useEffect(() => {
    const names = session.terminals.map((term) => term.name);
    const known = knownPanes.current;
    knownPanes.current = new Set(names);
    if (known === null) return;
    const fresh = names.filter((name) => !known.has(name));
    if (fresh.length === 0) return;
    setJustOpened(new Set(fresh));
    // On the chat stage, "seen appearing" means TAKING the stage: a pane
    // opened by voice would otherwise exist only as a rail entry behind the
    // one being read, which is the 2026-07-28 "terminals just don't load"
    // report in new clothes.
    let frame: number | undefined;
    if (chatView) {
      setChatPane(fresh[fresh.length - 1]);
      // Chat rows are arrival-ordered, so the new session belongs at the rail's
      // tail. Scrolling the hidden grid cell used to move the whole workspace
      // and then snap the terminal again after its delayed fit.
      frame = requestAnimationFrame(() => {
        const rail = chatRailRef.current;
        if (rail) rail.scrollTop = rail.scrollHeight;
      });
    }
    // In the grid there is nothing to scroll to: the workspace is one screenful
    // by rule, so a pane that just opened is already in view and the ring below
    // is the whole announcement. This used to call `scrollIntoView` on the
    // newest pane, which was the honest answer while a workspace could be wider
    // or taller than its window.
    const timer = window.setTimeout(() => setJustOpened(new Set()), 2600);
    return () => {
      if (frame !== undefined) cancelAnimationFrame(frame);
      window.clearTimeout(timer);
    };
  }, [session.terminals]);

  /*
   * "Jump to pane" — from the header bell, or from the view after a tab switch.
   *
   * Two things: the pane is maximized, and it wears the same arrival ring a
   * freshly opened pane does. Maximizing is what the user asked for — a
   * notification about a pane is a request to READ that pane, and reading a
   * postcard-sized terminal in a grid of twelve is the problem, not the answer.
   * The ring covers the case where the pane was already the maximized one and
   * nothing visibly moved.
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
    // The jump means "let me READ that pane". In the grid that is a maximize;
    // on the chat stage it is the stage showing that pane.
    if (chatView) setChatPane(wanted);
    else setMaximized(wanted);
    setJustOpened(new Set([wanted]));
    const timer = window.setTimeout(() => setJustOpened(new Set()), 2600);
    return () => window.clearTimeout(timer);
    // `session.terminals` deliberately absent: this fires on a JUMP, not every
    // time a pane opens or closes underneath one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jump]);

  /**
   * Give one pane another call-sign.
   *
   * The backend changes a label and nothing else — the agent keeps running and
   * keeps its conversation. This side has more to do than redraw, because a
   * call-sign is the key half this grid files a pane's UI state under: which
   * pane the prompt bar types into, which one is maximized, which are selected,
   * what each is doing. Every one of those maps is carried across to the new
   * name here rather than left to expire, since a pane that loses its focus,
   * its maximized state and its status line the moment it is renamed reads as a
   * rename that restarted something.
   *
   * `knownPanes` is carried too: it is what tells a genuinely NEW pane apart
   * from one that was already here, and without this a rename would announce
   * the pane as freshly opened.
   *
   * Answers whether the name was taken, so the editor can stay open on a
   * refusal (a duplicate call-sign) with the text still in it.
   */
  const renamePane = useCallback(
    async (pane: TerminalState, wanted: string): Promise<boolean> => {
      const from = pane.name;
      const cleaned = wanted.trim();
      if (!cleaned || cleaned === from) return true;
      setWorking(true);
      try {
        const next = await renameTerminal(from, cleaned);
        // The name the backend really settled on, read back off the pane's key
        // rather than assumed from the input — the key is what survives a
        // rename, which is exactly why it is the thing to look the pane up by.
        const to = next.terminals.find((term) => term.key === pane.key)?.name ?? cleaned;
        setStatuses((current) => rekey(current, from, to));
        setRecapCache((current) =>
          current.workspaceId === session.id
            ? { ...current, rows: rekey(current.rows, from, to) }
            : current,
        );
        setActivityCache((current) =>
          current.workspaceId === session.id
            ? { ...current, rows: rekey(current.rows, from, to) }
            : current,
        );
        setSentAt((current) => rekey(current, from, to));
        setRestartTokens((current) => rekey(current, from, to));
        // The layout tree is keyed by the pane's KEY, which is exactly what a
        // rename leaves alone — sizes need no rekeying at all.
        setTarget((current) => (current === from ? to : current));
        setMaximized((current) => (current === from ? to : current));
        setPendingClose((current) => (current === from ? to : current));
        setSelectedTerminals((current) => {
          if (!current.has(from)) return current;
          const set = new Set(current);
          set.delete(from);
          set.add(to);
          return set;
        });
        const known = knownPanes.current;
        if (known?.has(from)) {
          known.delete(from);
          known.add(to);
        }
        onSessionChanged?.(next);
        return true;
      } catch (error) {
        pushToast("error", (error as Error).message);
        return false;
      } finally {
        setWorking(false);
      }
    },
    [onSessionChanged, pushToast, session.id],
  );

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
    !chatView &&
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
      // The survivors keep the room they were dragged to — the backend's tree
      // dissolves the closed pane's share to its siblings (`remove_pane`).
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

  /**
   * Compose and deliver in one request — the same thing "prompt Mika …" does
   * by voice.
   *
   * The backend rewrites the typed instruction into a briefed task with the
   * relevant `@file` references attached and types THAT into the pane. It owns
   * every failure mode: a slow writer is hedged, a dead one is substituted,
   * and when no capable model is reachable the deterministic rendering ships
   * instead — so the pane always receives a prompt, never nothing. An earlier
   * version composed in a dry run first and held the result for approval, with
   * a "Send verbatim" escape; the maintainer retired that detour (2026-08-12)
   * because its fallback paths sent the raw text, and the typed bar must
   * behave exactly like the spoken one.
   *
   * On a failed REQUEST nothing was typed anywhere, so the draft stays in the
   * editor rather than being retried verbatim — silently downgrading to the
   * raw text is the one behaviour this path exists to rule out.
   */
  const send = async (draft: string) => {
    const text = draft.trim();
    if (!text || !target) return;
    setSending(true);
    try {
      const result = await promptTerminal(target, text, { compose: true, attachments });
      replacePrompt("");
      setAttachments([]);
      // The pane's badge flips to "working" NOW, not when the poll catches the
      // screen moving: the user just watched this send succeed, and a badge
      // still saying "done" for the next several seconds reads as a send that
      // silently failed. See `sentAt` for how the backend takes back over.
      if (!result || result.submitted !== false) {
        setSentAt((current) => ({ ...current, [target]: Date.now() }));
      }
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
      // No delivery event will arrive to clear the narration for this send.
      setComposeBeat(null);
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
            <span className="truncate font-display text-sm font-semibold">{project.name}</span>
          </div>
        )}

        {/* Which branch the agents are on. It stays even in the merged row —
            the workspace tab names the folder, not the checkout, and "which
            branch am I about to let a dozen agents commit to" is not a
            question worth answering from memory. Plain muted text rather than
            a chip: it is a fact to glance at, not a control to weigh. */}
        {branchLabel && (
          <span
            className="hidden shrink-0 font-mono text-[11px] text-muted-foreground xl:inline"
            title={session.folder}
          >
            {branchLabel}
          </span>
        )}

        <ToolbarOverflow>

        {/* Focus mode — the explicit switch into "Jarvis codes with me here".
            A quiet glyph that lights up while the mode is on; the one-time
            intro dialog and the tooltip carry the explanation. */}
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
          className={cn(TOOLBAR_BTN, focusMode && TOOLBAR_BTN_ON)}
        >
          <Brain className="h-4 w-4 shrink-0" />
        </button>

        {/* Grid or chat — how the same agents are read.

            Two buttons rather than one cycling through both states, because
            the switch stays readable when a third mode is added: "click
            again" stops telling you where you will land. They are ordinary
            toolbar glyphs, not a segmented group in a box — the row's rule is
            one button shape throughout, with colour reserved for what is ON.

            `agentic-view-mode-toggle` stays on the chat button: it is the one
            this control used to be, and the tests that press it are about
            chat view rather than about the switch. */}
        {VIEW_BUTTONS.map(({ view, testId, icon: Icon, title }) => (
          <button
            key={view}
            type="button"
            data-testid={testId}
            aria-pressed={viewMode === view}
            onClick={() => {
              setViewMode(view);
              // A conversation surface with no input box is a screenshot, so
              // chat view brings the composer with it.
              if (view !== "grid" && composerCollapsed) {
                resizeComposer(COMPOSER_DEFAULT_PX);
              }
            }}
            title={title}
            className={cn(TOOLBAR_BTN, viewMode === view && TOOLBAR_BTN_ON)}
          >
            <Icon className="h-4 w-4 shrink-0" />
          </button>
        ))}

        {/* The current workspace's files, in the familiar editor-explorer
            position. It is an independent panel rather than a fourth reading
            mode: opening it must not hide or remount a live terminal. */}
        <button
          type="button"
          data-testid="workspace-explorer-toggle"
          aria-pressed={explorerOpen}
          aria-expanded={explorerOpen}
          aria-controls="workspace-explorer"
          onClick={() => setExplorerOpen((open) => !open)}
          title={t("agentic_grid.explorer.toggle")}
          aria-label={t("agentic_grid.explorer.toggle")}
          className={cn(TOOLBAR_BTN, explorerOpen && TOOLBAR_BTN_ON)}
        >
          <Files className="h-4 w-4 shrink-0" aria-hidden />
        </button>

        {/* Even out the sizes. Beside the grid/chat toggle because both are
            about the SHAPE of the workspace rather than what is in it, and
            before the appearance controls because it changes the panes
            themselves, not how their text is drawn.

            Off in chat view and while a pane is maximized: both hide the
            boundaries this evens out, so the click would be a change nobody
            can see — and a control whose effect is invisible reads as a dead
            one. The tooltip says which of the reasons applies. */}
        <button
          type="button"
          data-testid="agentic-even-panes"
          onClick={evenPanes}
          disabled={chatView || maximized !== null || alreadyEven}
          title={
            chatView || maximized !== null
              ? t("agentic_grid.even.grid_only")
              : alreadyEven
                ? t("agentic_grid.even.already")
                : t("agentic_grid.even.hint")
          }
          aria-label={t("agentic_grid.even.label")}
          className={TOOLBAR_BTN}
        >
          <AlignHorizontalDistributeCenter className="h-4 w-4 shrink-0" />
        </button>

        {/* Appearance stays behind one quiet menu; text size is deliberately
            exposed beside it. Somebody opening this workspace because the
            terminal is too small must be able to find the remedy without
            already knowing which anonymous glyph hides it. */}
        <ViewMenu
          appearance={appearance}
          onAppearance={setAppearance}
        />
        <TerminalFontSizeControl
          fontSize={fontSize}
          onFontSize={setFontSize}
        />

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

        {/* Summon or dismiss the floating voice bubble. The glyph pulses gold
            while a conversation runs so a closed bubble still has a visible
            heartbeat somewhere on screen. */}
        {onToggleVoice && (
          <WorkspaceVoiceButton open={voiceOpen} onToggle={onToggleVoice} />
        )}

        <button
          ref={selectionToggleRef}
          type="button"
          data-testid="terminal-selection-toggle"
          aria-pressed={selectionMode}
          onClick={selectionMode ? leaveSelectionMode : enterSelectionMode}
          title={t("agentic_grid.selection.hint")}
          aria-label={
            selectionMode ? t("agentic_grid.selection.finish") : t("agentic_grid.selection.start")
          }
          className={cn(TOOLBAR_BTN, selectionMode && TOOLBAR_BTN_ON)}
        >
          <ListChecks className="h-4 w-4 shrink-0" />
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
                setSelectedTerminals(new Set(session.terminals.map((terminal) => terminal.name)))
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
              className={cn(TOOLBAR_BTN, "hover:bg-destructive/10 hover:text-destructive")}
              disabled={busy}
              aria-label="Close workspace"
              title="Close the workspace and stop every agent in it"
            >
              <Power className="h-4 w-4 shrink-0" />
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
        </ToolbarOverflow>
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

        Chat view is the same trick one step further: the rail on the left is
        ALWAYS in the tree (hidden in grid mode), and switching modes only
        flips class names — the scroller and every cell keep their place in
        the element tree, so React re-parents nothing and no agent dies for a
        change of clothes.
      */}
      <div className="flex min-h-0 flex-1">
        {/* ------------------------------------------------------ chat rail */}
        <aside
          data-testid="agentic-chat-rail"
          className={cn(
            /*
             * 224 px, and the header is the same 44 px rule-under-a-label as
             * the voice column opposite it. Chat view puts THREE vertical
             * bands in front of the one pane being read — the app's own
             * navigation, this list, and the voice column — so the two this
             * view owns at least agree with each other about how a column
             * begins.
             */
            "w-56 shrink-0 flex-col border-r border-border",
            chatView ? "flex" : "hidden",
          )}
        >
          {/*
            The column's own head and name.

            Not "AGENTS" in small caps any more. This column lists the
            conversations you are having, grouped by the folder they are in —
            "Chat" is what that is.
          */}
          <div className="flex h-11 shrink-0 items-center gap-1 px-2">
            <span className="flex-1 truncate px-1 text-sm font-semibold tracking-tight">
              Chat
            </span>
          </div>

          <div className="px-2 pb-2">
            <div className="relative flex items-center">
              <Search className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-muted-foreground/70" />
              <input
                value={railFilter}
                onChange={(event) => setRailFilter(event.target.value)}
                placeholder="Search"
                aria-label="Search chats"
                className="h-8 w-full rounded-md border border-transparent bg-foreground/[0.045] pl-7 pr-2 text-xs outline-none placeholder:text-muted-foreground/60 focus-visible:border-border focus-visible:bg-transparent"
              />
            </div>
          </div>

          {/*
            The three things a person arrives wanting to do, as rows with words
            on them. They used to be one bare "+" in the header, which cannot
            say which of the three it is.
          */}
          <div className="relative px-1.5 pb-1">
            <RailAction
              icon={SquarePen}
              label="New chat"
              testId="chat-rail-new-terminal"
              disabled={atLimit || busy || working}
              onClick={() => openTerminal("rail")}
            />
            {onNewSession && (
              <RailAction
                icon={Plus}
                label="New session"
                hint="A workspace with no project folder"
                testId="chat-rail-new-session"
                disabled={busy || working}
                onClick={onNewSession}
              />
            )}
            {onAddProject && (
              <RailAction
                icon={FolderPlus}
                label="Add project"
                testId="chat-rail-add-project"
                disabled={busy || working}
                onClick={onAddProject}
              />
            )}
            {picking === "rail" && (
              <AgentPickerMenu
                title="Open a terminal — what?"
                ariaLabel="What should run in the new terminal?"
                agents={agents ?? []}
                testId="chat-rail-agent-menu"
                itemTestId={(agent) => `chat-rail-new-${agent}`}
                className="left-2 top-full"
                onDismiss={() => setPicking(null)}
                onPick={(agent) => {
                  setPicking(null);
                  void split(null, "right", agent);
                }}
              />
            )}
          </div>

          <div
            ref={chatRailRef}
            className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-1.5 pb-2"
          >
            <RailBand>Projects</RailBand>
            {/*
              The folder these panes are in, named and coloured like the
              project it is. Every row under it belongs to it — which is the
              whole reason this band exists: with two workspaces open, a flat
              list of eleven conversations says nothing about which repository
              any of them is touching.
            */}
            <div className="flex items-center gap-1.5 rounded-md px-2 py-1.5">
              <span
                className="h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ background: folderColor(project.path || project.name) }}
                aria-hidden
              />
              <span className="min-w-0 flex-1 truncate text-xs font-medium">
                {project.name}
              </span>
            </div>

            {railTerminals.map((term) => {
              const state = statusOf(term);
              const headline = recaps[term.name]?.recap ?? term.recap;
              const title =
                headline?.trim() ||
                term.last_prompt?.trim() ||
                `${term.display_name || term.name} session`;
              const marked = selectedTerminals.has(term.name);
              const active = chatSelected === term.name;
              return (
                /*
                 * The row and its close button are SIBLINGS, never nested: a
                 * button inside a button is invalid HTML, and browsers resolve
                 * it by dropping one of them — usually the one you wanted.
                 */
                <div
                  key={term.key}
                  ref={railArrange.registerCell(term.name)}
                  data-testid={`chat-rail-item-${term.name}`}
                  data-terminal={term.name}
                  className={cn(
                    "group relative ml-3 mb-0.5 rounded-md transition-[opacity,box-shadow,background-color]",
                    railArrange.held === term.name && "opacity-50",
                    railArrange.hover?.target === term.name &&
                      "bg-primary/10 ring-2 ring-inset ring-primary/70",
                  )}
                >
                  <button
                    type="button"
                    data-testid={`chat-rail-${term.name}`}
                    aria-label={`${title}, ${term.display_name || term.agent}`}
                    aria-pressed={selectionMode ? marked : active}
                    onPointerDown={
                      canReorderRail
                        ? (event) => railArrange.start(term.name, event)
                        : undefined
                    }
                    onClick={(event) => {
                      if (Date.now() < suppressRailClickUntil.current) {
                        event.preventDefault();
                        return;
                      }
                      // Selection mode borrows the rail: in chat view the grid's
                      // per-pane overlays are hidden with their panes, so the
                      // rail is where a multi-close is composed.
                      selectionMode
                        ? toggleTerminalSelection(term.name)
                        : selectChatPane(term.name, takesPrompts(term));
                    }}
                    /*
                     * Selection is a barely-there lift of the surface — no hue,
                     * no border, no fill. Eleven rows of tinted boxes turn a
                     * list into a heat map and leave the accent with nothing
                     * distinct to say when a row actually wants attention. The
                     * one exception is a MARKED row in selection mode, which is
                     * a deliberate armed state and should look like one.
                     */
                    className={cn(
                      "w-full rounded-md px-2 py-[5px] text-left transition-colors",
                      selectionMode && marked
                        ? "bg-primary/15"
                        : active && !selectionMode
                          ? "bg-foreground/[0.09]"
                          : "hover:bg-foreground/[0.055]",
                      canReorderRail &&
                        (railArrange.held === term.name
                          ? "cursor-grabbing"
                          : "cursor-grab"),
                    )}
                    style={{ touchAction: canReorderRail ? "none" : undefined }}
                  >
                    {/* The right padding is permanent, not applied on hover:
                        the close button appears where the badge would otherwise
                        be, and a status pill that jumps sideways under the
                        cursor is a row nobody can aim at. */}
                    <span className="flex items-center gap-1.5 pr-5">
                      {selectionMode && (
                        <Check
                          className={cn(
                            "h-3.5 w-3.5 shrink-0",
                            marked ? "text-primary" : "text-transparent",
                          )}
                        />
                      )}
                      <span className="flex min-w-0 flex-1 items-center gap-2">
                        {/* The mark leads, at the weight of the text beside it
                            — it says which CLI this is, it is not the subject
                            of the row. The title is. */}
                        <AgentMark
                          agent={term.agent}
                          label={term.display_name || term.agent}
                          size="sm"
                          variant="plain"
                        />
                        {/* The app's own bubble, not `title=`: the native
                            tooltip takes over a second to appear and is drawn
                            in the OS's grey box — a foreign artifact over a
                            rail whose whole point is the title. */}
                        <QuickTooltip
                          content={title}
                          side="right"
                          className="min-w-0 flex-1"
                        >
                          <span
                            data-testid={`chat-rail-title-${term.name}`}
                            className={cn(
                              "block truncate text-[12px]",
                              active ? "text-foreground" : "text-foreground/75",
                            )}
                          >
                            {title}
                          </span>
                        </QuickTooltip>
                      </span>
                      {/* Not "live". Every pane in this list is live, all day —
                          what the user is scanning for is which of them still
                          owes them something. See ./PaneActivityPill. */}
                      <PaneActivityPill
                        status={state.status}
                        detail={state.detail}
                        {...activityOf(term)}
                      />
                    </span>
                  </button>
                  {/*
                    Closing one terminal from the list itself.
                    Chat view hides every pane but the one on the stage, and with
                    it the header that used to be the only place a single
                    terminal could be closed — so the eleven panes you are NOT
                    looking at could only be closed by switching to the grid or
                    by composing a multi-select. This is the row's own button.

                    Left out of selection mode on purpose: that mode is here to
                    close SEVERAL, and a per-row close beside a checkbox is two
                    answers to one question.
                  */}
                  {!selectionMode && (
                    <button
                      type="button"
                      data-testid={`chat-rail-close-${term.name}`}
                      disabled={busy || working}
                      onClick={() => setPendingClose(term.name)}
                      title={`Close ${term.name} and stop what is running in it`}
                      aria-label={`Close ${term.name}`}
                      className={cn(
                        "absolute right-1 top-1.5 flex h-5 w-5 items-center justify-center rounded",
                        "text-muted-foreground opacity-0 transition-opacity",
                        "hover:bg-destructive/20 hover:text-destructive",
                        "group-hover:opacity-100 group-focus-within:opacity-100",
                        "focus-visible:opacity-100 disabled:cursor-not-allowed",
                      )}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </aside>
      <div
        ref={gridRef}
        data-testid="agentic-grid"
        className={cn(
          // NEVER scrolls, on either axis. The workspace is one screenful by
            // rule (maintainer, 2026-08-04): opening a pane makes every pane a
            // little smaller, and finding the seventh terminal is never a
            // matter of scrolling sideways to it. `overflow-hidden` is the
            // whole enforcement — the canvas below is sized to this element, so
            // there is nothing to scroll to in the first place, and this only
            // guarantees that a rounding error or a pane's own overflow cannot
            // quietly hand the grid a scrollbar back.
            "relative min-h-0 min-w-0 flex-1 overflow-hidden",
            // A drag across the grid would otherwise sweep a text selection over
            // every header and label it crosses.
            arrange.held !== null && "select-none",
        )}
        // Inline rather than a utility class so ONE number drives both the
        // rendered outer margin and the width the column count is computed from.
        style={{ padding: chatView ? 12 : GRID_GAP_PX }}
      >
      {/*
        The surface the fractions resolve against — always exactly the window.

        It kept its own size until 2026-08-04, growing past the grid so the
        panes could stay above a minimum and the workspace scrolled to reach
        them. That is the behaviour this element now exists to prevent: a pane
        is a share of what is on screen, so the twelfth terminal makes the other
        eleven smaller and stays in the same view as them.
      */}
      <div
        ref={canvasRef}
        data-testid="agentic-grid-canvas"
        className="relative h-full w-full"
        aria-hidden={openedWorkspaceFile !== null}
      >
        {session.terminals.map((term, index) => {
          const box = layout.boxes[index];
          const isMaximized = maximized === term.name;
          // The one pane the single-pane view is showing. It stages one and
          // hides the rest — the others are hidden and NEVER unmounted.
          const onStage = chatView && chatSelected === term.name;
          return (
            <div
              key={term.key}
              ref={registerPaneCell(term.name)}
              data-testid={`pane-cell-${term.name}`}
              className={cn(
                "absolute min-h-0 min-w-0 rounded-lg",
                // Rearranging has to be SEEN: boxes glide to their new places
                // instead of teleporting in one repaint (see `justMoved`). Off
                // while a seam is being dragged — those frames are written
                // imperatively (`paintDraggedLayout`) and must track the
                // pointer, not ease after it — and off for the stage/maximize
                // style, which swaps to `inset` and cannot tween from here.
                !chatView &&
                  !isMaximized &&
                  !layoutBusy &&
                  "transition-[left,top,width,height] duration-300 ease-out motion-reduce:transition-none",
                /*
                 * The three rings below are drawn WITHOUT an offset, and that
                 * is arithmetic rather than taste.
                 *
                 * Neighbouring panes sit `GRID_GAP_PX` apart, and each of them
                 * gives up half of that on a shared side (see `paneBoxStyle`).
                 * A pane therefore owns exactly `HALF_GAP_PX` — 2 px — outside
                 * its own box before it is standing on its neighbour. A ring
                 * with `ring-offset-2` needs FOUR: two for the offset band and
                 * two for the ring itself. Marking one pane consequently drew
                 * over the edge of the pane beside it, and drew the offset band
                 * in the app's opaque background — a hard grey stripe across
                 * glass that is otherwise showing the desktop through it.
                 *
                 * At `ring-2` and no offset the ring fills precisely the gap
                 * this pane owns and stops in the middle of it. Two selected
                 * neighbours meet; neither covers the other.
                 */
                selectedTerminals.has(term.name) && "ring-2 ring-primary",
                // Just arrived — see `justOpened`. Second to the selection ring
                // deliberately: selection is a thing the user is DOING, and it
                // must keep its own answer while panes come and go.
                justOpened.has(term.name) &&
                  !selectedTerminals.has(term.name) &&
                  "ring-2 ring-primary/70",
                // Just landed after a drag — same ring, same reasoning: the
                // move happened, and the grid says WHERE.
                justMoved?.name === term.name &&
                  !selectedTerminals.has(term.name) &&
                  "ring-2 ring-primary/70",
                chatView
                  ? onStage
                    ? /*
                       * ONE edge per pane, in every view.
                       *
                       * This cell used to add `rounded-xl border border-border`
                       * of its own around a pane that already draws a rounded
                       * border in the TERMINAL's colours — two hairlines, at
                       * two different radii (12 px outside an 8 px corner), so
                       * every corner of the staged pane showed a sliver of app
                       * chrome curving around the pane's own. In grid view the
                       * same pane has a single edge, which is what made the
                       * switch to chat look like a different component rather
                       * than the same one enlarged.
                       *
                       * The lift stays: a staged pane genuinely IS raised above
                       * the rail beside it.
                       */
                      "shadow-lg"
                        : "hidden"
                      : maximized !== null && !isMaximized && "hidden",
                  )}
                  style={isMaximized || chatView ? MAXIMIZED_BOX : paneBoxStyle(box)}
                >
                  <AgenticTerminal
                    name={term.name}
                workspaceId={session.id}
                displayName={term.display_name}
                // The polled recap when one has arrived, the one the workspace
                    // state carried until then — so a pane opens with a sentence in
                    // its header rather than with a blank that fills in later.
                    recap={recaps[term.name]?.recap ?? term.recap}
                    recapDetail={recaps[term.name]?.recap_detail ?? term.recap_detail}
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
                // The backend's reading of what this agent is doing. Only a
                // pane that has had to give up its terminal for a card reads
                // them — see PaneTooNarrowCard — but they are cheap and already
                // in the state this component polls.
                activity={term.activity}
                activitySince={term.activity_since}
                worked={term.worked}
                // Only the panes that are NOT on the default login carry a
                // badge. Labelling every pane "Default Claude Code login" would
                    // be noise for the many; labelling the odd one out is the whole
                    // signal for the few running two seats at once.
                    accountLabel={
                      term.account && !term.account.endsWith(":default") ? term.account_label : null
                    }
                    promptCount={term.prompts_sent}
                    appearance={appearance}
                    fontSize={fontSize}
                    geometryReady={gridMeasured}
                    focused={target === term.name}
                    active={!chatView || onStage}
                    maximized={isMaximized}
                    layoutBusy={layoutBusy}
                    splitDisabled={atLimit || busy || working}
                agents={agents}
                onFocus={() => {
                  if (takesPrompts(term)) setTarget(term.name);
                }}
                onStatus={(status, detail) => setStatus(term.name, status, detail)}
                onToggleMaximize={() => {
                  // On the chat stage the pane already fills the surface, so
                  // "maximize" honestly means "give me the full-width grid
                  // version of this pane".
                  if (chatView) {
                        setViewMode("grid");
                        setMaximized(term.name);
                      } else {
                        setMaximized((current) => (current === term.name ? null : term.name));
                      }
                    }}
                    onSplit={(direction, agent) => void split(term.name, direction, agent)}
                onRename={(next) => renamePane(term, next)}
                onClose={() => setPendingClose(term.name)}
                onAttachError={(message) => pushToast("error", message)}
                // Picked up by its header, put down on another pane. Undefined
                // rather than a disabled flag when rearranging is off, so the
                    // header goes back to being a plain header — no grab cursor
                    // promising a gesture that would do nothing.
                    onArrangeStart={
                      canArrange ? (event) => arrange.start(term.name, event) : undefined
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
                      data-zone={arrange.hover?.target === term.name ? arrange.hover.zone : ""}
                      className="pointer-events-none absolute inset-0 z-30 rounded-lg border-2 border-dashed border-primary/30"
                    >
                      {arrange.hover?.target === term.name && (
                        <>
                          <div
                            className={cn(
                              "absolute rounded-md bg-primary/25 ring-2 ring-primary",
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
                          ? t("agentic_grid.selection.deselect_terminal").replace("{0}", term.name)
                          : t("agentic_grid.selection.select_terminal").replace("{0}", term.name)
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
        {!chatView &&
          maximized === null &&
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
            {/* `relative` so the picker hangs under the button rather than off
                the grid canvas. */}
            <div className="relative">
              <button
                type="button"
                data-testid="empty-workspace-new-terminal"
                className="btn-primary"
                disabled={busy || working}
                aria-expanded={offersChoice ? picking === "empty" : undefined}
                onClick={() => openTerminal("empty")}
              >
                <Plus className="h-4 w-4" />
                Open a terminal
              </button>
              {picking === "empty" && (
                <AgentPickerMenu
                  title="Open a terminal — what?"
                  ariaLabel="What should run in the new terminal?"
                  agents={agents ?? []}
                  testId="empty-workspace-agent-menu"
                  itemTestId={(agent) => `empty-workspace-new-${agent}`}
                  className="left-1/2 top-full mt-1 -translate-x-1/2"
                  onDismiss={() => setPicking(null)}
                  onPick={(agent) => {
                    setPicking(null);
                    void split(null, "right", agent);
                  }}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {openedWorkspaceFile && (
        <WorkspaceFileViewer
          workspaceId={session.id}
          path={openedWorkspaceFile}
          onClose={closeWorkspaceFile}
          onOpenFile={openWorkspaceFile}
        />
      )}
      </div>

      {/* The explorer owns the right edge. This zero-width host is ALWAYS
          present, so opening or closing it changes only width and never the
          sibling identity of a live terminal or its PTY socket. */}
      <div
        data-testid="workspace-explorer-host"
        className={cn(
          "relative h-full shrink-0",
          !explorerPane.isResizing &&
            "transition-[width] duration-200 motion-reduce:transition-none",
        )}
        style={{ width: explorerOpen ? requestedExplorer : 0 }}
        aria-hidden={!explorerOpen}
      >
        {explorerOpen && (
          <>
            <div className="group absolute inset-y-0 left-0 z-20 flex -translate-x-1/2">
              <PaneResizer
                testId="workspace-explorer-resizer"
                orientation="vertical"
                active={explorerPane.isResizing}
                title={t("agentic_grid.explorer.resize")}
                onPointerDown={explorerPane.startResize}
                onDoubleClick={explorerPane.reset}
                // This is a start-edge grip: Left grows the explorer, Right
                // shrinks it, so the shared seam's delta is inverted here.
                onNudge={(delta) => explorerPane.nudge(-delta)}
                valueNow={requestedExplorer}
                valueMin={EXPLORER_MIN_PX}
                valueMax={explorerMax}
                controls="workspace-explorer"
                className="h-full"
              />
              <MoveHorizontal
                aria-hidden
                className={cn(
                  "pointer-events-none absolute left-1/2 top-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 rounded-full border border-primary/35 bg-background/90 p-0.5 text-primary shadow-sm backdrop-blur transition-opacity",
                  explorerPane.isResizing
                    ? "opacity-100"
                    : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100",
                )}
              />
            </div>
            <div className="h-full overflow-hidden">
              <WorkspaceExplorer
                workspaceId={session.id}
                rootName={project.name}
                rootPath={session.folder}
                onClose={() => setExplorerOpen(false)}
                onOpenFile={openWorkspaceFile}
              />
            </div>
          </>
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

      {composerCollapsed && (
        <div
          data-testid="agentic-composer-collapsed"
          style={{ height: COMPOSER_COLLAPSED_PX }}
          className="flex shrink-0 items-center justify-between gap-2 px-3"
        >
          <span className="truncate text-[11px] text-muted-foreground/70">
            {target
              ? `Say it out loud and ${target} gets it.`
              : "Say it out loud and the agents get it."}
          </span>
          <button
            type="button"
            data-testid="agentic-composer-reopen"
            onClick={() => composer.resize(COMPOSER_DEFAULT_PX)}
            className="flex shrink-0 items-center gap-1.5 rounded-control px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            <ChevronUp className="h-3.5 w-3.5" />
            Write instead
          </button>
        </div>
      )}
      <div
        data-testid={composerCollapsed ? undefined : "agentic-composer"}
        aria-hidden={composerCollapsed || undefined}
        hidden={composerCollapsed}
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
            /*
             * The height the seam dragged belongs to THIS element; the padding
             * that insets the writing surface from the window edge belongs to
             * it too, so that the surface inside can be `h-full` and the bar
             * still occupies exactly the pixels the seam promised. Putting the
             * inset on the surface as a margin instead would have made the bar
             * 16 px taller than its own stated height — silently, and at the
             * expense of the panes above it.
             */
            "relative shrink-0 flex-col p-2",
            composerCollapsed ? "hidden" : "flex",
          )}
        >
          <div
            className={cn(
              /*
               * ONE surface, and the reason it is one is the reason this bar
               * used to look assembled: a target row, a bordered textarea and a
               * pill button, each with its own edge and its own radius, stacked
               * in a padded strip. Four frames, none of which was the edge of
               * the thing. The border, the radius and the focus treatment now
               * belong to this element alone; everything inside it is bare.
               */
              "relative flex h-full min-h-0 flex-col overflow-hidden rounded-surface border transition-colors",
              "border-border/70 bg-card/40 focus-within:border-primary/40",
              dragging && "border-primary/60 bg-primary/5",
            )}
          >
          {dragging && (
            <div
              data-testid="agentic-composer-dropzone"
              className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-surface bg-background/85 text-xs font-medium text-primary"
            >
              Drop a screenshot or document — {target || "the agent"} gets what is in it
            </div>
          )}
          {/*
            Which agent hears this — a row of tabs across the head of the
            surface rather than a "Send to" label followed by loose chips.
            The label was answering a question the chips already answer, and
            spending a line of the bar to do it; a selected tab is marked by a
            rule under it, which is the one place in this view where yellow
            still means "this is the choice you made" without shouting it.

            Agent panes only. A plain terminal is a shell prompt — it is typed
            into by hand, so listing it here would offer a target that refuses
            every instruction sent to it.
          */}
          {/* One line that scrolls sideways, never two that wrap: a bar whose
              head silently grows a second row takes that row off the panes
              above it, and it did so at exactly the moment a workspace got
              busy enough to need them. */}
          <div className="flex shrink-0 items-stretch gap-0.5 overflow-x-auto border-b border-border/60 px-1.5 scrollbar-jarvis">
            {session.terminals.filter(takesPrompts).map((term) => {
              const state = statusOf(term);
              const picked = target === term.name;
              return (
                <button
                  key={term.key}
                  type="button"
                  data-testid={`prompt-target-${term.name}`}
                  onClick={() => (chatView ? selectChatPane(term.name, true) : setTarget(term.name))}
                  aria-pressed={picked}
                  className={cn(
                    "flex shrink-0 items-center gap-1.5 border-b-2 px-2.5 py-1.5 text-xs transition-colors",
                    picked
                      ? "border-primary font-medium text-foreground"
                      : "border-transparent text-muted-foreground hover:text-foreground",
                  )}
                >
                  <span>{term.name}</span>
                  <PaneActivityPill
                    status={state.status}
                    detail={state.detail}
                    {...activityOf(term)}
                  />
                </button>
              );
            })}
          </div>
          {(attachments.length > 0 || analyzing > 0) && (
            <div className="shrink-0 px-2 pt-2">
              <AttachmentStrip
                attachments={attachments}
                analyzing={analyzing}
                onRemove={dropAttachment}
              />
            </div>
          )}
          {composeBeat && (
            <div
              data-testid="agentic-compose-progress"
              className="flex shrink-0 items-center gap-2 px-3 pt-2 text-[11px] text-muted-foreground"
            >
              <Loader2 className="h-3 w-3 shrink-0 animate-spin text-primary" />
              <span className="truncate">{composeBeat.message}</span>
            </div>
          )}
          {/*
            The input takes whatever the seam left over, which is the point of
            dragging it: pull the bar up and you get a real writing surface, not a
            two-line box with empty space under it. Its own resize grip is gone —
            two ways to change one height fight each other.
          */}
        {/*
          No standing "you can also say this out loud" note under the input.
          It was a tip printed permanently into the writing surface, and a tip
          that cannot be dismissed is read once and then occupies the bar
          forever — the collapsed strip already says the same thing in the one
          state where it is news (see `agentic-composer-collapsed`).
        */}
        <PromptEditor
          target={target}
          sending={sending}
          seed={promptSeed}
          onSend={send}
          onAttach={(files) => void attach({ paths: [], files })}
        />
        </div>
      </div>

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
          {/* Exchanging two panes is a real move, just not the one dragging
              means — so it is offered here, where someone mid-drag can see it,
              rather than left to be discovered by accident. */}
          {arrange.hover !== null && !arrange.swapping && (
            <span
              data-testid="agentic-arrange-swap-hint"
              className="font-normal text-muted-foreground/70"
            >
              {t("agentic_grid.arrange.swap_hint")}
            </span>
          )}
        </div>
      )}
      {railArrange.held !== null && railArrange.point !== null && (
        <div
          data-testid="chat-rail-arrange-ghost"
          className="pointer-events-none fixed z-50 flex items-center gap-1.5 rounded-lg border border-primary/60 bg-card px-2.5 py-1.5 text-xs font-semibold shadow-xl"
          style={{ left: railArrange.point.x + 14, top: railArrange.point.y + 14 }}
        >
          <GripVertical className="h-3.5 w-3.5 text-primary" />
          {railArrange.held}
          <span className="font-normal text-muted-foreground">
            {railArrange.hover === null
              ? t("agentic_grid.arrange.carrying")
              : t("agentic_grid.arrange.swap").replace(
                  "{0}",
                  railArrange.hover.target,
                )}
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * Terminal appearance, behind one quiet toolbar button.
 *
 * Theme is a set-and-forget preference, unlike text size: the latter is an
 * accessibility control and remains visible beside this menu.
 */
function ViewMenu({
  appearance,
  onAppearance,
}: {
  appearance: TerminalAppearance;
  onAppearance: (next: TerminalAppearance) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <div className="relative shrink-0">
      <button
        type="button"
        aria-label={t("agentic_grid.display.appearance")}
        aria-expanded={open}
        data-testid="agentic-view-menu"
        title={t("agentic_grid.display.appearance")}
        onClick={() => setOpen((value) => !value)}
        className={cn(TOOLBAR_BTN, open && "bg-secondary text-foreground")}
      >
        <SlidersHorizontal className="h-4 w-4" />
      </button>
      {open && (
        <>
          {/* Same dismiss pattern as the pane split menu: anywhere else closes. */}
          <div className="fixed inset-0 z-40" onMouseDown={() => setOpen(false)} />
          <div
            data-testid="agentic-view-menu-panel"
            className="absolute right-0 top-full z-50 mt-1 w-60 rounded-xl border border-border bg-card p-3 shadow-xl"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">
                {t("agentic_grid.display.appearance")}
              </span>
              <div className="flex items-center gap-0.5 rounded-md border border-border p-0.5">
                <button
                  type="button"
                  aria-label={t("agentic_grid.display.light")}
                  aria-pressed={appearance === "light"}
                  onClick={() => onAppearance("light")}
                  className={cn(
                    "flex h-6 w-6 items-center justify-center rounded transition-colors",
                    appearance === "light"
                      ? "bg-primary/20 text-primary"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <Sun className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  aria-label={t("agentic_grid.display.dark")}
                  aria-pressed={appearance === "dark"}
                  onClick={() => onAppearance("dark")}
                  className={cn(
                    "flex h-6 w-6 items-center justify-center rounded transition-colors",
                    appearance === "dark"
                      ? "bg-primary/20 text-primary"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <Moon className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Keep every workspace tab visible in the desktop's narrowest supported window.
 *
 * At normal widths this wrapper is `display: contents`, so the toolbar keeps
 * its exact established order and spacing. Below 900 px the controls stay
 * mounted but move behind one overflow button; live status controls therefore
 * keep their state and polling while the tab bar receives its reserved width.
 */
function ToolbarOverflow({ children }: { children: React.ReactNode }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeAndRestoreFocus = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };
  return (
    <div className="contents max-[900px]:relative max-[900px]:block max-[900px]:shrink-0">
      <button
        ref={triggerRef}
        type="button"
        data-testid="agentic-toolbar-overflow"
        aria-expanded={open}
        aria-controls="agentic-toolbar-overflow-panel"
        aria-label={t("agentic_grid.toolbar.more_controls")}
        title={t("agentic_grid.toolbar.more_controls")}
        onClick={() => setOpen((current) => !current)}
        className={cn(TOOLBAR_BTN, "hidden max-[900px]:flex")}
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {open && (
        <button
          type="button"
          tabIndex={-1}
          aria-label={t("agentic_grid.toolbar.close_controls")}
          className="fixed inset-0 z-40 hidden cursor-default max-[900px]:block"
          onClick={() => setOpen(false)}
        />
      )}
      <div
        id="agentic-toolbar-overflow-panel"
        data-testid="agentic-toolbar-overflow-panel"
        onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          event.preventDefault();
          closeAndRestoreFocus();
        }}
        className={cn(
          "contents",
          open
            ? "max-[900px]:absolute max-[900px]:right-0 max-[900px]:top-full max-[900px]:z-50 max-[900px]:mt-1 max-[900px]:flex max-[900px]:w-[min(22rem,calc(100vw-1rem))] max-[900px]:flex-wrap max-[900px]:items-center max-[900px]:justify-end max-[900px]:gap-1 max-[900px]:rounded-xl max-[900px]:border max-[900px]:border-border max-[900px]:bg-card max-[900px]:p-2 max-[900px]:shadow-xl"
            : "max-[900px]:hidden",
        )}
      >
        {children}
      </div>
    </div>
  );
}

/** Always-visible terminal text sizing for the active workspace. */
/**
 * The toolbar's voice button — summons the floating voice bubble.
 *
 * Its own component so only IT re-renders on voice-state changes: it
 * subscribes to `voiceState` for the gold pulse, and re-rendering the whole
 * grid a few times per spoken turn to animate one glyph would be the tail
 * wagging a very large dog.
 */
function WorkspaceVoiceButton({
  open,
  onToggle,
}: {
  open: boolean;
  onToggle: () => void;
}) {
  const t = useT();
  const voiceState = (useEventStore((s) => s.voiceState) ?? "idle") as VoiceState;
  const assistantName =
    (useEventStore((s) => s.assistantName) ?? "").trim() ||
    t("agentic_grid.voice_bubble.assistant_fallback");
  const active = isVoiceActive(voiceState);
  return (
    <button
      type="button"
      data-testid="agentic-voice-toggle"
      aria-pressed={open}
      onClick={onToggle}
      title={
        open
          ? t("agentic_grid.voice_bubble.button_close")
          : t("agentic_grid.voice_bubble.button_open").replace("{0}", assistantName)
      }
      className={cn(TOOLBAR_BTN, open && TOOLBAR_BTN_ON)}
    >
      <AudioLines
        className={cn(
          "h-4 w-4 shrink-0",
          active && "animate-pulse text-primary motion-reduce:animate-none",
        )}
      />
    </button>
  );
}

function TerminalFontSizeControl({
  fontSize,
  onFontSize,
}: {
  fontSize: number;
  onFontSize: (next: number) => void;
}) {
  const t = useT();
  const smaller = fontSize <= FONT_MIN;
  const larger = fontSize >= FONT_MAX;
  // The keyboard is how this actually gets used (see ./terminalZoom); the
  // stepper's job is to make the chord discoverable. Symbols rather than a
  // translated sentence, because "⌘ + − 0" reads the same in every locale.
  const isMac = /mac|iphone|ipad/i.test(navigator.userAgent);
  const chordHint = isMac ? "⌘ + / − / 0" : "Ctrl + / − / 0";

  return (
    <div
      role="group"
      aria-label={t("agentic_grid.display.text_size")}
      data-testid="agentic-font-size-control"
      aria-keyshortcuts={
        isMac ? "Meta+Plus Meta+Minus Meta+0" : "Control+Plus Control+Minus Control+0"
      }
      title={`${t("agentic_grid.display.text_size")} — ${chordHint}`}
      className="flex h-7 shrink-0 items-center rounded-md border border-border/70 bg-background/30"
    >
      <Type aria-hidden="true" className="mx-1 h-3.5 w-3.5 text-muted-foreground" />
      <button
        type="button"
        aria-label={t("agentic_grid.display.smaller")}
        disabled={smaller}
        onClick={() => onFontSize(Math.max(FONT_MIN, fontSize - 1))}
        className="flex h-7 w-6 items-center justify-center text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-35"
      >
        <Minus className="h-3.5 w-3.5" />
      </button>
      <span
        aria-live="polite"
        data-testid="agentic-font-size-value"
        className="w-7 text-center font-mono text-[11px] tabular-nums text-foreground"
      >
        {fontSize}
      </span>
      <button
        type="button"
        aria-label={t("agentic_grid.display.larger")}
        disabled={larger}
        onClick={() => onFontSize(Math.min(FONT_MAX, fontSize + 1))}
        className="flex h-7 w-6 items-center justify-center rounded-r-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-35"
      >
        <Plus className="h-3.5 w-3.5" />
      </button>
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
          This stops all {terminalCount} {agentLabel} and closes every terminal session. Anything
          already written to disk stays.
        </Dialog.Description>
        <div className="mt-5 flex items-center justify-end gap-2">
          <Dialog.Close asChild>
            <button type="button" className="btn-ghost" autoFocus disabled={busy}>
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
          The coding agent running in this terminal is stopped and its session is gone. Anything it
          already wrote to disk stays.
        </p>
        <div className="mt-5 flex items-center justify-end gap-2">
          <button type="button" className="btn-ghost" autoFocus disabled={busy} onClick={onCancel}>
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

/**
 * One of the rail's standing actions — a word, an icon, a whole row to hit.
 *
 * Rows rather than a toolbar of glyphs: these are the three things a person
 * arrives wanting to do, and each has to be able to say which one it is
 * without being hovered first.
 */
function RailAction({
  icon: Icon,
  label,
  hint,
  onClick,
  disabled,
  testId,
}: {
  icon: LucideIcon;
  label: string;
  hint?: string;
  onClick: () => void;
  disabled?: boolean;
  testId?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      disabled={disabled}
      title={hint ?? label}
      className={cn(
        "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs font-medium",
        "text-foreground/85 transition-colors hover:bg-foreground/[0.055]",
        "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent",
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
      {label}
    </button>
  );
}

/** A section heading in the rail. Quiet enough to be scanned past. */
function RailBand({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-2 pb-1 pt-3 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60">
      {children}
    </div>
  );
}

/**
 * A stable colour for a folder, so the same project reads the same everywhere.
 *
 * Derived from the path rather than stored, which means it needs no setting and
 * survives a lost store. The palette is fixed rather than a free hue rotation:
 * arbitrary HSL produces colours that vanish against one of the two themes.
 */
const FOLDER_COLORS = [
  "#e7c46e",
  "#7dd3fc",
  "#a5b4fc",
  "#86efac",
  "#fca5a5",
  "#f0abfc",
  "#fdba74",
  "#5eead4",
] as const;

function folderColor(key: string): string {
  let sum = 0;
  for (const char of key) sum = (sum + char.charCodeAt(0)) % 4096;
  return FOLDER_COLORS[sum % FOLDER_COLORS.length];
}
