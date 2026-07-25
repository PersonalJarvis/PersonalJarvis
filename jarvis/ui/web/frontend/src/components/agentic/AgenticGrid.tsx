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
import { useCallback, useMemo, useState } from "react";
import {
  ArrowUp,
  Brain,
  FolderGit2,
  Minus,
  Moon,
  Plus,
  Power,
  Sun,
  Type,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import {
  AgenticTerminal,
  PaneStatusPill,
  type PaneStatus,
} from "./AgenticTerminal";
import type { TerminalAppearance } from "./terminalThemes";
import { paneColumns, paneLines, paneRows } from "./layout";
import {
  addTerminal,
  closeTerminal,
  promptTerminal,
  type SessionState,
} from "@/lib/agenticIdeApi";

interface AgenticGridProps {
  session: SessionState;
  focusMode: boolean;
  onToggleFocus: (enabled: boolean) => void;
  onClose: () => void;
  busy?: boolean;
  /** Hard cap on panes, so the split buttons can disable themselves. */
  maxTerminals?: number;
  /** Adding or closing a pane changes the workspace — the owner re-reads it. */
  onSessionChanged?: (session: SessionState) => void;
}

const FONT_MIN = 10;
const FONT_MAX = 20;

export function AgenticGrid({
  session,
  focusMode,
  onToggleFocus,
  onClose,
  busy = false,
  maxTerminals = 12,
  onSessionChanged,
}: AgenticGridProps) {
  const pushToast = useEventStore((s) => s.pushToast);
  const [appearance, setAppearance] = useState<TerminalAppearance>("light");
  const [fontSize, setFontSize] = useState(13);
  const [target, setTarget] = useState(session.terminals[0]?.name ?? "");
  const [statuses, setStatuses] = useState<Record<string, { status: PaneStatus; detail?: string }>>({});
  const [prompt, setPrompt] = useState("");
  const [sending, setSending] = useState(false);

  const [maximized, setMaximized] = useState<string | null>(null);
  const [pendingClose, setPendingClose] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  // Group panes into their rows. The backend keeps `row` packed and the list in
  // render order, so this is a straight fold rather than a sort.
  const rows = useMemo(() => paneRows(session.terminals), [session.terminals]);

  const atLimit = session.terminals.length >= maxTerminals;

  const split = async (anchor: string | null, direction: "right" | "down") => {
    setWorking(true);
    try {
      const next = await addTerminal({ anchor: anchor ?? undefined, direction });
      onSessionChanged?.(next);
      // A fresh pane should receive the next prompt — that is why it was opened.
      const known = new Set(session.terminals.map((t) => t.name));
      const added = next.terminals.find((t) => !known.has(t.name));
      if (added) setTarget(added.name);
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
      if (target === name) setTarget(next.terminals[0]?.name ?? "");
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setWorking(false);
    }
  };

  const setStatus = useCallback((name: string, status: PaneStatus, detail?: string) => {
    setStatuses((prev) => ({ ...prev, [name]: { status, detail } }));
  }, []);

  const send = async () => {
    const text = prompt.trim();
    if (!text || !target) return;
    setSending(true);
    try {
      const result = await promptTerminal(target, text);
      setPrompt("");
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

  const project = session.project;
  const branchLabel = useMemo(
    () => (project.is_repo && project.branch ? `on ${project.branch}` : ""),
    [project],
  );

  return (
    <div className="relative flex h-full flex-col">
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
            onClick={() => setFontSize((n) => Math.max(FONT_MIN, n - 1))}
            className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:text-foreground"
          >
            <Minus className="h-3.5 w-3.5" />
          </button>
          <Type className="h-3.5 w-3.5 text-muted-foreground" />
          <button
            type="button"
            aria-label="Larger text"
            onClick={() => setFontSize((n) => Math.min(FONT_MAX, n + 1))}
            className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:text-foreground"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>

        <button
          type="button"
          className="btn-ghost shrink-0"
          onClick={onClose}
          disabled={busy}
          title="Close the workspace and stop every agent in it"
        >
          <Power className="h-4 w-4" />
          Close
        </button>
      </div>

      {/* ------------------------------------------------------------- grid */}
      {/*
        Rows, not one uniform grid: "split right" adds a pane to an existing row
        and "split down" opens a new one, so rows can differ in width. Every pane
        stays MOUNTED at all times — including while another one is maximized —
        because unmounting it would tear down its WebSocket and kill the agent
        behind it. Maximizing therefore hides the others with CSS instead.

        Each row is a CSS grid whose width comes from `paneColumns`, so a row
        wider than that wraps onto a second line (12 panes → 6 above, 6 below)
        instead of squeezing every pane down to an unreadable sliver. Wrapping
        inside ONE grid rather than into extra row elements is what keeps the
        panes mounted through the wrap — moving a pane to a different parent
        would remount it, and remounting kills its agent.
      */}
      <div className="flex flex-1 flex-col gap-3 overflow-hidden p-3">
        {rows.map((rowTerminals, rowIndex) => {
          const rowHidden =
            maximized !== null && !rowTerminals.some((t) => t.name === maximized);
          // While a pane is maximized the row collapses to a single column, so
          // the one visible pane gets the full width instead of keeping the
          // narrow slot it had in the grid.
          const columns = maximized !== null ? 1 : paneColumns(rowTerminals.length);
          return (
            <div
              key={rowIndex}
              className={cn("grid min-h-0 gap-3", rowHidden && "hidden")}
              style={{
                gridTemplateColumns: `repeat(${Math.max(1, columns)}, minmax(0, 1fr))`,
                gridAutoRows: "minmax(0, 1fr)",
                // A row that wraps onto two lines needs twice the height of a
                // single-line row, or its panes end up half as tall.
                flexGrow: maximized !== null ? 1 : paneLines(rowTerminals.length),
                flexBasis: 0,
              }}
            >
              {rowTerminals.map((term) => (
                <div
                  key={term.key}
                  className={cn(
                    "min-w-0",
                    maximized !== null && maximized !== term.name && "hidden",
                  )}
                >
                  <AgenticTerminal
                    name={term.name}
                    displayName={term.display_name}
                    appearance={appearance}
                    fontSize={fontSize}
                    focused={target === term.name}
                    maximized={maximized === term.name}
                    splitDisabled={atLimit || busy || working}
                    onFocus={() => setTarget(term.name)}
                    onStatus={(status, detail) => setStatus(term.name, status, detail)}
                    onToggleMaximize={() =>
                      setMaximized((current) => (current === term.name ? null : term.name))
                    }
                    onSplitRight={() => void split(term.name, "right")}
                    onSplitDown={() => void split(term.name, "down")}
                    onClose={() => setPendingClose(term.name)}
                  />
                </div>
              ))}
            </div>
          );
        })}
        {session.terminals.length === 0 && (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
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

      {/* -------------------------------------------------------- prompt bar */}
      <div className="border-t border-border px-4 py-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="text-[11px] uppercase tracking-wider text-muted-foreground">
            Send to
          </span>
          {session.terminals.map((term) => {
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
        <div className="flex items-end gap-2">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            rows={2}
            placeholder={
              target
                ? `Type an instruction for ${target} — Enter sends, Shift+Enter adds a line`
                : "Pick a terminal first"
            }
            className="max-h-32 min-h-[52px] flex-1 resize-y rounded-lg border border-border bg-background/60 px-3 py-2 text-sm outline-none focus:border-primary/50"
          />
          <button
            type="button"
            className="btn-primary h-[52px] shrink-0"
            disabled={sending || !prompt.trim() || !target}
            onClick={() => void send()}
          >
            <ArrowUp className="h-4 w-4" />
            Send
          </button>
        </div>
        <p className="mt-2 text-[11px] text-muted-foreground">
          Anything you can type here, you can also say out loud — for example
          “what is {session.terminals[0]?.name ?? "Mika"} doing?” or “tell{" "}
          {session.terminals[0]?.name ?? "Mika"} to run the tests”.
        </p>
      </div>
    </div>
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
