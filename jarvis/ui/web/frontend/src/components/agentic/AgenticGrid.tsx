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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  ArrowUp,
  Brain,
  Check,
  ChevronUp,
  FileText,
  FolderGit2,
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
  paneGrid,
} from "./layout";
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
import {
  addTerminal,
  attachToTerminal,
  closeTerminal,
  closeTerminals,
  composePrompt,
  fetchTerminalRecaps,
  promptTerminal,
  type ComposedPreview,
  type DropAttachment,
  type IdeAccountState,
  type IdeState,
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
}

const FONT_MIN = 10;
const FONT_MAX = 20;

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
 */
const COMPOSER_DEFAULT_PX = 176;
/** Height of the collapsed strip: its reopen button and nothing else. */
const COMPOSER_COLLAPSED_PX = 34;
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
   */
  useEffect(() => {
    let cancelled = false;
    const pull = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
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
    // A window coming back to the foreground has skipped every tick it spent
    // hidden, so its headers are as old as the time away. Catch up at once
    // rather than showing minute-old work for another five seconds.
    const onVisible = () => {
      if (!document.hidden) void pull();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [session.id]);

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
  useEffect(() => {
    const node = gridRef.current;
    if (!node) return;
    const fallback = () =>
      Math.max(0, node.clientWidth - GRID_HORIZONTAL_PADDING_PX);
    setGridWidth(fallback());
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? fallback();
      // Round to a step so a one-pixel drift cannot churn the layout (and with
      // it every pane's resize) on window animations.
      setGridWidth(Math.round(width / 16) * 16);
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
    storageKey: "jarvis.agenticIde.composerHeight.v1",
    defaultSize: COMPOSER_DEFAULT_PX,
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

  const perBand = useMemo(() => bandCapacityFor(gridWidth), [gridWidth]);
  const grid = useMemo(
    () => paneGrid(session.terminals, perBand),
    [session.terminals, perBand],
  );

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

  const closeOne = async (name: string) => {
    setWorking(true);
    try {
      const next = await closeTerminal(name);
      setPendingClose(null);
      if (maximized === name) setMaximized(null);
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
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2.5">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <FolderGit2 className="h-4 w-4 shrink-0 text-primary" />
          <span className="font-display text-sm font-semibold">{project.name}</span>
          {branchLabel && (
            <span className="chip shrink-0 text-[10px] uppercase tracking-wide">
              {branchLabel}
            </span>
          )}
          <code className="hidden min-w-0 truncate font-mono text-[11px] text-muted-foreground lg:block">
            {session.folder}
          </code>
        </div>

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
            "flex shrink-0 items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
            focusMode
              ? "border-primary/60 bg-primary/15 text-primary"
              : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground",
          )}
        >
          <Brain className="h-4 w-4" />
          {focusMode ? "Coding mode ON" : "Coding mode OFF"}
        </button>

        <div className="flex shrink-0 items-center gap-1 rounded-lg border border-border p-0.5">
          <button
            type="button"
            aria-label="Light terminals"
            aria-pressed={appearance === "light"}
            onClick={() => setAppearance("light")}
            className={cn(
              "flex h-7 w-7 items-center justify-center rounded-md transition-colors",
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
              "flex h-7 w-7 items-center justify-center rounded-md transition-colors",
              appearance === "dark" ? "bg-primary/20 text-primary" : "text-muted-foreground",
            )}
          >
            <Moon className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="flex shrink-0 items-center gap-1 rounded-lg border border-border p-0.5">
          <button
            type="button"
            aria-label="Smaller text"
            onClick={() => setFontSize(Math.max(FONT_MIN, fontSize - 1))}
            className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:text-foreground"
          >
            <Minus className="h-3.5 w-3.5" />
          </button>
          <Type className="h-3.5 w-3.5 text-muted-foreground" />
          <button
            type="button"
            aria-label="Larger text"
            onClick={() => setFontSize(Math.min(FONT_MAX, fontSize + 1))}
            className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:text-foreground"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>

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
            "flex shrink-0 items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
            selectionMode
              ? "border-primary/60 bg-primary/15 text-primary"
              : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground",
          )}
        >
          <ListChecks className="h-4 w-4" />
          {selectionMode
            ? t("agentic_grid.selection.finish")
            : t("agentic_grid.selection.start")}
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
              className="btn-ghost shrink-0"
              disabled={busy}
              title="Close the workspace and stop every agent in it"
            >
              <Power className="h-4 w-4" />
              Close
            </button>
          </Dialog.Trigger>
          <ConfirmWorkspaceClose
            terminalCount={session.terminals.length}
            busy={busy}
            onConfirm={onClose}
          />
        </Dialog.Root>
      </div>

      {/* ------------------------------------------------------------- grid */}
      {/*
        ONE grid, and every pane is a direct child of it — placed by the
        coordinates `paneGrid` computed rather than by where it sits in a tree
        of row and column elements.

        That is not a style choice. Every pane must stay MOUNTED for its whole
        life, because unmounting one tears down its WebSocket and kills the
        coding agent behind it. React re-parents children whenever the element
        tree changes shape, so nesting a container per column would remount
        panes on every split, close, and wrap. With one flat container the
        layout only ever changes numbers, and nothing moves in the DOM.

        The same reasoning covers maximizing: the other panes are HIDDEN with
        CSS, never removed, and the maximized one is told to span the whole
        grid instead of keeping its narrow cell.
      */}
      <div
        ref={gridRef}
        data-testid="agentic-grid"
        className={cn(
          "grid min-h-0 flex-1 gap-3 p-3",
          // Scrolls only once the panes would be squeezed below a readable
          // height. With a workspace that fits, this is `overflow-hidden`
          // behaviour and nothing moves.
          maximized !== null ? "overflow-hidden" : "overflow-y-auto scrollbar-jarvis",
        )}
        style={{
          // While one pane is maximized the grid IS that pane: a single track
          // filling exactly what the window shows. Spanning the full template
          // instead was wrong in a workspace large enough to scroll — the
          // tracks then add up to more than the window, so the maximized pane
          // was taller than the visible area and the terminal fitted itself to
          // rows nobody could see, putting the CLI's prompt box below the clip.
          // A grid that had been scrolled also kept its offset, so the pane
          // came up part-way past its own header.
          gridTemplateColumns:
            maximized !== null
              ? "minmax(0, 1fr)"
              : `repeat(${Math.max(1, grid.columns)}, minmax(0, 1fr))`,
          // A floor on row height, not a free 1fr. Panes used to share the
          // window height in equal parts however many there were, so a large
          // workspace ended up with three text rows per pane — readable width,
          // unusable height. Below the floor the grid grows and scrolls instead.
          gridTemplateRows:
            maximized !== null
              ? "minmax(0, 1fr)"
              : `repeat(${Math.max(1, grid.rows)}, minmax(${MIN_PANE_HEIGHT_PX}px, 1fr))`,
        }}
      >
        {session.terminals.map((term, index) => {
          const place = grid.placements[index];
          const isMaximized = maximized === term.name;
          return (
            <div
              key={term.key}
              data-testid={`pane-cell-${term.name}`}
              className={cn(
                "relative min-h-0 min-w-0 rounded-xl",
                selectedTerminals.has(term.name) &&
                  "ring-2 ring-primary ring-offset-2 ring-offset-background",
                maximized !== null && !isMaximized && "hidden",
              )}
              style={
                isMaximized
                  ? { gridColumn: "1 / -1", gridRow: "1 / -1" }
                  : {
                      gridColumn: place?.column ?? 1,
                      gridRow: `${place?.row ?? 1} / span ${place?.rowSpan ?? 1}`,
                    }
              }
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
                restartToken={restartTokens[term.name] ?? 0}
                onRestart={() => restartPane(term.name)}
              />
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
                    "absolute inset-0 z-20 cursor-pointer rounded-xl transition-colors",
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
        {session.terminals.length === 0 && (
          <div
            className="flex flex-col items-center justify-center gap-3 text-sm text-muted-foreground"
            style={{ gridColumn: "1 / -1", gridRow: "1 / -1" }}
          >
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
        there — and that line is the control. Double-click restores 176 px.
      */}
      <PaneResizer
        orientation="horizontal"
        onPointerDown={composer.startResize}
        onDoubleClick={composer.reset}
        onNudge={composer.nudge}
        active={composer.isResizing}
        title="Drag to resize the prompt bar — double-click to reset, drag all the way down to collapse"
      />

      {composerCollapsed ? (
        <div
          data-testid="agentic-composer-collapsed"
          style={{ height: COMPOSER_COLLAPSED_PX }}
          className="flex shrink-0 items-center justify-between px-4"
        >
          <span className="truncate text-[11px] text-muted-foreground">
            Prompt bar hidden — {target || "no terminal"} still receives what you
            say out loud.
          </span>
          <button
            type="button"
            data-testid="agentic-composer-reopen"
            onClick={composer.reset}
            className="flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <ChevronUp className="h-3.5 w-3.5" />
            Show prompt bar
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
            "relative flex shrink-0 flex-col overflow-hidden px-4 py-3 transition-colors",
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
          <div className="mb-2 flex max-h-24 shrink-0 flex-wrap items-center gap-2 overflow-y-auto scrollbar-jarvis">
            <span className="text-[11px] uppercase tracking-wider text-muted-foreground">
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
                    "flex items-center gap-2 rounded-lg border px-2.5 py-1 text-xs transition-colors",
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
